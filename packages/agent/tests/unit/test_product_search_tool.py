"""The local search tool: the generated schema, the product.v1 envelope, errors the model can fix,
the embedding fallback, and backend faults without details. Every call goes through Strands'
own path: `AgentTool.stream()`, or an Agent's direct tool call."""

import asyncio
import io
import json
from urllib.error import HTTPError

import pytest
from strands import Agent
from strands.models.bedrock import BedrockModel
from structlog.testing import capture_logs

import aria_catalog
from aria_catalog import embedding as catalog_embedding
from aria_catalog import providers as catalog_providers
from aria_catalog import search as catalog_search
from tools import product_search
from tools.product_search import ProductSearchTool

ENDPOINT = "https://catalog.internal.example/search"
SECRET_ARN = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:catalog-AbCdEf"  # nosec B105 # a fake secret ARN with the placeholder account
API_KEY = "catalog-key-s3cr3t"
EXAMPLE = aria_catalog.load_definition()
EMPTY = {"schemaVersion": "product.v1", "count": 0, "products": []}

# A definition other than the example: one keyword filter and one range filter.
SHOP = {
    "catalog": {
        "name": "Shop",
        "description": "Search the shop.",
        "idField": "id",
        "fields": [
            {"name": "id", "type": "keyword"},
            {"name": "title", "type": "text", "search": True},
            {"name": "kind", "type": "keyword", "filter": True, "values": ["Coat", "Boot"], "description": "Kind"},
            {"name": "price", "type": "integer", "filter": "range", "description": "Price in KRW"},
            {"name": "year", "type": "integer"},
        ],
        "embedding": {"text": "{title}"},
        "card": {
            "id": "id",
            "productDisplayName": "title",
            "gender": "kind",
            "masterCategory": "kind",
            "subCategory": "kind",
            "articleType": "kind",
            "baseColour": "kind",
            "season": "kind",
            "year": "year",
            "usage": "kind",
        },
    }
}


@pytest.fixture(autouse=True)
def fixture_catalog(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
    monkeypatch.delenv("CATALOG_DEFINITION", raising=False)
    monkeypatch.setattr(catalog_providers, "_credential_cache", None)


def run(tool, arguments):
    """Stream the tool the way the agent loop does and return its last event, the ToolResultEvent."""
    tool_use = {"toolUseId": "t1", "name": "search_products", "input": arguments}

    async def stream():
        return [event async for event in tool.stream(tool_use, {})][-1]

    return asyncio.run(stream())


def call(**arguments):
    """Run the deployment's search tool and return its tool result."""
    return run(ProductSearchTool(), arguments).tool_result


def payload(result):
    assert result["status"] == "success"
    return json.loads(result["content"][0]["text"])


def error_text(result):
    assert result["status"] == "error" and result["toolUseId"] == "t1"
    return result["content"][0]["text"]


def ids(result):
    return [p["id"] for p in payload(result)["products"]]


def events(logs, name):
    return [entry for entry in logs if entry["event"] == name]


def assert_no_values(logs, *values):
    """No log entry carries the credential, the endpoint or the given argument and product values."""
    text = json.dumps(logs, default=str)
    for value in (API_KEY, SECRET_ARN, ENDPOINT, "catalog.internal.example", *values):
        assert value not in text


def product(**fields):
    """A product in the example catalog's field names, as a backend returns it."""
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


# --- The schema ---


def test_the_tool_spec_is_generated_from_the_definition():
    tool = ProductSearchTool()
    assert tool.tool_name == "search_products"
    assert tool.tool_spec == aria_catalog.tool_spec(EXAMPLE)
    assert list(tool.tool_spec["inputSchema"]["json"]["properties"]) == [
        "query", "season", "gender", "category1", "category2", "year", "usage", "limit",
    ]  # fmt: skip


def test_schema_lists_every_declared_value_as_an_enum():
    # The model must see the closed lists, or it guesses values the catalog rejects.
    properties = ProductSearchTool().tool_spec["inputSchema"]["json"]["properties"]
    keyword_filters = [f for f in EXAMPLE.fields if f.filter == "exact" and f.type == "keyword"]
    assert {f.parameter for f in keyword_filters} == {"season", "gender", "category1", "category2", "usage"}
    for f in keyword_filters:
        assert properties[f.parameter]["enum"] == list(f.values)


def test_the_schema_follows_catalog_definition(monkeypatch):
    monkeypatch.setenv("CATALOG_DEFINITION", json.dumps(SHOP))
    spec = ProductSearchTool().tool_spec
    properties = spec["inputSchema"]["json"]["properties"]
    assert list(properties) == ["query", "kind", "price", "limit"]
    assert properties["kind"]["enum"] == ["Coat", "Boot"]
    assert properties["price"]["type"] == "object" and set(properties["price"]["properties"]) == {"min", "max"}
    assert spec["description"].startswith("Search the shop.\n")


def test_an_invalid_catalog_definition_fails_when_the_tool_is_built(monkeypatch):
    monkeypatch.setenv("CATALOG_DEFINITION", '{"catalog": {}}')
    with pytest.raises(aria_catalog.DefinitionError, match="catalog.name"):
        ProductSearchTool()


def test_the_tool_is_bound_to_its_definition():
    shop = aria_catalog.parse_definition(SHOP)
    tool = ProductSearchTool(shop)
    assert tool.definition is shop and tool.tool_spec == aria_catalog.tool_spec(shop)


# --- Results ---


def test_returns_the_product_v1_envelope_as_json_text():
    result = call(query="Winter", gender="Men")
    assert result == {"toolUseId": "t1", "status": "success", "content": [{"text": result["content"][0]["text"]}]}
    assert payload(result)["schemaVersion"] == "product.v1"
    assert ids(result) == ["sample-1"]


def test_korean_text_is_not_escaped():
    text = call(query="Winter", gender="Men")["content"][0]["text"]
    assert "겨울 자켓" in text


def test_the_agent_calls_it_through_strands():
    agent = Agent(model=BedrockModel(region_name="us-west-2"), tools=[ProductSearchTool()], callback_handler=None)
    result = agent.tool.search_products(query="Winter", gender="Men", record_direct_tool_call=False)
    assert result["status"] == "success"
    assert ids(result) == ["sample-1"]


def test_filters_alone_are_a_search():
    assert ids(call(gender="men", season="WINTER", category2="topwear")) == ["sample-1", "nordly-002", "nordly-025"]


def test_limit_is_capped(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "opensearch")
    client = FakeOpenSearch([product()])
    monkeypatch.setattr(catalog_search, "get_client", lambda: client)
    payload(call(gender="Women", limit=500))
    assert client.bodies[0]["size"] == aria_catalog.MAX_LIMIT == 20


def test_no_criteria_returns_an_empty_result():
    assert payload(call()) == EMPTY


def test_missing_input_is_no_criteria():
    tool_use = {"toolUseId": "t1", "name": "search_products"}

    async def stream():
        return [event async for event in ProductSearchTool().stream(tool_use, {})][-1].tool_result

    assert payload(asyncio.run(stream())) == EMPTY


@pytest.mark.parametrize("blank", ["   ", "\t\n", ""])
def test_blank_query_is_no_criterion(blank):
    # An unfiltered fixture search would return its first three products.
    assert payload(call(query=blank)) == EMPTY


def test_blank_query_does_not_widen_other_criteria():
    assert ids(call(query="   ", gender="Men")) == ids(call(gender="Men"))


def test_success_is_logged_with_provider_count_and_duration():
    with capture_logs() as logs:
        call(query="Winter", gender="Men")
    [finished] = events(logs, "product_search_finished")
    assert finished["provider"] == "fixture" and finished["results"] == 1
    assert isinstance(finished["duration_ms"], int) and finished["duration_ms"] >= 0
    assert_no_values(logs, "Winter", "sample-1")


# --- Errors the model can fix ---


def test_invalid_value_returns_an_actionable_error():
    with capture_logs() as logs:
        event = run(ProductSearchTool(), {"category2": "Outerwear"})
    text = error_text(event.tool_result)
    assert text.startswith("Error: invalid category2 'Outerwear'; expected one of: Topwear, Shoes, ")
    assert isinstance(event.exception, aria_catalog.CatalogQueryError)
    # The model's mistake, not a backend fault: nothing for an operator to look at.
    assert not events(logs, "product_search_failed")


def test_an_unknown_parameter_names_the_accepted_ones():
    # The example has neither `name` nor `price`.
    accepted = "query, season, gender, category1, category2, year, usage, limit"
    assert error_text(call(name="jacket")) == f"Error: unknown parameter 'name'; expected one of: {accepted}"
    assert error_text(call(price={"max": 50000})).startswith("Error: unknown parameter 'price'")


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"query": "x" * 201}, "Error: query is longer than 200 characters; shorten it or move details into the filters"),
        ({"query": "coat", "limit": 0}, "Error: invalid limit 0; expected a whole number from 1 to 20"),
        ({"year": "soon"}, "Error: invalid year 'soon'; expected a whole number"),
    ],
)
def test_a_catalog_query_error_reaches_the_model(arguments, message):
    assert error_text(call(**arguments)) == message


# --- Backend faults ---


def test_unexpected_failure_is_reported_without_details(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("search-domain.internal:443 refused")

    monkeypatch.setattr(product_search.aria_catalog, "search_products", boom)
    with capture_logs() as logs:
        event = run(ProductSearchTool(), {"query": "x"})
    assert error_text(event.tool_result) == "Error: RuntimeError - Product search failed"
    assert type(event.exception) is RuntimeError
    [failed] = events(logs, "product_search_failed")
    assert failed["provider"] == "fixture" and failed["error_type"] == "ConnectionError"
    assert "search-domain.internal" not in json.dumps(logs)


def test_a_value_error_from_the_backend_is_not_blamed_on_the_arguments(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(product_search.aria_catalog, "search_products", broken)
    with capture_logs() as logs:
        text = error_text(call(query="x"))
    assert text == "Error: RuntimeError - Product search failed"
    [failed] = events(logs, "product_search_failed")
    assert failed["error_type"] == "ValueError" and "reason" not in failed


class Upstream:
    """The HTTPS catalog and its Secrets Manager credential, faked at the network edge.

    Each response is a JSON value, raw bytes, or an int HTTP error status.
    """

    def __init__(self, monkeypatch):
        self.responses = []
        self.requests = []
        self.secrets = [{"header": "x-api-key", "value": API_KEY}]
        self.secret_fetches = 0
        monkeypatch.setattr(catalog_providers, "build_opener", lambda *handlers: self)
        monkeypatch.setattr(catalog_providers.boto3, "client", lambda service, region_name=None: self)

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, int):
            raise HTTPError(request.full_url, response, "error", {}, None)
        return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())

    def get_secret_value(self, SecretId):
        secret = self.secrets[min(self.secret_fetches, len(self.secrets) - 1)]
        self.secret_fetches += 1
        return {"SecretString": json.dumps(secret)}


@pytest.fixture
def http_catalog(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "http")
    monkeypatch.setenv("SEARCH_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("SEARCH_SECRET_ARN", SECRET_ARN)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("SEARCH_RESULT_PATH", raising=False)
    return Upstream(monkeypatch)


class TestHttpCatalog:
    def test_gets_canonical_values(self, http_catalog):
        http_catalog.responses.append({"products": [product()]})
        result = call(
            query="  shirt ",
            category1="apparel",
            category2="TOPWEAR",
            gender="women",
            season="summer",
            year=2026,
            limit=50,
        )
        assert ids(result) == ["p-1"]
        [request] = http_catalog.requests
        assert json.loads(request.data) == {
            "query": "shirt",
            "season": "Summer",
            "gender": "Women",
            "category1": "Apparel",
            "category2": "Topwear",
            "year": 2026,
            "usage": None,
            "limit": 20,
        }

    def test_a_definition_with_a_range_sends_it(self, http_catalog, monkeypatch):
        monkeypatch.setenv("CATALOG_DEFINITION", json.dumps(SHOP))
        http_catalog.responses.append({"products": [{"id": "c-1", "title": "Wool Coat", "kind": "Coat", "year": 2026}]})
        result = call(query="coat", kind="coat", price={"max": "200000"})
        assert ids(result) == ["c-1"]
        assert json.loads(http_catalog.requests[0].data) == {
            "query": "coat", "kind": "Coat", "price": {"min": None, "max": 200000}, "limit": 3,
        }  # fmt: skip

    @pytest.mark.parametrize(
        "response, reason",
        [
            (b"<html>Bad gateway</html>", "Catalog response is not valid JSON"),
            (b"[" * (256 * 1024 + 1), "Catalog response exceeds 256 KiB"),
            ({"products": [product()] * 21}, "Catalog returned more than 20 products"),
            ({"results": [product()]}, "Catalog response has no product list at SEARCH_RESULT_PATH"),
            (503, "Catalog returned HTTP 503"),
        ],
    )
    def test_catalog_faults_are_not_reported_as_bad_arguments(self, http_catalog, response, reason):
        http_catalog.responses.append(response)
        with capture_logs() as logs:
            text = error_text(call(query="shirt"))
        assert text == "Error: RuntimeError - Product search failed"
        [failed] = events(logs, "product_search_failed")
        assert failed["provider"] == "http" and failed["error_type"] == "CatalogBackendError"
        assert failed["reason"] == reason
        assert isinstance(failed["duration_ms"], int) and failed["duration_ms"] >= 0
        assert_no_values(logs, "shirt")

    def test_no_valid_product_is_a_failure_not_an_empty_match(self, http_catalog):
        # Schema drift: every product misses the card's fields.
        http_catalog.responses.append({"products": [{"sku": "SKU 1", "title": "Cotton Shirt"}]})
        with capture_logs() as logs:
            text = error_text(call(query="shirt"))
        assert text.endswith("Product search failed")
        [dropped] = events(logs, "product_search_invalid_product")
        assert dropped["provider"] == "http" and dropped["count"] == 1
        [failed] = events(logs, "product_search_failed")
        assert failed["reason"] == "No product in the catalog response matches product.v1"
        assert_no_values(logs, "SKU 1", "Cotton Shirt")

    def test_plain_http_endpoint_is_a_backend_fault(self, http_catalog, monkeypatch):
        monkeypatch.setenv("SEARCH_ENDPOINT", "http://catalog.internal.example/search")
        with capture_logs() as logs:
            text = error_text(call(query="shirt"))
        assert text.endswith("Product search failed") and not http_catalog.requests
        assert events(logs, "product_search_failed")[0]["error_type"] == "CatalogBackendError"

    def test_unsupported_credential_header_is_a_backend_fault(self, http_catalog):
        http_catalog.secrets = [{"header": "Cookie", "value": API_KEY}]
        with capture_logs() as logs:
            text = error_text(call(query="shirt"))
        assert text.endswith("Product search failed") and not http_catalog.requests
        assert events(logs, "product_search_failed")[0]["reason"] == "Unsupported catalog credential header"
        assert_no_values(logs)

    def test_credential_is_reused_between_searches(self, http_catalog):
        http_catalog.responses += [{"products": [product()]}, {"products": [product()]}]
        call(query="shirt")
        call(query="shirt")
        assert http_catalog.secret_fetches == 1

    def test_rotated_credential_is_fetched_again_once_on_401(self, http_catalog):
        http_catalog.secrets = [{"header": "x-api-key", "value": "old-key"}, {"header": "x-api-key", "value": API_KEY}]
        http_catalog.responses += [401, {"products": [product()]}]
        assert ids(call(query="shirt")) == ["p-1"]
        assert [request.get_header("X-api-key") for request in http_catalog.requests] == ["old-key", API_KEY]
        assert http_catalog.secret_fetches == 2

    def test_credential_rejected_after_the_refetch_fails(self, http_catalog):
        http_catalog.responses += [403, 403]
        with capture_logs() as logs:
            text = error_text(call(query="shirt"))
        assert text.endswith("Product search failed") and len(http_catalog.requests) == 2
        assert events(logs, "product_search_failed")[0]["reason"] == "Catalog returned HTTP 403"
        assert_no_values(logs)


# --- OpenSearch ---


class FakeOpenSearch:
    """The OpenSearch client: returns `sources` as hits and records each search's body and params."""

    def __init__(self, sources):
        self.sources = sources
        self.bodies, self.params = [], []

    def search(self, index, body, params=None):
        self.bodies.append(body)
        self.params.append(params)
        return {"hits": {"hits": [{"_source": source} for source in self.sources]}}


@pytest.fixture
def opensearch(monkeypatch):
    """Select OpenSearch with `hits` as its results, and a query embedding that works or raises `error`."""

    def serve(hits, error=None):
        monkeypatch.setenv("SEARCH_PROVIDER", "opensearch")
        client = FakeOpenSearch(hits)
        monkeypatch.setattr(catalog_search, "get_client", lambda: client)

        def embed(texts, input_type, *, model, client):
            if error is not None:
                raise error
            return [[0.5] * 1024 for _ in texts]

        monkeypatch.setattr(catalog_embedding, "embed", embed)
        monkeypatch.setattr(catalog_embedding, "query_client", lambda: object())
        return client

    return serve


def test_a_query_runs_the_hybrid_search(opensearch):
    client = opensearch([product()])
    with capture_logs() as logs:
        assert ids(call(query="shirt", gender="Women")) == ["p-1"]
    assert "hybrid" in client.bodies[0]["query"]
    assert client.params == [{"search_pipeline": "products-hybrid"}]
    assert not events(logs, "product_search_embedding_failed")


def test_an_embedding_failure_falls_back_to_lexical_and_is_logged(opensearch):
    client = opensearch([product()], error=TimeoutError("Read timeout on endpoint URL: bedrock secret-detail"))
    with capture_logs() as logs:
        result = call(query="shirt", gender="Women")

    assert ids(result) == ["p-1"]  # The shopper still gets an answer.
    assert "hybrid" not in client.bodies[0]["query"] and client.params == [None]
    [fallback] = events(logs, "product_search_embedding_failed")
    assert fallback == {
        "event": "product_search_embedding_failed",
        "log_level": "warning",
        "provider": "opensearch",
        "error_type": "TimeoutError",
    }
    assert events(logs, "product_search_finished")[0]["results"] == 1
    assert_no_values(logs, "shirt", "secret-detail")


def test_products_outside_product_v1_are_dropped_and_counted(opensearch):
    missing_year = {key: value for key, value in product(sku="p-3").items() if key != "year"}
    opensearch([product(sku="p-1"), product(sku="SKU 2", title="Hidden Name"), missing_year, product(sku="p-4")])

    with capture_logs() as logs:
        result = payload(call(query="shirt", limit=5))

    assert [p["id"] for p in result["products"]] == ["p-1", "p-4"]
    assert result["count"] == 2
    [dropped] = events(logs, "product_search_invalid_product")
    assert dropped == {
        "event": "product_search_invalid_product",
        "log_level": "warning",
        "provider": "opensearch",
        "count": 2,
    }
    assert events(logs, "product_search_finished")[0]["results"] == 2
    assert_no_values(logs, "SKU 2", "Hidden Name", "shirt")


def test_missing_region_is_a_backend_fault(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "opensearch")
    monkeypatch.setenv("OPENSEARCH_HOST", "search.internal.example")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    catalog_search.get_client.cache_clear()

    with capture_logs() as logs:
        text = error_text(call(query="shirt"))

    assert text.endswith("Product search failed")
    [failed] = events(logs, "product_search_failed")
    assert failed["provider"] == "opensearch"
    assert failed["reason"] == "Set AWS_REGION or AWS_DEFAULT_REGION for the catalog's AWS clients"
