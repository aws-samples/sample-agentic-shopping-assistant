"""The product.v1 check every provider's products go through before they reach the agent."""

import pytest

from aria_catalog.contract import PRODUCT_FIELDS, product_v1
from aria_catalog.definition import example_products, load_definition, to_card


def product(**fields):
    base = {
        "id": "p-1",
        "productDisplayName": "Cotton Shirt",
        "gender": "Women",
        "masterCategory": "Apparel",
        "subCategory": "Topwear",
        "articleType": "Shirts",
        "baseColour": "Blue",
        "season": "Summer",
        "year": 2026,
        "usage": "Casual",
    }
    return {**base, **fields}


def test_every_example_card_passes_unchanged():
    cards = [to_card(load_definition(), product) for product in example_products()]
    assert all(cards) and [product_v1(card) for card in cards] == cards


def test_extra_fields_are_left_out():
    assert product_v1({**product(), "price": 10, "_score": 0.9}) == product()


def test_numeric_id_and_whole_number_year_are_normalized():
    # Upstream catalogs often send numeric IDs and string years.
    assert product_v1(product(id=15970, year="2026")) == product(id="15970", year=2026)
    assert product_v1(product(year=2026.0))["year"] == 2026


@pytest.mark.parametrize("field", PRODUCT_FIELDS)
def test_a_missing_field_fails(field):
    assert product_v1({key: value for key, value in product().items() if key != field}) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"gender": None},
        {"usage": 3},
        {"baseColour": ["Blue"]},
        {"articleType": "x" * 2001},
        {"productDisplayName": "   "},
        {"year": "twenty"},
        {"year": "２０２６"},
        {"year": 2026.5},
        {"year": True},
        {"year": None},
        {"id": None},
        {"id": True},
        {"id": 1.5},
    ],
)
def test_wrong_field_values_fail(fields):
    assert product_v1(product(**fields)) is None


def test_text_at_the_length_limit_passes():
    assert product_v1(product(articleType="x" * 2000)) is not None


@pytest.mark.parametrize("product_id", ["", "sku 1", "a/b", "<b>", "x" * 129, "상품1", "１２３", "sample-1\n"])
def test_an_id_the_web_cannot_cite_fails(product_id):
    # The web cites only IDs matching ^[A-Za-z0-9._:-]{1,128}$.
    assert product_v1(product(id=product_id)) is None


@pytest.mark.parametrize("product_id", ["sample-1", "SKU_123", "a.b:c-d", "0", "x" * 128])
def test_an_id_the_web_can_cite_passes(product_id):
    assert product_v1(product(id=product_id))["id"] == product_id


@pytest.mark.parametrize("value", [None, "p-1", ["p-1"], 7])
def test_a_non_object_fails(value):
    assert product_v1(value) is None
