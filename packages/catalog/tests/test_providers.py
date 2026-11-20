"""The fixture provider and the HTTPS catalog adapter, with the network and Secrets Manager faked."""

import io
import json
from urllib.error import HTTPError

import pytest

from aria_catalog import providers
from aria_catalog.definition import canonical_arguments, definition_to_dict, load_definition, parse_definition
from aria_catalog.errors import CatalogBackendError
from aria_catalog.providers import NoRedirect, fixture_search, http_body
from aria_catalog.search import search_products

EXAMPLE = load_definition()
ENDPOINT = "https://catalog.example/search"
SECRET_ARN = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:catalog-AbCdEf"  # nosec B105 # a fake secret ARN with the placeholder account


def product(**fields):
    """A product in the example catalog's field names."""
    base = {
        "sku": "p-1",
        "title": "Cotton Shirt",
        "description": "A light shirt",
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


def args(**arguments):
    return canonical_arguments(EXAMPLE, arguments)


def priced_definition():
    """The example with a range filter added: an integer `price`. The example itself has none."""
    data = definition_to_dict(EXAMPLE)
    data["catalog"]["fields"].append({"name": "price", "type": "integer", "filter": "range"})
    return parse_definition(data)


PRICED = priced_definition()


def priced(**arguments):
    return canonical_arguments(PRICED, arguments)


def ids(rows):
    return [row["sku"] for row in rows]


# --- The fixture ---


@pytest.fixture
def catalog_rows(monkeypatch):
    def use(*rows):
        monkeypatch.setattr(providers, "example_products", lambda: [dict(row) for row in rows])

    return use


SCORED = [
    product(sku="a", title="Wool Coat", description="warm winter coat"),
    product(sku="b", title="Winter Jacket", description="light"),
    product(sku="c", title="Rain Boots", description="for winter rain"),
    product(sku="d", title="Winter Coat", description="a winter coat"),
    product(sku="e", title="Linen Shirt", description="cool"),
]


class TestFixture:
    def test_title_words_weigh_three_times_description_words(self, catalog_rows):
        catalog_rows(*SCORED)
        # d: 3+3+1+1, a: 1+3+1, b: 3, c: 1; e matches nothing and is left out.
        assert ids(fixture_search(EXAMPLE, args(query="winter coat", limit=10))) == ["d", "a", "b", "c"]

    def test_ties_keep_catalog_order(self, catalog_rows):
        catalog_rows(*SCORED)
        # d: 4, b: 3, then a and c tie at 1 and keep their order.
        assert ids(fixture_search(EXAMPLE, args(query="winter", limit=10))) == ["d", "b", "a", "c"]

    @pytest.mark.parametrize("query", ["WINTER", "Winter winter", "  winter  "])
    def test_words_are_case_folded_and_counted_once(self, catalog_rows, query):
        catalog_rows(*SCORED)
        assert ids(fixture_search(EXAMPLE, args(query=query, limit=10))) == ["d", "b", "a", "c"]

    def test_a_query_that_matches_nothing_finds_nothing(self, catalog_rows):
        catalog_rows(*SCORED)
        assert fixture_search(EXAMPLE, args(query="sandals")) == []

    def test_filters_and_ranges_must_all_hold(self, catalog_rows):
        # The fixture applies whatever ranges the canonical arguments hold; the example declares none.
        catalog_rows(
            product(sku="a", gender="Men", price=50000),
            product(sku="b", gender="Women", price=50000),
            product(sku="c", gender="Men", price=150000),
            product(sku="d", gender="Men", price=None),
            product(sku="e", gender="Men", season="Winter", price=60000),
        )
        assert ids(fixture_search(EXAMPLE, priced(gender="Men", price={"max": 100000}, limit=10))) == ["a", "e"]
        assert ids(fixture_search(EXAMPLE, priced(gender="Men", price={"min": 60000}, limit=10))) == ["c", "e"]
        assert ids(fixture_search(EXAMPLE, args(gender="Men", season="Winter", limit=10))) == ["e"]

    def test_without_a_query_the_filtered_products_come_in_catalog_order_up_to_the_limit(self, catalog_rows):
        catalog_rows(*SCORED)
        assert ids(fixture_search(EXAMPLE, args(gender="Women", limit=3))) == ["a", "b", "c"]

    def test_the_example_catalog(self):
        result = fixture_search(EXAMPLE, args(query="자켓", gender="Men", limit=10))
        assert ids(result) == ["sample-1", "nordly-010", "nordly-008", "nordly-037"]
        boots = fixture_search(EXAMPLE, args(gender="Women", season="Winter", category1="Footwear", category2="Shoes"))
        assert [row["type"] for row in boots] == ["Boots", "Boots", "Boots"]

    def test_the_fixture_serves_only_the_example_definition(self):
        other = parse_definition({"catalog": {**definition_to_dict(EXAMPLE)["catalog"], "name": "Other"}})
        with pytest.raises(CatalogBackendError, match="only the example catalog"):
            fixture_search(other, canonical_arguments(other, {"query": "shirt"}))

    def test_search_products_returns_cards_from_the_fixture(self, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
        result = search_products(EXAMPLE, {"query": "Winter Jacket", "gender": "men", "season": "WINTER"})
        assert result[0] == {
            "id": "sample-1",
            "productDisplayName": "겨울 자켓 Winter Jacket",
            "gender": "Men",
            "masterCategory": "Apparel",
            "subCategory": "Topwear",
            "articleType": "Topwear",
            "baseColour": "Blue",
            "season": "Winter",
            "year": 2026,
            "usage": "Casual",
        }


# --- The HTTPS catalog ---


class FakeCatalog:
    """The HTTPS catalog: records each request and answers with the next response.

    A response is a JSON value, raw bytes, or an int HTTP error status.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.handlers = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, int):
            raise HTTPError(request.full_url, response, "error", {}, None)
        return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())


class FakeSecrets:
    """Secrets Manager: each fetch returns the next secret value, and the last one repeats."""

    def __init__(self, values):
        self.values = list(values)
        self.fetches = 0
        self.regions = []

    def client(self, service, region_name=None):
        assert service == "secretsmanager"
        self.regions.append(region_name)
        return self

    def get_secret_value(self, SecretId):
        assert SecretId == SECRET_ARN
        value = self.values[min(self.fetches, len(self.values) - 1)]
        self.fetches += 1
        return {"SecretString": value if isinstance(value, str) else json.dumps(value)}


@pytest.fixture(autouse=True)
def http_env(monkeypatch):
    monkeypatch.setenv("SEARCH_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    for name in ("SEARCH_SECRET_ARN", "SEARCH_RESULT_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(providers, "_credential_cache", None)


@pytest.fixture
def catalog(monkeypatch):
    def serve(*responses):
        fake = FakeCatalog(responses)

        def build_opener(*handlers):
            fake.handlers.extend(handlers)
            return fake

        monkeypatch.setattr(providers, "build_opener", build_opener)
        return fake

    return serve


@pytest.fixture
def secrets(monkeypatch):
    def store(*values):
        fake = FakeSecrets(values)
        monkeypatch.setenv("SEARCH_SECRET_ARN", SECRET_ARN)
        monkeypatch.setattr(providers.boto3, "client", fake.client)
        return fake

    return store


NO_CRITERIA = {"query": None, "season": None, "gender": None, "category1": None, "category2": None, "year": None, "usage": None}


class TestHttpAdapter:
    def test_the_body_is_flat_in_parameter_names_with_null_for_criteria_not_given(self):
        assert http_body(EXAMPLE, args(gender="men")) == {**NO_CRITERIA, "gender": "Men", "limit": 3}

    def test_the_body_carries_every_canonical_argument(self):
        canonical = args(
            query=" 셔츠 ", season="summer", gender="women", category1="apparel", category2="TOPWEAR",
            year="2026", usage="casual", limit=99,
        )  # fmt: skip
        assert http_body(EXAMPLE, canonical) == {
            "query": "셔츠",
            "season": "Summer",
            "gender": "Women",
            "category1": "Apparel",
            "category2": "Topwear",
            "year": 2026,
            "usage": "Casual",
            "limit": 20,
        }

    def test_a_range_is_sent_as_min_and_max(self):
        assert http_body(PRICED, priced(price={"min": "10000", "max": 90000}))["price"] == {"min": 10000, "max": 90000}

    def test_an_open_ended_range_sends_null_for_the_open_end(self):
        assert http_body(PRICED, priced(price={"max": 50000}))["price"] == {"min": None, "max": 50000}

    def test_a_range_not_given_is_null(self):
        assert http_body(PRICED, priced(gender="men")) == {**NO_CRITERIA, "gender": "Men", "price": None, "limit": 3}

    def test_the_request_carries_the_body_and_the_credential(self, monkeypatch, catalog, secrets):
        monkeypatch.setenv("SEARCH_PROVIDER", "http")
        secrets({"header": "x-api-key", "value": "k1"})
        fake = catalog({"products": [product()]})

        assert search_products(EXAMPLE, {"query": "  shirt ", "gender": "women", "season": "SUMMER", "year": "2026", "limit": 99}) == [card()]

        [request] = fake.requests
        assert request.full_url == ENDPOINT and request.get_method() == "POST"
        assert json.loads(request.data) == {
            **NO_CRITERIA, "query": "shirt", "gender": "Women", "season": "Summer", "year": 2026, "limit": 20,
        }  # fmt: skip
        assert request.get_header("X-api-key") == "k1"
        assert request.get_header("Content-type") == "application/json"
        assert any(isinstance(handler, NoRedirect) for handler in fake.handlers)

    def test_no_secret_means_no_credential_header(self, catalog):
        fake = catalog({"products": []})
        assert providers.http_search(EXAMPLE, args(query="shirt")) == []
        assert not {"Authorization", "X-api-key"} & set(fake.requests[0].headers)

    def test_products_map_onto_cards_through_the_definition(self, monkeypatch, catalog):
        monkeypatch.setenv("SEARCH_PROVIDER", "http")
        upstream = {**product(sku=15970, year="2026"), "rating": 4.5, "images": ["a.jpg"]}
        catalog({"products": [upstream]})
        assert search_products(EXAMPLE, {"query": "shirt"}) == [card(sku="15970", year=2026)]

    def test_a_product_missing_a_card_field_is_dropped_not_fatal(self, monkeypatch, catalog):
        monkeypatch.setenv("SEARCH_PROVIDER", "http")
        catalog({"products": [product(), {"sku": "broken"}, {**product(sku="p-2"), "colour": None}]})
        dropped = []
        assert search_products(EXAMPLE, {"query": "shirt"}, on_invalid=dropped.append) == [card()]
        assert dropped == [2]

    def test_products_come_back_as_the_catalog_sent_them(self, catalog):
        catalog({"products": [{"sku": "broken"}, product()]})
        assert providers.http_search(EXAMPLE, args(query="shirt")) == [{"sku": "broken"}, product()]

    @pytest.mark.parametrize(
        "response, reason",
        [
            (b"<html>Bad gateway</html>", "not valid JSON"),
            (b"\x80not utf-8", "not valid JSON"),
            (b" " * (256 * 1024 + 1), "exceeds 256 KiB"),
            ({"products": [product()] * 21}, "more than 20 products"),
            ({"results": [product()]}, "no product list"),
            ({"products": {"sku": "p-1"}}, "no product list"),
            ({"products": None}, "no product list"),
            ([product()], "no product list"),
        ],
    )
    def test_upstream_faults_are_backend_errors(self, catalog, response, reason):
        catalog(response)
        with pytest.raises(CatalogBackendError, match=reason) as raised:
            providers.http_search(EXAMPLE, args(query="shirt"))
        # Never a ValueError, which the agent's tool would show the model as its own mistake.
        assert not isinstance(raised.value, ValueError)

    def test_twenty_products_are_accepted(self, catalog):
        catalog({"products": [product()] * 20})
        assert len(providers.http_search(EXAMPLE, args(query="shirt"))) == 20

    def test_result_path_is_followed(self, monkeypatch, catalog):
        monkeypatch.setenv("SEARCH_RESULT_PATH", "data.items")
        catalog({"data": {"items": [product()]}})
        assert providers.http_search(EXAMPLE, args(query="shirt")) == [product()]

    def test_plain_http_endpoint_is_a_backend_error(self, monkeypatch, catalog):
        monkeypatch.setenv("SEARCH_ENDPOINT", "http://catalog.example/search")
        fake = catalog()
        with pytest.raises(CatalogBackendError, match="HTTPS"):
            providers.http_search(EXAMPLE, args(query="shirt"))
        assert not fake.requests

    @pytest.mark.parametrize("status", [302, 404, 500, 503])
    def test_error_status_is_a_backend_error_without_a_credential_refetch(self, catalog, secrets, status):
        store = secrets({"header": "x-api-key", "value": "k1"})
        fake = catalog(status)
        with pytest.raises(CatalogBackendError, match=f"HTTP {status}"):
            providers.http_search(EXAMPLE, args(query="shirt"))
        assert len(fake.requests) == 1 and store.fetches == 1


class TestCredential:
    @pytest.mark.parametrize(
        "secret",
        [
            {"header": "Cookie", "value": "k1"},
            {"header": "x-api-key", "value": 7},
            {"value": "k1"},
            ["x-api-key", "k1"],
            "not json",
        ],
    )
    def test_bad_secret_is_a_backend_error_and_nothing_is_sent(self, catalog, secrets, secret):
        secrets(secret)
        fake = catalog({"products": []})
        with pytest.raises(CatalogBackendError, match="credential"):
            providers.http_search(EXAMPLE, args(query="shirt"))
        assert not fake.requests

    def test_secrets_client_uses_the_environment_region(self, secrets):
        store = secrets({"header": "Authorization", "value": "Bearer k1"})
        assert providers.credential() == {"Authorization": "Bearer k1"}
        assert store.regions == ["eu-west-1"]

    def test_no_region_fails_before_fetching(self, monkeypatch, secrets):
        store = secrets({"header": "x-api-key", "value": "k1"})
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        with pytest.raises(CatalogBackendError, match="AWS_REGION or AWS_DEFAULT_REGION"):
            providers.credential()
        assert store.fetches == 0

    def test_cached_for_its_ttl_then_fetched_again(self, monkeypatch, secrets):
        clock = [1000.0]
        monkeypatch.setattr(providers, "monotonic", lambda: clock[0])
        store = secrets({"header": "x-api-key", "value": "k1"}, {"header": "x-api-key", "value": "k2"})

        assert providers.credential() == {"x-api-key": "k1"}
        clock[0] += providers.CREDENTIAL_TTL_SECONDS - 1
        assert providers.credential() == {"x-api-key": "k1"}
        assert store.fetches == 1

        clock[0] += 1
        assert providers.credential() == {"x-api-key": "k2"}
        assert store.fetches == 2

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credential_is_fetched_again_once(self, catalog, secrets, status):
        store = secrets({"header": "x-api-key", "value": "old"}, {"header": "x-api-key", "value": "new"})
        fake = catalog(status, {"products": [product()]})

        assert providers.http_search(EXAMPLE, args(query="shirt")) == [product()]

        assert [request.get_header("X-api-key") for request in fake.requests] == ["old", "new"]
        assert store.fetches == 2
        assert providers.credential() == {"x-api-key": "new"} and store.fetches == 2

    def test_credential_rejected_again_fails_after_one_retry(self, catalog, secrets):
        store = secrets({"header": "x-api-key", "value": "k1"})
        fake = catalog(401, 403)
        with pytest.raises(CatalogBackendError, match="HTTP 403"):
            providers.http_search(EXAMPLE, args(query="shirt"))
        assert len(fake.requests) == 2 and store.fetches == 2

    def test_rejection_without_a_secret_is_not_retried(self, catalog):
        fake = catalog(401)
        with pytest.raises(CatalogBackendError, match="HTTP 401"):
            providers.http_search(EXAMPLE, args(query="shirt"))
        assert len(fake.requests) == 1
