"""The packaged example catalog: its definition and its synthetic products."""

import json
import re
from pathlib import Path

import pytest

from aria_catalog.contract import product_v1
from aria_catalog.definition import (
    EXAMPLE_DIR,
    check_product,
    definition_to_dict,
    embedding_text,
    example_products,
    load_definition,
    parse_definition,
    to_card,
)

EXAMPLE = load_definition()
PRODUCTS = example_products()


def test_the_example_lives_in_the_package():
    assert EXAMPLE_DIR == Path(__file__).parents[1] / "src/aria_catalog/example"
    assert (EXAMPLE_DIR / "catalog.toml").is_file() and (EXAMPLE_DIR / "products.json").is_file()


def test_there_are_about_150_products_with_unique_ids():
    assert len(PRODUCTS) == 150
    assert len({p["sku"] for p in PRODUCTS}) == len(PRODUCTS)
    assert len({p["title"] for p in PRODUCTS}) == len(PRODUCTS)


@pytest.mark.parametrize("product", PRODUCTS, ids=[p["sku"] for p in PRODUCTS])
def test_every_example_product_passes_check_product(product):
    assert check_product(EXAMPLE, product) == []
    card = to_card(EXAMPLE, product)
    assert card is not None and product_v1(card) == card
    assert embedding_text(EXAMPLE, product).startswith(product["title"])


def test_every_product_has_every_field():
    names = {f.name for f in EXAMPLE.fields}
    assert all(set(product) == names for product in PRODUCTS)


def test_ids_are_the_samples_then_nordly_numbers():
    assert [p["sku"] for p in PRODUCTS[:3]] == ["sample-1", "sample-2", "sample-3"]
    assert all(re.fullmatch(r"nordly-\d{3}", p["sku"]) for p in PRODUCTS[3:])


def test_there_is_no_price():
    # Cards (product.v1) carry no price, and the agent tells shoppers prices aren't available.
    assert "price" not in {f.name for f in EXAMPLE.fields}
    assert not any("price" in p for p in PRODUCTS)


def test_the_catalog_is_varied():
    for name, least in (("gender", 5), ("category", 3), ("subcategory", 20), ("season", 4), ("usage", 7), ("year", 4), ("colour", 15)):
        assert len({p[name] for p in PRODUCTS}) >= least, name
    assert all(re.search(r"[가-힣]", p["title"]) for p in PRODUCTS[3:])
    assert all(re.search(r"[A-Za-z]", p["title"]) for p in PRODUCTS)


def test_the_example_definition_round_trips():
    assert parse_definition(json.loads(json.dumps(definition_to_dict(EXAMPLE)))) == EXAMPLE
