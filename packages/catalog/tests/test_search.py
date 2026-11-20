"""search_products(): argument checks, provider dispatch, the hybrid OpenSearch query and its fallback.

The tool-facing behaviour (result envelope, actionable errors, sanitized failures) is tested with
the agent's local search tool in packages/agent/tests/unit/test_product_search_tool.py.
"""

import logging

import boto3
import opensearchpy
import pytest

from aria_catalog import embedding, providers, search
from aria_catalog.definition import canonical_arguments, load_definition, search_body
from aria_catalog.errors import CatalogBackendError, CatalogQueryError

EXAMPLE = load_definition()
VECTOR = [0.25] * 1024


@pytest.fixture(autouse=True)
def index_env(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "opensearch")
    monkeypatch.setenv("OPENSEARCH_PRODUCT_INDEX", "products")
    monkeypatch.setenv("OPENSEARCH_HOST", "search.example.com")


def product(**fields):
    """A product in the example catalog's field names."""
    base = {
        "sku": "p-1",
        "title": "Cotton Shirt",
        "gender": "Women",
        "category": "Apparel",
        "subcategory": "Topwear",
        "type": "Shirts",
        "colour": "Blue",
        "season": "Summer",
        "year": 2026,
        "usage": "Casual",
    }
    return {**base, **fields}


def card(**fields):
    """The product.v1 card of `product(**fields)`."""
    p = product(**fields)
    return {
        "id": p["sku"],
        "productDisplayName": p["title"],
        "gender": p["gender"],
        "masterCategory": p["category"],
        "subCategory": p["subcategory"],
        "articleType": p["type"],
        "baseColour": p["colour"],
        "season": p["season"],
        "year": p["year"],
        "usage": p["usage"],
    }


class FakeClient:
    """The OpenSearch client: returns `sources` as hits and records each search."""

    def __init__(self, sources):
        self.sources = sources
        self.searches = []

    def search(self, index, body, params=None):
        self.searches.append({"index": index, "body": body, "params": params})
        return {"hits": {"hits": [{"_source": source} for source in self.sources]}}


class FakeEmbedding:
    """`embedding.embed` and the query client: records each call; returns VECTOR or raises `error`."""

    def __init__(self, error=None):
        self.error, self.calls, self.client = error, [], object()

    def embed(self, texts, input_type, *, model, client):
        self.calls.append({"texts": texts, "input_type": input_type, "model": model, "client": client})
        if self.error:
            raise self.error
        return [VECTOR for _ in texts]


@pytest.fixture
def embedder(monkeypatch):
    def install(error=None):
        fake = FakeEmbedding(error)
        monkeypatch.setattr(embedding, "embed", fake.embed)
        monkeypatch.setattr(embedding, "query_client", lambda: fake.client)
        return fake

    return install


def serve(monkeypatch, provider, rows):
    """Select `provider` and have it return `rows`; returns the calls it receives."""
    monkeypatch.setenv("SEARCH_PROVIDER", provider)
    if provider == "opensearch":
        client = FakeClient(rows)
        monkeypatch.setattr(search, "get_client", lambda: client)
        monkeypatch.setattr(embedding, "embed", lambda texts, *a, **k: [VECTOR for _ in texts])
        monkeypatch.setattr(embedding, "query_client", lambda: None)
        return client.searches
    calls = []
    target = "fixture_search" if provider == "fixture" else "http_search"
    monkeypatch.setattr(providers, target, lambda definition, args: calls.append((definition, args)) or rows)
    return calls


def test_only_query_errors_blame_the_arguments():
    # The agent's tool shows a ValueError to the model as its own mistake; backend faults are not one.
    assert issubclass(CatalogQueryError, ValueError)
    assert issubclass(CatalogBackendError, RuntimeError) and not issubclass(CatalogBackendError, ValueError)


class TestNothingToSearch:
    @pytest.mark.parametrize(
        "arguments",
        [{}, None, {"query": "   "}, {"query": "\t\n"}, {"gender": " "}, {"year": " "}, {"query": " ", "season": ""}, {"limit": 5}],
    )
    def test_no_criteria_return_empty_without_a_backend(self, monkeypatch, arguments):
        monkeypatch.setattr(search, "get_client", lambda: pytest.fail("should not query"))
        monkeypatch.setattr(embedding, "query_client", lambda: pytest.fail("should not embed"))
        assert search.search_products(EXAMPLE, arguments) == []

    def test_blank_query_does_not_list_the_fixture(self, monkeypatch):
        # A blank query is no criterion: counted as one, it would list the first fixture products unfiltered.
        monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
        assert search.search_products(EXAMPLE, {"query": "   "}) == []

    def test_invalid_arguments_fail_before_any_backend(self, monkeypatch):
        monkeypatch.setattr(search, "get_client", lambda: pytest.fail("should not query"))
        with pytest.raises(CatalogQueryError, match="invalid gender"):
            search.search_products(EXAMPLE, {"query": "shirt", "gender": "Martian"})


class TestOpenSearch:
    def test_a_query_runs_the_hybrid_search_through_the_pipeline(self, monkeypatch, embedder):
        client = FakeClient([product()])
        monkeypatch.setattr(search, "get_client", lambda: client)
        fake = embedder()

        arguments = {"query": " 따뜻한 셔츠 ", "gender": "women", "season": "summer", "limit": 5}
        assert search.search_products(EXAMPLE, arguments, on_fallback=lambda name: pytest.fail("no fallback")) == [card()]

        assert fake.calls == [
            {"texts": ["따뜻한 셔츠"], "input_type": "search_query", "model": EXAMPLE.embedding_model, "client": fake.client}
        ]
        [sent] = client.searches
        assert sent["index"] == "products"
        assert sent["params"] == {"search_pipeline": "products-hybrid"}
        assert sent["body"] == search_body(EXAMPLE, canonical_arguments(EXAMPLE, arguments), VECTOR)
        assert "hybrid" in sent["body"]["query"]

    def test_the_alias_names_the_index_and_its_pipeline(self, monkeypatch, embedder):
        monkeypatch.setenv("OPENSEARCH_PRODUCT_INDEX", "nordly")
        client = FakeClient([])
        monkeypatch.setattr(search, "get_client", lambda: client)
        embedder()
        search.search_products(EXAMPLE, {"query": "shirt"})
        assert client.searches[0]["index"] == "nordly"
        assert client.searches[0]["params"] == {"search_pipeline": "nordly-hybrid"}

    def test_the_default_alias_is_products(self, monkeypatch, embedder):
        monkeypatch.delenv("OPENSEARCH_PRODUCT_INDEX")
        client = FakeClient([])
        monkeypatch.setattr(search, "get_client", lambda: client)
        embedder()
        search.search_products(EXAMPLE, {"query": "shirt"})
        assert client.searches[0]["index"] == "products"

    def test_filters_alone_run_lexically_without_an_embedding(self, monkeypatch):
        client = FakeClient([product()])
        monkeypatch.setattr(search, "get_client", lambda: client)
        monkeypatch.setattr(embedding, "query_client", lambda: pytest.fail("nothing to embed"))

        search.search_products(EXAMPLE, {"gender": "Women", "season": "summer"})

        [sent] = client.searches
        assert sent["params"] is None
        assert sent["body"] == search_body(EXAMPLE, canonical_arguments(EXAMPLE, {"gender": "Women", "season": "Summer"}))

    def test_an_embedding_failure_falls_back_to_the_lexical_search(self, monkeypatch, embedder):
        client = FakeClient([product()])
        monkeypatch.setattr(search, "get_client", lambda: client)
        embedder(error=TimeoutError("read timed out"))
        fallbacks = []

        products = search.search_products(EXAMPLE, {"query": "shirt", "gender": "Women"}, on_fallback=fallbacks.append)

        assert products == [card()]
        assert fallbacks == ["TimeoutError"]
        [sent] = client.searches
        assert sent["params"] is None
        assert sent["body"] == search_body(EXAMPLE, canonical_arguments(EXAMPLE, {"query": "shirt", "gender": "Women"}))
        assert "hybrid" not in sent["body"]["query"]

    def test_a_malformed_embedding_also_falls_back(self, monkeypatch, embedder):
        monkeypatch.setattr(search, "get_client", lambda: FakeClient([product()]))
        embedder(error=CatalogBackendError("Embedding response has the wrong number of vectors"))
        fallbacks = []
        search.search_products(EXAMPLE, {"query": "shirt"}, on_fallback=fallbacks.append)
        assert fallbacks == ["CatalogBackendError"]

    def test_the_fallback_is_logged_without_values_by_default(self, monkeypatch, embedder, caplog):
        monkeypatch.setattr(search, "get_client", lambda: FakeClient([product()]))
        embedder(error=PermissionError("secret detail"))
        with caplog.at_level(logging.WARNING, logger="aria_catalog"):
            search.search_products(EXAMPLE, {"query": "secret shirt"})
        assert caplog.messages == ["product_search_embedding_failed provider=opensearch error_type=PermissionError"]

    def test_hits_map_to_cards(self, monkeypatch):
        serve(monkeypatch, "opensearch", [product(sku="1"), product(sku="2")])
        assert search.search_products(EXAMPLE, {"query": "shirt"}) == [card(sku="1"), card(sku="2")]


class TestDispatch:
    def test_the_fixture_is_the_default(self, monkeypatch):
        monkeypatch.delenv("SEARCH_PROVIDER")
        assert search.search_provider() == "fixture"
        assert [p["id"] for p in search.search_products(EXAMPLE, {"query": "Winter Jacket", "gender": "Men"})][:1] == ["sample-1"]

    def test_the_fixture_gets_the_canonical_arguments(self, monkeypatch):
        calls = serve(monkeypatch, "fixture", [])
        search.search_products(EXAMPLE, {"query": " shirt ", "gender": "women", "limit": 50})
        assert calls == [(EXAMPLE, canonical_arguments(EXAMPLE, {"query": "shirt", "gender": "Women", "limit": 20}))]

    def test_http_gets_the_canonical_arguments(self, monkeypatch):
        calls = serve(monkeypatch, "http", [product()])
        assert search.search_products(EXAMPLE, {"category1": "apparel", "category2": "TOPWEAR"}) == [card()]
        [(definition, args)] = calls
        assert definition is EXAMPLE
        assert args.filters == (("category", "Apparel"), ("subcategory", "Topwear"))

    def test_unknown_provider_is_a_backend_error(self, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "elastic")
        with pytest.raises(CatalogBackendError, match="Unknown search provider"):
            search.search_products(EXAMPLE, {"query": "shirt"})


class TestCards:
    @pytest.mark.parametrize("provider", ["fixture", "opensearch", "http"])
    def test_every_provider_drops_products_outside_product_v1(self, monkeypatch, provider):
        missing_year = {key: value for key, value in product(sku="p-3").items() if key != "year"}
        rows = [product(sku="p-1"), product(sku="SKU 2"), missing_year, product(sku="p-4", usage=None), product(sku="p-5"), "p-6"]
        serve(monkeypatch, provider, rows)
        dropped = []

        products = search.search_products(EXAMPLE, {"query": "shirt", "limit": 5}, on_invalid=dropped.append)

        assert [p["id"] for p in products] == ["p-1", "p-5"]
        assert dropped == [4]

    def test_valid_products_are_reported_as_none_dropped(self, monkeypatch):
        serve(monkeypatch, "opensearch", [product()])
        dropped = []
        assert search.search_products(EXAMPLE, {"query": "shirt"}, on_invalid=dropped.append) == [card()]
        assert dropped == []

    def test_no_valid_product_is_a_backend_error_not_an_empty_match(self, monkeypatch):
        serve(monkeypatch, "http", [{"id": "1"}, product(sku="bad id")])
        dropped = []
        with pytest.raises(CatalogBackendError, match="product.v1"):
            search.search_products(EXAMPLE, {"query": "shirt"}, on_invalid=dropped.append)
        assert dropped == [2]

    def test_no_match_is_still_an_empty_result(self, monkeypatch):
        serve(monkeypatch, "opensearch", [])
        assert search.search_products(EXAMPLE, {"query": "shirt"}, on_invalid=lambda count: pytest.fail("nothing dropped")) == []

    def test_dropped_products_are_logged_without_values_by_default(self, monkeypatch, caplog):
        serve(monkeypatch, "opensearch", [product(), product(sku="secret sku", title="Hidden Name")])
        with caplog.at_level(logging.WARNING, logger="aria_catalog"):
            search.search_products(EXAMPLE, {"query": "shirt"})
        assert caplog.messages == ["product_search_invalid_product provider=opensearch count=1"]

    def test_results_never_exceed_the_limit(self, monkeypatch):
        # An HTTP catalog may ignore the limit it was sent.
        serve(monkeypatch, "http", [product(sku=f"p-{n}") for n in range(5)])
        assert [p["id"] for p in search.search_products(EXAMPLE, {"query": "shirt", "limit": 2})] == ["p-0", "p-1"]


@pytest.fixture
def fresh_client():
    search.get_client.cache_clear()
    yield
    search.get_client.cache_clear()


class FakeSession:
    def get_credentials(self):
        return "credentials"


class TestRegion:
    @pytest.mark.parametrize(
        "env, region",
        [
            ({"AWS_REGION": "eu-central-1", "AWS_DEFAULT_REGION": "us-east-1"}, "eu-central-1"),
            ({"AWS_DEFAULT_REGION": "us-east-1"}, "us-east-1"),
        ],
    )
    def test_region_comes_from_the_environment(self, monkeypatch, env, region):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert search.aws_region() == region

    def test_no_region_is_a_clear_backend_error(self, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        with pytest.raises(CatalogBackendError, match="AWS_REGION or AWS_DEFAULT_REGION"):
            search.aws_region()

    def test_opensearch_requests_are_signed_for_the_environment_region(self, monkeypatch, fresh_client):
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        signed = {}
        monkeypatch.setattr(boto3, "Session", FakeSession)
        monkeypatch.setattr(opensearchpy, "AWSV4SignerAuth", lambda credentials, region: signed.update(region=region))
        monkeypatch.setattr(opensearchpy, "OpenSearch", lambda **settings: settings)

        settings = search.get_client()

        assert signed == {"region": "eu-central-1"}
        assert settings["hosts"] == [{"host": "search.example.com", "port": 443}]

    def test_opensearch_without_a_region_fails_before_creating_a_session(self, monkeypatch, fresh_client):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setattr(boto3, "Session", lambda: pytest.fail("no AWS session without a region"))
        with pytest.raises(CatalogBackendError, match="AWS_REGION"):
            search.search_products(EXAMPLE, {"query": "shirt"})

    def test_opensearch_without_a_host_is_a_backend_error(self, monkeypatch, fresh_client):
        monkeypatch.setenv("OPENSEARCH_HOST", "")
        with pytest.raises(CatalogBackendError, match="OPENSEARCH_HOST"):
            search.search_products(EXAMPLE, {"query": "shirt"})
