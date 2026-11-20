"""The indexing job against fake OpenSearch, Bedrock and S3 clients. No test calls AWS."""

import io
import json
import math

import pytest
from botocore.exceptions import ClientError

from aria_catalog.definition import (
    DIMENSION,
    EMBEDDING_FIELD,
    definition_to_dict,
    example_products,
    index_body,
    load_definition,
    parse_definition,
    pipeline_body,
)
from aria_indexer import job as job_module
from aria_indexer.__main__ import describe, main
from aria_indexer.job import BATCH, JobError, parse_catalog, validate

EXAMPLE = load_definition()
PRODUCTS = example_products()
SECRET = "SECRET-VALUE-DO-NOT-LOG"  # nosec B105 # a fake secret value; the tests prove it's never logged


# --- Fakes ---


class FakeIndices:
    def __init__(self, cluster):
        self.cluster = cluster

    def exists(self, index):
        self.cluster.calls.append(("HEAD", index))
        return index in self.cluster.indexes or index in self.cluster.aliases

    def get(self, index):
        self.cluster.calls.append(("GET", index))
        if index in self.cluster.aliases:
            return {name: {"aliases": {index: {}}} for name in self.cluster.aliases[index]}
        return {index: {"aliases": {}}}

    def create(self, index, body):
        self.cluster.calls.append(("PUT", index))
        self.cluster.indexes[index] = {"body": body, "docs": {}}

    def refresh(self, index):
        self.cluster.calls.append(("POST", f"{index}/_refresh"))

    def update_aliases(self, body):
        self.cluster.calls.append(("POST", "_aliases"))
        self.cluster.alias_actions.append(body["actions"])
        for action in body["actions"]:
            (kind, target), = action.items()
            members = self.cluster.aliases.setdefault(target["alias"], [])
            if kind == "remove":
                members.remove(target["index"])
            else:
                members.append(target["index"])


class FakePipelines:
    def __init__(self, cluster):
        self.cluster = cluster

    def put(self, id, body):
        self.cluster.calls.append(("PUT", f"_search/pipeline/{id}"))
        self.cluster.pipelines[id] = body


class FakeOpenSearch:
    """Enforces the strict mapping: a document with a field outside it fails, as on the domain."""

    def __init__(self, indexes=(), aliases=None, lose=0, bulk_error=None):
        self.indexes = {name: {"body": {}, "docs": {}} for name in indexes}
        self.aliases = dict(aliases or {})
        self.pipelines = {}
        self.calls = []
        self.alias_actions = []
        self.lose = lose
        self.bulk_error = bulk_error
        self.indices = FakeIndices(self)
        self.search_pipeline = FakePipelines(self)

    def bulk(self, body, index):
        self.calls.append(("POST", f"{index}/_bulk"))
        assert body.endswith("\n")
        lines = body.splitlines()
        mapping = self.indexes[index]["body"]["mappings"]["properties"]
        items = []
        for action, source in zip(lines[::2], lines[1::2]):
            doc_id = json.loads(action)["index"]["_id"]
            doc = json.loads(source)
            assert set(doc) <= set(mapping), set(doc) - set(mapping)
            assert len(doc[EMBEDDING_FIELD]) == DIMENSION
            if self.bulk_error:
                items.append({"index": {"_id": doc_id, "status": 400, "error": {
                    "type": self.bulk_error, "reason": f"failed to parse field [title] value {doc.get('title')}"}}})
                continue
            self.indexes[index]["docs"][doc_id] = doc
            items.append({"index": {"_id": doc_id, "status": 201}})
        return {"errors": any("error" in item["index"] for item in items), "items": items}

    def count(self, index):
        self.calls.append(("GET", f"{index}/_count"))
        return {"count": len(self.indexes[index]["docs"]) - self.lose}


def throttle(code="ThrottlingException"):
    return ClientError({"Error": {"Code": code, "Message": f"slow down {SECRET}"}}, "InvokeModel")


class FakeBedrock:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.requests = []

    def invoke_model(self, modelId, body, contentType, accept):
        request = json.loads(body)
        self.requests.append((modelId, request))
        if self.failures:
            raise self.failures.pop(0)
        vectors = [[0.5] * DIMENSION for _ in request["texts"]]
        return {"body": io.BytesIO(json.dumps({"embeddings": {"float": vectors}}).encode())}


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class Clients:
    """What main() gets instead of AWS clients. An attribute that is None fails if the job reaches for it."""

    def __init__(self, s3=None, opensearch=None, bedrock=None):
        self._s3, self._opensearch, self._bedrock = s3, opensearch, bedrock

    def _get(self, name):
        value = getattr(self, f"_{name}")
        if value is None:
            pytest.fail(f"the job used the {name} client")
        return value

    s3 = property(lambda self: self._get("s3"))
    opensearch = property(lambda self: self._get("opensearch"))
    bedrock = property(lambda self: self._get("bedrock"))


def as_json(products) -> bytes:
    return json.dumps(products, ensure_ascii=False).encode()


def as_jsonl(products) -> bytes:
    return "\n".join(json.dumps(p, ensure_ascii=False) for p in products).encode()


@pytest.fixture
def catalog_file(tmp_path):
    def write(raw: bytes, name="products.json"):
        path = tmp_path / name
        path.write_bytes(raw)
        return str(path)
    return write


def run(argv, capsys, **kwargs):
    code = main(argv, environ=kwargs.pop("environ", {}), sleep=kwargs.pop("sleep", lambda s: None), **kwargs)
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out  # one manifest line on stdout
    return code, json.loads(lines[0]), err


# --- Reading and validation ---


def test_reads_a_json_array_and_jsonl_alike():
    assert parse_catalog(as_json(PRODUCTS)) == ("json", PRODUCTS)
    assert parse_catalog(as_jsonl(PRODUCTS) + b"\n\n") == ("jsonl", PRODUCTS)
    assert parse_catalog(b"\xef\xbb\xbf" + as_json(PRODUCTS[:1]))[1] == PRODUCTS[:1]  # a UTF-8 BOM


def test_a_broken_json_array_stops_the_job_and_a_broken_jsonl_line_is_rejected():
    with pytest.raises(JobError, match="not a valid JSON array"):
        parse_catalog(b'[{"sku": "a"},')
    _, entries = parse_catalog(as_jsonl(PRODUCTS[:2]) + b'\n{"sku": ')
    result = validate(EXAMPLE, entries)
    assert (len(result.accepted), result.rejected) == (2, 1)
    assert result.reasons == {"the line is not valid JSON": 1}


def test_every_example_product_is_accepted_with_its_embedding_text():
    result = validate(EXAMPLE, PRODUCTS)
    assert (len(result.accepted), result.rejected) == (len(PRODUCTS), 0)
    assert result.texts[0] == f"{PRODUCTS[0]['title']}. {PRODUCTS[0]['description']}"


def test_rejections_are_counted_by_field_never_by_value():
    missing = {k: v for k, v in PRODUCTS[0].items() if k != "title"}
    wrong = {**PRODUCTS[1], "gender": SECRET}
    duplicate = {**PRODUCTS[2], "sku": PRODUCTS[3]["sku"]}
    result = validate(EXAMPLE, [missing, wrong, PRODUCTS[3], duplicate, "not an object"])
    assert len(result.accepted) == 1 and result.rejected == 4
    assert result.reasons == {
        "title: missing": 1,
        "gender: not one of the declared values": 1,
        "sku: duplicate ID": 1,
        "the product is not an object": 1,
    }
    assert SECRET not in json.dumps(result.reasons)


def test_blank_embedding_text_is_rejected():
    definition = parse_definition({"catalog": {**definition_to_dict(EXAMPLE)["catalog"], "embedding": {"text": "{description}"}}})
    result = validate(definition, [{**PRODUCTS[0], "description": "  "}])
    assert result.reasons == {"embedding text: blank": 1}


# --- The dry run ---


def test_a_dry_run_validates_and_estimates_without_any_client(capsys, catalog_file):
    code, manifest, err = run(["--catalog", catalog_file(as_json(PRODUCTS)), "--version", "v1"], capsys, clients=Clients())
    assert code == 0
    assert manifest["manifest"] == "aria.catalog-index.v1"
    assert (manifest["products"], manifest["accepted"], manifest["rejected"]) == (len(PRODUCTS), len(PRODUCTS), 0)
    assert manifest["index"] == "products-v1" and manifest["pipeline"] == "products-hybrid"
    assert manifest["embeddingCalls"] == math.ceil(len(PRODUCTS) / BATCH)
    assert manifest["vectorBytes"] == len(PRODUCTS) * DIMENSION * 4
    assert manifest["applied"] is False and manifest["promoted"] is False
    # The script passes the stack's replica count only at --apply, so a dry run doesn't guess one.
    assert manifest["replicas"] is None
    assert "dry run" in err


def test_an_applied_run_without_replicas_uses_one(capsys, catalog_file):
    opensearch = FakeOpenSearch()
    code, manifest, _ = run(["--catalog", catalog_file(as_json(PRODUCTS)), "--version", "v2", "--apply"], capsys,
                            clients=Clients(opensearch=opensearch, bedrock=FakeBedrock()))
    assert code == 0 and manifest["replicas"] == 1
    assert opensearch.indexes["products-v2"]["body"] == index_body(EXAMPLE, 1)


def test_the_catalog_is_read_from_s3(capsys):
    s3 = FakeS3({("aria-catalog", "catalog/v1/products.jsonl"): as_jsonl(PRODUCTS[:5])})
    code, manifest, _ = run(["--catalog", "s3://aria-catalog/catalog/v1/products.jsonl", "--version", "v1"], capsys,
                            clients=Clients(s3=s3))
    assert code == 0 and manifest["format"] == "jsonl" and manifest["accepted"] == 5
    assert s3.calls == [("aria-catalog", "catalog/v1/products.jsonl")]


def test_the_definition_comes_from_catalog_definition(capsys, catalog_file):
    custom = parse_definition({"catalog": {**definition_to_dict(EXAMPLE)["catalog"], "name": "Custom", "analyzer": "standard"}})
    environ = {"CATALOG_DEFINITION": json.dumps(definition_to_dict(custom)), "OPENSEARCH_PRODUCT_INDEX": "catalog"}
    _, manifest, _ = run(["--catalog", catalog_file(as_json(PRODUCTS[:3])), "--version", "v1"], capsys,
                         clients=Clients(), environ=environ)
    assert manifest["catalog"] == "Custom" and manifest["alias"] == "catalog" and manifest["index"] == "catalog-v1"


def test_an_invalid_definition_fails_before_reading(capsys):
    code = main(["--catalog", "s3://b/k", "--version", "v1"], environ={"CATALOG_DEFINITION": "{"}, clients=Clients())
    assert code == 2
    assert "CATALOG_DEFINITION" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--version", "V1"], ["--version=-v1"], ["--version", "v1", "--alias", "a_b"]])
def test_names_must_be_valid_index_names(capsys, catalog_file, args):
    code, manifest, _ = run(["--catalog", catalog_file(as_json(PRODUCTS[:1])), *args], capsys, clients=Clients())
    assert code == 1 and "lowercase letters" in manifest["error"]


def test_a_catalog_with_no_valid_product_fails(capsys, catalog_file):
    code, manifest, _ = run(["--catalog", catalog_file(as_json([{"sku": SECRET}])), "--version", "v1"], capsys,
                            clients=Clients())
    assert code == 1 and manifest["accepted"] == 0 and "nothing to index" in manifest["error"]
    assert SECRET not in json.dumps(manifest)


# --- Applying ---


def apply(capsys, catalog_file, opensearch, bedrock=None, products=PRODUCTS, extra=(), sleeps=None):
    bedrock = bedrock or FakeBedrock()
    sleep = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    return run(["--catalog", catalog_file(as_json(products)), "--version", "v2", "--replicas", "0", "--apply", *extra],
               capsys, clients=Clients(opensearch=opensearch, bedrock=bedrock), sleep=sleep), bedrock


def test_apply_builds_the_index_puts_the_pipeline_and_promotes_the_alias(capsys, catalog_file):
    opensearch = FakeOpenSearch()
    (code, manifest, _), bedrock = apply(capsys, catalog_file, opensearch)
    assert code == 0, manifest
    index = opensearch.indexes["products-v2"]
    assert index["body"] == index_body(EXAMPLE, 0)
    assert len(index["docs"]) == len(PRODUCTS)
    doc = index["docs"][PRODUCTS[0]["sku"]]
    assert {k: v for k, v in doc.items() if k != EMBEDDING_FIELD} == PRODUCTS[0]
    # One call per batch of 96, with the definition's model, as documents.
    assert [len(r["texts"]) for _, r in bedrock.requests] == [BATCH, len(PRODUCTS) - BATCH]
    assert {(model, r["input_type"]) for model, r in bedrock.requests} == {(EXAMPLE.embedding_model, "search_document")}
    assert opensearch.pipelines == {"products-hybrid": pipeline_body()}
    assert opensearch.alias_actions == [[{"add": {"index": "products-v2", "alias": "products"}}]]
    assert manifest["applied"] and manifest["promoted"] and manifest["indexed"] == len(PRODUCTS)
    assert manifest["previousIndices"] == []
    # The pipeline is in place before the alias moves to the new index.
    assert opensearch.calls.index(("PUT", "_search/pipeline/products-hybrid")) < opensearch.calls.index(("POST", "_aliases"))


def test_every_request_stays_on_the_paths_the_task_role_grants(capsys, catalog_file):
    opensearch = FakeOpenSearch(indexes=["products-v1"], aliases={"products": ["products-v1"]})
    apply(capsys, catalog_file, opensearch)
    allowed = ("products", "_aliases", "_search/pipeline/products-hybrid")
    for _, path in opensearch.calls:
        assert path in allowed or path.startswith("products-"), path


def test_promotion_moves_the_alias_in_one_call(capsys, catalog_file):
    opensearch = FakeOpenSearch(indexes=["products-v1"], aliases={"products": ["products-v1"]})
    (code, manifest, _), _ = apply(capsys, catalog_file, opensearch)
    assert code == 0
    assert opensearch.alias_actions == [[
        {"remove": {"index": "products-v1", "alias": "products"}},
        {"add": {"index": "products-v2", "alias": "products"}},
    ]]
    assert opensearch.aliases == {"products": ["products-v2"]}
    assert manifest["previousIndices"] == ["products-v1"]


def test_an_existing_version_is_refused_before_any_write_or_embedding(capsys, catalog_file):
    opensearch = FakeOpenSearch(indexes=["products-v2"])
    (code, manifest, _), bedrock = apply(capsys, catalog_file, opensearch)
    assert code == 1 and "already exists" in manifest["error"]
    assert bedrock.requests == [] and not any(method == "PUT" for method, _ in opensearch.calls)


def test_a_concrete_index_named_like_the_alias_is_refused(capsys, catalog_file):
    opensearch = FakeOpenSearch(indexes=["products"])
    (code, manifest, _), bedrock = apply(capsys, catalog_file, opensearch)
    assert code == 1 and "concrete index" in manifest["error"]
    assert bedrock.requests == [] and "products-v2" not in opensearch.indexes


def test_a_count_mismatch_leaves_the_alias_alone(capsys, catalog_file):
    opensearch = FakeOpenSearch(aliases={"products": []}, lose=1)
    (code, manifest, _), _ = apply(capsys, catalog_file, opensearch)
    assert code == 1 and "the alias is unchanged" in manifest["error"]
    assert opensearch.alias_actions == [] and opensearch.pipelines == {}
    assert manifest["created"] and not manifest["promoted"]


def test_rejected_bulk_items_fail_with_their_error_types_only(capsys, catalog_file):
    opensearch = FakeOpenSearch(bulk_error="mapper_parsing_exception")
    products = [{**PRODUCTS[0], "title": SECRET}]
    (code, manifest, err), _ = apply(capsys, catalog_file, opensearch, products=products)
    assert code == 1 and "mapper_parsing_exception x1" in manifest["error"]
    assert SECRET not in json.dumps(manifest) and SECRET not in err
    assert opensearch.alias_actions == []


def test_throttling_is_retried_with_exponential_backoff(capsys, catalog_file):
    bedrock = FakeBedrock(failures=[throttle(), throttle("ServiceUnavailableException")])
    sleeps = []
    (code, manifest, err), _ = apply(capsys, catalog_file, FakeOpenSearch(), bedrock=bedrock, sleeps=sleeps)
    assert code == 0, manifest
    assert sleeps == [2.0, 4.0]
    assert "ThrottlingException; retrying" in err and SECRET not in err


def test_throttling_gives_up_after_the_last_attempt(capsys, catalog_file):
    bedrock = FakeBedrock(failures=[throttle() for _ in range(job_module.ATTEMPTS)])
    sleeps = []
    (code, manifest, _), _ = apply(capsys, catalog_file, FakeOpenSearch(), bedrock=bedrock, sleeps=sleeps)
    assert code == 1 and manifest["error"] == "ClientError: ThrottlingException from InvokeModel"
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_other_bedrock_errors_are_not_retried(capsys, catalog_file):
    bedrock = FakeBedrock(failures=[throttle("AccessDeniedException")])
    sleeps = []
    (code, manifest, _), _ = apply(capsys, catalog_file, FakeOpenSearch(), bedrock=bedrock, sleeps=sleeps)
    assert code == 1 and "AccessDeniedException" in manifest["error"] and sleeps == []
    assert SECRET not in json.dumps(manifest)


def test_errors_are_described_without_their_messages():
    from opensearchpy.exceptions import RequestError

    error = RequestError(400, "mapper_parsing_exception", {"error": {"reason": f"failed to parse {SECRET}"}})
    assert describe(error) == "RequestError: HTTP 400 mapper_parsing_exception"
    assert describe(ValueError(SECRET)) == "ValueError"
    assert describe(JobError("index products-v2 already exists")) == "index products-v2 already exists"


def test_apply_needs_the_domain_host(capsys, catalog_file):
    from aria_indexer.__main__ import AwsClients

    clients = AwsClients({"AWS_REGION": "ap-northeast-2"})
    code, manifest, _ = run(["--catalog", catalog_file(as_json(PRODUCTS[:1])), "--version", "v1", "--apply"], capsys,
                            clients=clients)
    assert code == 1 and manifest["error"] == "OPENSEARCH_HOST is not set"
