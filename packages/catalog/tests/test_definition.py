"""The catalog definition: validation, serialization, the tool schema, arguments, index and query bodies, cards."""

import copy
import json
import math

import pytest

from aria_catalog.contract import PRODUCT_FIELDS
from aria_catalog.definition import (
    EMBEDDING_FIELD,
    CatalogDefinition,
    CatalogField,
    ENVIRONMENT_PART_BYTES,
    MAX_ENVIRONMENT_PARTS,
    DefinitionError,
    SearchArguments,
    canonical_arguments,
    check_product,
    definition_environment,
    definition_from_env,
    definition_to_dict,
    embedding_text,
    index_body,
    is_empty,
    load_definition,
    parse_definition,
    pipeline_body,
    pipeline_name,
    search_body,
    source_fields,
    to_card,
    tool_spec,
)
from aria_catalog.errors import CatalogQueryError

EXAMPLE = load_definition()

CATEGORIES = ["Apparel", "Accessories", "Footwear", "Personal Care", "Free Items", "Sporting Goods", "Home"]
SUBCATEGORIES = [
    "Topwear", "Shoes", "Bags", "Bottomwear", "Watches", "Innerwear", "Jewellery", "Eyewear", "Fragrance",
    "Sandal", "Wallets", "Flip Flops", "Belts", "Socks", "Lips", "Dress", "Loungewear and Nightwear", "Saree",
    "Nails", "Makeup", "Headwear", "Ties", "Accessories", "Scarves", "Cufflinks", "Apparel Set", "Free Gifts",
    "Stoles", "Skin Care", "Skin", "Eyes", "Mufflers", "Shoe Accessories", "Sports Equipment", "Gloves", "Hair",
    "Bath and Body", "Water Bottle", "Perfumes", "Umbrellas", "Beauty Accessories", "Wristbands",
    "Sports Accessories", "Home Furnishing", "Vouchers",
]  # fmt: skip
USAGES = ["Casual", "Smart Casual", "Formal", "Sports", "Travel", "Party", "Home"]
SOURCE = ["sku", "title", "gender", "category", "subcategory", "type", "colour", "season", "year", "usage"]


def minimal() -> dict:
    """A small valid definition in the TOML shape; each validation test breaks one rule of it."""
    return {
        "catalog": {
            "name": "Shop",
            "description": "Search the shop.",
            "idField": "id",
            "fields": [
                {"name": "id", "type": "keyword"},
                {"name": "title", "type": "text", "search": True},
                {"name": "kind", "type": "keyword", "filter": True, "values": ["A", "B"]},
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


def broken(change) -> dict:
    data = minimal()
    change(data["catalog"])
    return data


def field(catalog, name) -> dict:
    return next(entry for entry in catalog["fields"] if entry["name"] == name)


def add(**entry):
    return lambda catalog: catalog["fields"].append(entry)


def set_field(name, **values):
    return lambda catalog: field(catalog, name).update(values)


def drop_field_key(name, key):
    return lambda catalog: field(catalog, name).pop(key)


def filters(count):
    return lambda catalog: catalog["fields"].extend(
        {"name": f"f{n}", "type": "integer", "filter": True} for n in range(count)
    )


# --- Validation ---


def test_the_minimal_definition_parses():
    d = parse_definition(minimal())
    assert d.name == "Shop" and d.id_field == "id" and d.analyzer == "standard"
    assert d.embedding_model == "global.cohere.embed-v4:0" and d.k == 50
    assert [f.name for f in d.fields] == ["id", "title", "kind", "year"]
    assert d.fields[2] == CatalogField(name="kind", type="keyword", filter="exact", values=("A", "B"))
    assert d.fields[2].parameter == "kind"


@pytest.mark.parametrize(
    "change, where, rule",
    [
        # Field and parameter names
        (add(name="title", type="keyword"), "catalog.fields.title", "defined twice"),
        (add(name="bad-name", type="keyword"), r"catalog.fields\[4\].name", "must match"),
        (add(name="1st", type="keyword"), r"catalog.fields\[4\].name", "must match"),
        (add(name="x" * 65, type="keyword"), r"catalog.fields\[4\].name", "must match"),
        (add(type="keyword"), r"catalog.fields\[4\].name", "is required"),
        (add(name="_id", type="keyword"), "catalog.fields._id.name", "underscore"),
        (add(name=EMBEDDING_FIELD, type="text"), "catalog.fields.embedding.name", "vector field"),
        (set_field("year", filter=True, parameter="kind"), "catalog.fields.year.parameter", "another filter"),
        (set_field("kind", parameter="bad name"), "catalog.fields.kind.parameter", "must match"),
        (set_field("kind", parameter="query"), "catalog.fields.kind.parameter", "reserved"),
        (set_field("kind", parameter="limit"), "catalog.fields.kind.parameter", "reserved"),
        (add(name="query", type="integer", filter=True), "catalog.fields.query.parameter", "reserved"),
        (set_field("year", parameter="released"), "catalog.fields.year.parameter", "only to a filter"),
        # Types and filters
        (set_field("year", type="date"), "catalog.fields.year.type", "must be one of"),
        (drop_field_key("year", "type"), "catalog.fields.year.type", "must be one of"),
        (drop_field_key("kind", "values"), "catalog.fields.kind.values", "must list its values"),
        (set_field("kind", values=[]), "catalog.fields.kind.values", "must list its values"),
        (set_field("kind", values="A"), "catalog.fields.kind.values", "must list its values"),
        (set_field("kind", values=["A", 2]), "catalog.fields.kind.values", "non-blank text"),
        (set_field("kind", values=["A", " "]), "catalog.fields.kind.values", "non-blank text"),
        (set_field("kind", values=["A", "B "]), "catalog.fields.kind.values", "surrounding spaces"),
        (set_field("kind", values=[f"v{n}" for n in range(101)]), "catalog.fields.kind.values", "at most 100 values"),
        (set_field("kind", values=["A", "x" * 101]), "catalog.fields.kind.values", "at most 100 characters"),
        (set_field("kind", values=["Men", "men"]), "catalog.fields.kind.values", "ignoring case"),
        (set_field("year", values=[2024]), "catalog.fields.year.values", "only a keyword field with filter"),
        (set_field("kind", filter=False), "catalog.fields.kind.values", "only a keyword field with filter"),
        (set_field("kind", filter="range"), "catalog.fields.kind.filter", "integer or float"),
        (add(name="flag", type="boolean", filter="range"), "catalog.fields.flag.filter", "integer or float"),
        (set_field("title", filter=True), "catalog.fields.title.filter", "text field can't be a filter"),
        (set_field("year", filter="between"), "catalog.fields.year.filter", 'true .* or "range"'),
        (set_field("year", filter=1), "catalog.fields.year.filter", 'true .* or "range"'),
        # Search and boost
        (set_field("year", search=True), "catalog.fields.year.search", "only text and keyword"),
        (set_field("title", search="yes"), "catalog.fields.title.search", "true or false"),
        (set_field("title", boost=0), "catalog.fields.title.boost", "greater than 0"),
        (set_field("title", boost=-1), "catalog.fields.title.boost", "greater than 0"),
        (set_field("title", boost=10.5), "catalog.fields.title.boost", "at most 10"),
        (set_field("title", boost=True), "catalog.fields.title.boost", "must be a number"),
        (set_field("title", boost="3"), "catalog.fields.title.boost", "must be a number"),
        (set_field("kind", boost=2), "catalog.fields.kind.boost", "search = true"),
        (set_field("title", search=False), "catalog.fields", "at least one field needs search"),
        (set_field("title", description=3), "catalog.fields.title.description", "must be text"),
        (filters(12), "catalog.fields", "at most 12 filters"),
        (lambda c: c.update(fields=[]), "catalog.fields", "at least one"),
        (lambda c: c.update(fields={"name": "id"}), "catalog.fields", "at least one"),
        (lambda c: c["fields"].append("id"), r"catalog.fields\[4\]", "must be a table"),
        # The embedding
        (lambda c: c.pop("embedding"), "catalog.embedding", "is required"),
        (lambda c: c.update(embedding={}), "catalog.embedding.text", "is required"),
        (lambda c: c.update(embedding={"text": "a product"}), "catalog.embedding.text", "at least one"),
        (lambda c: c.update(embedding={"text": "{name}"}), "catalog.embedding.text", "{name} doesn't name"),
        (lambda c: c.update(embedding={"text": "{title.en}"}), "catalog.embedding.text", "doesn't name"),
        (lambda c: c.update(embedding={"text": "{}"}), "catalog.embedding.text", "must name a field"),
        (lambda c: c.update(embedding={"text": "{title!r}"}), "catalog.embedding.text", "no format spec"),
        (lambda c: c.update(embedding={"text": "{title:>9}"}), "catalog.embedding.text", "no format spec"),
        (lambda c: c.update(embedding={"text": "{title"}), "catalog.embedding.text", "not a valid template"),
        *(
            (lambda c, m=model: c["embedding"].update(model=m), "catalog.embedding.model", "must match")
            for model in ("cohere.embed-v3", "amazon.titan-embed-text-v2:0", "cohere.embed-v4:1", "ap.cohere.embed-v4:0", "apac.cohere.embed-v4:0", 4)
        ),
        # The card
        (lambda c: c["card"].pop("usage"), "catalog.card", "missing usage"),
        (lambda c: c["card"].update(price="year"), "catalog.card", "unknown card field 'price'"),
        (lambda c: c["card"].update(gender="sex"), "catalog.card.gender", "must name a defined field"),
        (lambda c: c["card"].update(gender=["kind"]), "catalog.card.gender", "must name a defined field"),
        (lambda c: c["card"].update(gender="year"), "catalog.card.gender", "keyword or text"),
        (lambda c: c["card"].update(year="title"), "catalog.card.year", "integer or keyword"),
        (lambda c: c.update(card={**c["card"], "id": "kind"}), "catalog.card.id", "must be the idField"),
        (lambda c: c.pop("card"), "catalog.card", "is required"),
        # The catalog table
        (lambda c: c.pop("idField"), "catalog.idField", "must name a defined field"),
        (lambda c: c.update(idField="sku"), "catalog.idField", "must name a defined field"),
        (lambda c: c.update(idField="title"), "catalog.idField", "keyword or integer"),
        (lambda c: c.pop("name"), "catalog.name", "is required"),
        (lambda c: c.update(description="  "), "catalog.description", "is required"),
        (lambda c: c.update(analyzer="english"), "catalog.analyzer", "standard, nori"),
        (lambda c: c.update(search={"k": 0}), "catalog.search.k", "from 1 to 100"),
        (lambda c: c.update(search={"k": 101}), "catalog.search.k", "from 1 to 100"),
        (lambda c: c.update(search={"k": 1.5}), "catalog.search.k", "from 1 to 100"),
        (lambda c: c.update(search={"k": True}), "catalog.search.k", "from 1 to 100"),
        (lambda c: c.update(search=50), "catalog.search", "must be a table"),
        # Unknown keys are typos, not ignored
        (lambda c: c.update(idfield="id"), "catalog", "unknown key 'idfield'"),
        (set_field("year", fliter="range"), "catalog.fields.year", "unknown key 'fliter'"),
        (set_field("title", embed=True), "catalog.fields.title", "unknown key 'embed'"),
        (lambda c: c["embedding"].update(dimension=512), "catalog.embedding", "unknown key 'dimension'"),
        (lambda c: c.update(search={"k": 50, "size": 3}), "catalog.search", "unknown key 'size'"),
    ],
)
def test_a_broken_rule_names_the_field_and_the_rule(change, where, rule):
    with pytest.raises(DefinitionError, match=rf"^{where}: .*{rule}"):
        parse_definition(broken(change))


@pytest.mark.parametrize("data", [None, [], "catalog", {}, {"catalog": []}, {"catalog": {}, "extra": {}}])
def test_a_definition_needs_a_catalog_table(data):
    with pytest.raises(DefinitionError):
        parse_definition(data)


def test_twelve_filters_are_allowed():
    d = parse_definition(broken(filters(11)))
    assert sum(1 for f in d.fields if f.filter) == 12


@pytest.mark.parametrize(
    "model", ["cohere.embed-v4:0", "global.cohere.embed-v4:0", "us.cohere.embed-v4:0", "eu.cohere.embed-v4:0"]
)
def test_every_embed_v4_profile_is_accepted(model):
    assert parse_definition(broken(lambda c: c["embedding"].update(model=model))).embedding_model == model


@pytest.mark.parametrize("value, kind", [(True, "exact"), ("exact", "exact"), ("range", "range"), (False, None)])
def test_filter_spellings(value, kind):
    assert parse_definition(broken(set_field("year", filter=value))).fields[3].filter == kind


def test_numeric_and_boolean_exact_filters_are_allowed():
    d = parse_definition(broken(lambda c: c["fields"].extend([
        {"name": "rating", "type": "float", "filter": True},
        {"name": "onSale", "type": "boolean", "filter": True},
        {"name": "weight", "type": "float", "filter": "range"},
    ])))  # fmt: skip
    assert [(f.name, f.filter) for f in d.fields[4:]] == [("rating", "exact"), ("onSale", "exact"), ("weight", "range")]


def test_a_filter_named_query_can_take_another_parameter():
    d = parse_definition(broken(add(name="query", type="keyword", filter=True, values=["x"], parameter="q")))
    assert d.fields[4].parameter == "q"


def test_a_keyword_card_year_and_an_integer_id_are_allowed():
    def change(catalog):
        field(catalog, "id")["type"] = "integer"
        catalog["fields"].append({"name": "released", "type": "keyword"})
        catalog["card"]["year"] = "released"

    d = parse_definition(broken(change))
    assert d.card["year"] == "released"


def test_descriptions_and_texts_are_trimmed():
    d = parse_definition(broken(lambda c: (c.update(name=" Shop "), set_field("kind", description=" Kind. ")(c))))
    assert d.name == "Shop" and d.fields[2].description == "Kind."


# --- Loading and serializing ---


def test_the_packaged_example_loads():
    assert EXAMPLE.name == "NORDLY" and EXAMPLE.analyzer == "nori" and EXAMPLE.id_field == "sku"
    assert [f.name for f in EXAMPLE.fields] == [
        "sku", "title", "description", "season", "gender", "category", "subcategory", "type", "colour", "year",
        "usage",
    ]  # fmt: skip
    # Cards carry no price, so the example has no price filter; ranges are tested on PRICED below.
    assert not any(f.filter == "range" for f in EXAMPLE.fields)
    assert [(f.name, f.boost) for f in EXAMPLE.fields if f.search] == [("title", 3.0), ("description", 1.0)]
    assert EXAMPLE.embedding_text == "{title}. {description}" and EXAMPLE.k == 50
    assert load_definition() is EXAMPLE


def full_definition() -> CatalogDefinition:
    """A definition that uses every option, for round-tripping."""

    def change(catalog):
        catalog.update(analyzer="nori", search={"k": 20})
        catalog["embedding"].update(text="{title} {{note}} {kind}", model="us.cohere.embed-v4:0")
        field(catalog, "title").update(boost=2.5, description="The name")
        field(catalog, "kind").update(parameter="category1", description="Kind")
        field(catalog, "year").update(filter=True)
        catalog["fields"].extend([
            {"name": "notes", "type": "keyword", "search": True},
            {"name": "price", "type": "float", "filter": "range", "description": "Price"},
            {"name": "onSale", "type": "boolean", "filter": True},
        ])  # fmt: skip

    return parse_definition(broken(change))


def priced_definition() -> CatalogDefinition:
    """The example with a range filter added: an integer `price` in KRW."""
    data = definition_to_dict(EXAMPLE)
    data["catalog"]["fields"].append({"name": "price", "type": "integer", "filter": "range", "description": "Price in KRW"})
    return parse_definition(data)


PRICED = priced_definition()


@pytest.mark.parametrize(
    "definition",
    [EXAMPLE, parse_definition(minimal()), full_definition(), PRICED],
    ids=["example", "minimal", "full", "priced"],
)
def test_definition_to_dict_round_trips_through_json(definition):
    data = definition_to_dict(definition)
    assert parse_definition(json.loads(json.dumps(data))) == definition


def test_definition_to_dict_is_compact():
    data = definition_to_dict(parse_definition(minimal()))
    assert data["catalog"]["fields"] == [
        {"name": "id", "type": "keyword"},
        {"name": "title", "type": "text", "search": True},
        {"name": "kind", "type": "keyword", "filter": True, "values": ["A", "B"]},
        {"name": "year", "type": "integer"},
    ]
    assert definition_to_dict(EXAMPLE)["catalog"]["fields"][6] == {
        "name": "subcategory",
        "type": "keyword",
        "filter": True,
        "values": SUBCATEGORIES,
        "description": EXAMPLE.fields[6].description,
        "parameter": "category2",
    }


def test_definition_from_env_reads_the_json_and_defaults_to_the_example():
    custom = parse_definition(minimal())
    assert definition_from_env({"CATALOG_DEFINITION": json.dumps(definition_to_dict(custom))}) == custom
    assert definition_from_env({}) is EXAMPLE
    assert definition_from_env({"CATALOG_DEFINITION": "  "}) is EXAMPLE


def compact(definition: CatalogDefinition) -> str:
    return json.dumps(definition_to_dict(definition), separators=(",", ":"), ensure_ascii=False)


def test_a_short_definition_is_one_value():
    text = compact(parse_definition(minimal()))
    assert definition_environment(text) == {"CATALOG_DEFINITION": text}
    assert definition_environment("") == {"CATALOG_DEFINITION": ""}


def test_the_example_is_split_into_values_the_runtime_takes_and_joined_back():
    environment = definition_environment(compact(EXAMPLE))
    assert list(environment) == ["CATALOG_DEFINITION", "CATALOG_DEFINITION_2"]
    assert all(len(part.encode("utf-8")) <= ENVIRONMENT_PART_BYTES for part in environment.values())
    assert definition_from_env(environment) == EXAMPLE


def test_korean_text_is_split_by_bytes_and_never_next_to_a_space():
    raw = minimal()
    raw["catalog"]["fields"][1]["description"] = "한국어 설명 " * 300  # 3 bytes a character in UTF-8
    custom = parse_definition(raw)
    environment = definition_environment(compact(custom))
    assert len(environment) > 2
    for part in environment.values():
        assert len(part.encode("utf-8")) <= ENVIRONMENT_PART_BYTES
        assert part == part.strip()
    assert definition_from_env(environment) == custom


def test_a_definition_too_big_for_the_runtime_is_a_definition_error():
    text = "x" * (ENVIRONMENT_PART_BYTES * MAX_ENVIRONMENT_PARTS)
    assert len(definition_environment(text)) == MAX_ENVIRONMENT_PARTS
    with pytest.raises(DefinitionError, match="more than the 8 environment values of 2048 bytes"):
        definition_environment(text + "x")


def test_parts_are_read_in_order_up_to_the_first_gap():
    environment = definition_environment(compact(EXAMPLE))
    assert definition_from_env({**environment, "CATALOG_DEFINITION_4": "ignored"}) == EXAMPLE
    with pytest.raises(DefinitionError, match="not valid JSON"):
        definition_from_env({"CATALOG_DEFINITION": environment["CATALOG_DEFINITION"]})


@pytest.mark.parametrize("raw, match", [("{not json", "not valid JSON"), ('{"catalog": {}}', "catalog.name")])
def test_a_bad_definition_in_the_environment_is_a_definition_error(raw, match):
    with pytest.raises(DefinitionError, match=match):
        definition_from_env({"CATALOG_DEFINITION": raw})


SHOP_TOML = """
[catalog]
name = "Shop"
description = "Search the shop."
idField = "id"

[[catalog.fields]]
name = "id"
type = "keyword"

[[catalog.fields]]
name = "title"
type = "text"
search = true

[[catalog.fields]]
name = "kind"
type = "keyword"
filter = true
values = ["A", "B"]

[[catalog.fields]]
name = "year"
type = "integer"

[catalog.embedding]
text = "{title}"

[catalog.card]
id = "id"
productDisplayName = "title"
gender = "kind"
masterCategory = "kind"
subCategory = "kind"
articleType = "kind"
baseColour = "kind"
season = "kind"
year = "year"
usage = "kind"
"""


def test_load_definition_reads_a_toml_file(tmp_path):
    source = tmp_path / "catalog.toml"
    source.write_text(SHOP_TOML, encoding="utf-8")
    assert load_definition(source) == parse_definition(minimal())
    assert load_definition(str(source)) == parse_definition(minimal())


def test_invalid_toml_is_a_definition_error(tmp_path):
    source = tmp_path / "catalog.toml"
    source.write_text("[catalog\nname = ", encoding="utf-8")
    with pytest.raises(DefinitionError, match="not valid TOML"):
        load_definition(source)


# --- The tool spec ---

EXAMPLE_TOOL_SPEC = {
    "name": "search_products",
    "description": (
        "Search fashion products in the NORDLY catalog. Use this tool only for searching products.\n"
        "Prefer the filters to free text, in this order: season, gender, category1, category2, year, usage. "
        "Use query only for what they leave out."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": (
                        "Free text for what the filters don't cover. It's matched by keyword and by meaning, "
                        "so a short description of the item works best. At most 200 characters."
                    ),
                },
                "season": {
                    "type": "string",
                    "enum": ["Summer", "Fall", "Winter", "Spring"],
                    "description": "Target season. One of: Summer, Fall, Winter, Spring",
                },
                "gender": {
                    "type": "string",
                    "enum": ["Men", "Women", "Unisex", "Boys", "Girls"],
                    "description": "Target gender. One of: Men, Women, Unisex, Boys, Girls",
                },
                "category1": {
                    "type": "string",
                    "enum": CATEGORIES,
                    "description": "Top-level category. One of: " + ", ".join(CATEGORIES),
                },
                "category2": {
                    "type": "string",
                    "enum": SUBCATEGORIES,
                    "description": (
                        "Sub-category within category1. Jackets and coats are Topwear; sneakers, running shoes and "
                        "boots are Shoes. One of: " + ", ".join(SUBCATEGORIES)
                    ),
                },
                "year": {"type": "integer", "description": "Release year, e.g. 2025."},
                "usage": {
                    "type": "string",
                    "enum": USAGES,
                    "description": "Occasion the product is made for. One of: " + ", ".join(USAGES),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 3,
                    "description": "Max results to return (default 3, max 20).",
                },
            },
            "required": [],
        }
    },
}


def test_example_tool_spec_snapshot():
    spec = tool_spec(EXAMPLE)
    assert spec == EXAMPLE_TOOL_SPEC
    # Definition order, between query and limit.
    assert list(spec["inputSchema"]["json"]["properties"]) == [
        "query", "season", "gender", "category1", "category2", "year", "usage", "limit",
    ]  # fmt: skip


def test_a_range_filter_is_an_object_parameter_with_min_and_max():
    spec = tool_spec(PRICED)
    assert spec["inputSchema"]["json"]["properties"]["price"] == {
        "type": "object",
        "description": "Price in KRW. An object with min, max or both.",
        "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
        "additionalProperties": False,
    }
    assert list(spec["inputSchema"]["json"]["properties"])[-2:] == ["price", "limit"]
    assert "in this order: season, gender, category1, category2, year, usage, price." in spec["description"]


def test_tool_spec_parameter_shapes():
    properties = tool_spec(full_definition())["inputSchema"]["json"]["properties"]
    assert properties["category1"] == {"type": "string", "enum": ["A", "B"], "description": "Kind. One of: A, B"}
    assert properties["year"] == {"type": "integer"}
    assert properties["onSale"] == {"type": "boolean"}
    assert properties["price"]["type"] == "object" and properties["price"]["description"].startswith("Price. ")
    assert "kind" not in properties and "notes" not in properties and "title" not in properties


def test_a_keyword_filter_without_a_description_still_lists_its_values():
    properties = tool_spec(parse_definition(minimal()))["inputSchema"]["json"]["properties"]
    assert properties["kind"]["description"] == "One of: A, B"


# --- Arguments ---


def args(**arguments) -> SearchArguments:
    return canonical_arguments(EXAMPLE, arguments)


def priced(**arguments) -> SearchArguments:
    return canonical_arguments(PRICED, arguments)


def test_canonical_arguments_hold_field_names_in_definition_order():
    canonical = priced(limit=5, category2="Topwear", gender="Men", season="Winter", query=" 따뜻한 자켓 ", price={"max": 200000})
    assert canonical == SearchArguments(
        query="따뜻한 자켓",
        filters=(("season", "Winter"), ("gender", "Men"), ("subcategory", "Topwear")),
        ranges=(("price", None, 200000),),
        limit=5,
    )


@pytest.mark.parametrize(
    "value, canonical", [("men", "Men"), ("MEN", "Men"), (" Women ", "Women"), ("unisex", "Unisex")]
)
def test_enum_values_match_ignoring_case_and_come_back_canonical(value, canonical):
    assert args(gender=value).filters == (("gender", canonical),)


def test_a_multiword_enum_value_matches_ignoring_case():
    assert args(category2="flip FLOPS").filters == (("subcategory", "Flip Flops"),)


def test_every_declared_value_is_accepted():
    for f in EXAMPLE.fields:
        for value in f.values:
            assert canonical_arguments(EXAMPLE, {f.parameter: value}).filters == ((f.name, value),)


def test_an_enum_mismatch_lists_the_allowed_values():
    with pytest.raises(CatalogQueryError, match="invalid gender 'Martian'; expected one of: Men, Women, Unisex, Boys, Girls"):
        args(gender="Martian")


def test_an_unknown_parameter_lists_the_accepted_ones():
    expected = "query, season, gender, category1, category2, year, usage, limit"
    with pytest.raises(CatalogQueryError, match=f"unknown parameter 'name'; expected one of: {expected}"):
        args(name="jacket")
    with pytest.raises(CatalogQueryError, match="unknown parameter 'category'"):
        args(category="Apparel")  # The field name is not the parameter.
    with pytest.raises(CatalogQueryError, match="unknown parameter 'price'"):
        args(price={"max": 50000})  # The example has no price filter.


@pytest.mark.parametrize(
    "arguments, match",
    [
        ({"gender": 5}, "gender must be a string"),
        ({"gender": ["Men"]}, "gender must be a string"),
        ({"year": "soon"}, "invalid year 'soon'; expected a whole number"),
        ({"year": 2024.5}, "expected a whole number"),
        ({"year": True}, "expected a whole number"),
        ({"year": math.inf}, "expected a whole number"),
        ({"query": 7}, "query must be a string"),
        ({"query": "x" * 201}, "longer than 200 characters"),
        ({"limit": 0}, "invalid limit 0; expected a whole number from 1 to 20"),
        ({"limit": -3}, "invalid limit"),
        ({"limit": "many"}, "invalid limit"),
        ({"limit": 2.5}, "invalid limit"),
        ({"limit": True}, "invalid limit"),
        ([("gender", "Men")], "must be an object"),
    ],
)
def test_arguments_the_model_can_fix_are_query_errors(arguments, match):
    with pytest.raises(CatalogQueryError, match=match):
        canonical_arguments(EXAMPLE, arguments)


@pytest.mark.parametrize(
    "price, match",
    [
        (50000, "price must be an object with min, max or both"),
        ("cheap", "price must be an object"),
        ({"below": 5}, "price takes only min and max"),
        ({"min": "a lot"}, "invalid price.min 'a lot'; expected a number"),
        ({"max": True}, "invalid price.max"),
        ({"max": math.nan}, "invalid price.max"),
        ({"min": 90000, "max": 10000}, "price.min is greater than price.max"),
    ],
)
def test_range_arguments_the_model_can_fix_are_query_errors(price, match):
    with pytest.raises(CatalogQueryError, match=match):
        priced(price=price)


def test_a_long_bad_value_is_shortened_in_the_message():
    with pytest.raises(CatalogQueryError) as raised:
        args(gender="x" * 500)
    assert len(str(raised.value)) < 200


@pytest.mark.parametrize(
    "value, expected", [("2024", 2024), (" 2024 ", 2024), (2024, 2024), (2024.0, 2024)]
)
def test_a_whole_number_may_arrive_as_text_or_float(value, expected):
    assert args(year=value).filters == (("year", expected),)
    assert type(args(year=value).filters[0][1]) is int


@pytest.mark.parametrize(
    "price, bounds",
    [
        ({"min": 50000, "max": 100000}, (50000, 100000)),
        ({"max": "90000"}, (None, 90000)),
        ({"min": 1.5}, (1.5, None)),
        ({"min": 7, "max": 7}, (7, 7)),
        ({"min": None, "max": " "}, None),
        ({}, None),
        (None, None),
        ("", None),
    ],
)
def test_range_arguments(price, bounds):
    assert priced(price=price).ranges == (() if bounds is None else (("price", *bounds),))


@pytest.mark.parametrize("limit, size", [(None, 3), ("", 3), (1, 1), (5, 5), ("7", 7), (20, 20), (21, 20), (999, 20)])
def test_limit_defaults_to_three_and_is_capped_at_twenty(limit, size):
    assert args(query="x", limit=limit).limit == size


def test_a_query_of_200_characters_is_accepted():
    assert args(query="x" * 200).query == "x" * 200


@pytest.mark.parametrize(
    "arguments",
    [{}, {"query": "   "}, {"query": "\t\n"}, {"gender": " "}, {"year": " "}, {"query": " ", "season": ""}, {"price": {}}, {"limit": 5}],
)
def test_blank_values_are_no_criteria(arguments):
    assert is_empty(canonical_arguments(PRICED, arguments))


def test_none_arguments_are_empty():
    assert is_empty(canonical_arguments(EXAMPLE, None))
    assert canonical_arguments(EXAMPLE, None).limit == 3


@pytest.mark.parametrize("arguments", [{"query": "jacket"}, {"gender": "Men"}, {"price": {"max": 1}}])
def test_any_one_criterion_is_a_search(arguments):
    assert not is_empty(canonical_arguments(PRICED, arguments))


def test_boolean_and_float_exact_filters():
    d = full_definition()
    canonical = canonical_arguments(d, {"onSale": "TRUE", "year": "2024", "price": {"min": "9.5"}})
    assert canonical.filters == (("year", 2024), ("onSale", True))
    assert canonical.ranges == (("price", 9.5, None),)
    assert canonical_arguments(d, {"onSale": False}).filters == (("onSale", False),)
    with pytest.raises(CatalogQueryError, match="invalid onSale 'yes'; expected true or false"):
        canonical_arguments(d, {"onSale": "yes"})


# --- The index, the pipeline and the query ---

EXAMPLE_INDEX_BODY = {
    "settings": {"index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 2, "refresh_interval": "10s"}},
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "sku": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "nori"},
            "description": {"type": "text", "analyzer": "nori"},
            "season": {"type": "keyword"},
            "gender": {"type": "keyword"},
            "category": {"type": "keyword"},
            "subcategory": {"type": "keyword"},
            "type": {"type": "keyword"},
            "colour": {"type": "keyword"},
            "year": {"type": "integer"},
            "usage": {"type": "keyword"},
            "embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "engine": "faiss",
                    "space_type": "cosinesimil",
                    "parameters": {"m": 16, "ef_construction": 128},
                },
            },
        },
    },
}


def test_example_index_body_snapshot():
    assert index_body(EXAMPLE, replicas=2) == EXAMPLE_INDEX_BODY


def test_index_body_maps_every_type_and_the_standard_analyzer():
    properties = index_body(full_definition(), replicas=0)["mappings"]["properties"]
    assert properties["title"] == {"type": "text", "analyzer": "nori"}
    assert properties["price"] == {"type": "float"} and properties["onSale"] == {"type": "boolean"}
    assert index_body(parse_definition(minimal()), 0)["mappings"]["properties"]["title"]["analyzer"] == "standard"
    assert index_body(EXAMPLE, 0)["settings"]["index"]["number_of_replicas"] == 0
    assert index_body(PRICED, 0)["mappings"]["properties"]["price"] == {"type": "integer"}


def test_pipeline_snapshot():
    assert pipeline_name("products") == "products-hybrid"
    assert pipeline_body() == {
        "description": "Fuses the catalog's lexical and vector results by reciprocal rank fusion",
        "phase_results_processors": [{"score-ranker-processor": {"combination": {"technique": "rrf"}}}],
    }


def test_source_fields_are_the_card_fields():
    assert source_fields(EXAMPLE) == SOURCE
    assert source_fields(parse_definition(minimal())) == ["id", "title", "kind", "year"]


MULTI_MATCH = {"multi_match": {"query": "따뜻한 자켓", "type": "best_fields", "fields": ["title^3", "description^1"]}}


def test_search_body_lexical_query_only():
    assert search_body(EXAMPLE, args(query="따뜻한 자켓")) == {
        "query": {"bool": {"filter": [], "must": [MULTI_MATCH]}},
        "size": 3,
        "_source": SOURCE,
    }


def test_search_body_filters_and_ranges_without_a_query():
    canonical = priced(gender="men", year="2025", price={"min": 50000, "max": 150000}, limit=10)
    assert search_body(PRICED, canonical, vector=[0.5]) == {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"gender": "Men"}},
                    {"term": {"year": 2025}},
                    {"range": {"price": {"gte": 50000, "lte": 150000}}},
                ]
            }
        },
        "size": 10,
        "_source": SOURCE,
    }


def test_search_body_hybrid():
    canonical = priced(query="따뜻한 자켓", gender="men", season="Winter", price={"max": 200000}, limit=5)
    filters = [{"term": {"season": "Winter"}}, {"term": {"gender": "Men"}}, {"range": {"price": {"lte": 200000}}}]
    assert search_body(PRICED, canonical, vector=[0.1, 0.2]) == {
        "query": {
            "hybrid": {
                "queries": [
                    {"bool": {"filter": filters, "must": [MULTI_MATCH]}},
                    {"knn": {"embedding": {"vector": [0.1, 0.2], "k": 50, "filter": {"bool": {"filter": filters}}}}},
                ],
                "pagination_depth": 50,
            }
        },
        "size": 5,
        "_source": SOURCE,
    }


def test_search_body_hybrid_without_filters_has_an_unfiltered_knn():
    body = search_body(EXAMPLE, args(query="따뜻한 자켓"), vector=[0.1])
    assert body["query"]["hybrid"]["queries"][1] == {"knn": {"embedding": {"vector": [0.1], "k": 50}}}


def test_pagination_depth_covers_the_page_when_k_is_small():
    d = parse_definition(broken(lambda c: c.update(search={"k": 5})))
    body = search_body(d, canonical_arguments(d, {"query": "x", "limit": 20}), vector=[0.1])
    assert body["query"]["hybrid"]["pagination_depth"] == 20
    assert body["query"]["hybrid"]["queries"][1]["knn"]["embedding"]["k"] == 5


def test_search_body_formats_fractional_boosts():
    body = search_body(full_definition(), canonical_arguments(full_definition(), {"query": "x"}))
    assert body["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["title^2.5", "notes^1"]


# --- Products ---

PRODUCT = {
    "sku": "nordly-900",
    "title": "겨울 자켓 Winter Jacket",
    "description": "따뜻한 남성 자켓",
    "season": "Winter",
    "gender": "Men",
    "category": "Apparel",
    "subcategory": "Topwear",
    "type": "Jackets",
    "colour": "Blue",
    "year": 2026,
    "usage": "Casual",
}
CARD = {
    "id": "nordly-900",
    "productDisplayName": "겨울 자켓 Winter Jacket",
    "gender": "Men",
    "masterCategory": "Apparel",
    "subCategory": "Topwear",
    "articleType": "Jackets",
    "baseColour": "Blue",
    "season": "Winter",
    "year": 2026,
    "usage": "Casual",
}


def without(key):
    return {k: v for k, v in PRODUCT.items() if k != key}


def test_to_card_maps_the_catalog_fields_onto_product_v1():
    assert to_card(EXAMPLE, PRODUCT) == CARD
    assert list(to_card(EXAMPLE, PRODUCT)) == list(PRODUCT_FIELDS)


def test_to_card_normalizes_a_numeric_id_and_year():
    assert to_card(EXAMPLE, {**PRODUCT, "sku": 15970, "year": "2026"}) == {**CARD, "id": "15970", "year": 2026}


@pytest.mark.parametrize(
    "source",
    [without("colour"), without("sku"), {**PRODUCT, "sku": "bad id"}, {**PRODUCT, "title": " "}, {**PRODUCT, "usage": None}, "nordly-900", None],
)
def test_to_card_is_none_for_a_product_that_does_not_qualify(source):
    assert to_card(EXAMPLE, source) is None


def test_to_card_does_not_need_the_fields_outside_the_card():
    assert to_card(EXAMPLE, without("description")) == CARD
    assert to_card(PRICED, PRODUCT) == CARD  # A product without a price still makes a card.


@pytest.mark.parametrize(
    "product, text",
    [
        (PRODUCT, "겨울 자켓 Winter Jacket. 따뜻한 남성 자켓"),
        (without("description"), "겨울 자켓 Winter Jacket."),
        ({**PRODUCT, "description": None}, "겨울 자켓 Winter Jacket."),
        ({**PRODUCT, "title": "  Winter \n Jacket ", "description": "warm\t\tand  light "}, "Winter Jacket . warm and light"),
        ({}, "."),
    ],
)
def test_embedding_text_fills_the_template(product, text):
    assert embedding_text(EXAMPLE, product) == text


def test_embedding_text_keeps_escaped_braces_and_formats_numbers():
    d = parse_definition(broken(lambda c: c["embedding"].update(text="{title} {{{year}}}")))
    assert embedding_text(d, {"title": "Coat", "year": 2026}) == "Coat {2026}"


def test_a_valid_product_has_no_problems():
    assert check_product(EXAMPLE, PRODUCT) == []
    assert check_product(EXAMPLE, without("description")) == []
    assert check_product(EXAMPLE, {**PRODUCT, "sku": 15970}) == []  # product.v1 takes a numeric ID
    assert check_product(EXAMPLE, {**PRODUCT, "extra": {"nested": True}}) == []


@pytest.mark.parametrize(
    "product, problems",
    [
        (without("sku"), ["sku: missing"]),
        ({**PRODUCT, "sku": "sku 1"}, ["sku: not a product ID (ASCII letters, digits and ._:-, at most 128 characters)"]),
        ({**PRODUCT, "sku": True}, ["sku: not a product ID (ASCII letters, digits and ._:-, at most 128 characters)"]),
        (without("colour"), ["colour: missing"]),
        ({**PRODUCT, "usage": None}, ["usage: missing"]),
        ({**PRODUCT, "title": "   "}, ["title: blank"]),
        ({**PRODUCT, "title": "x" * 2001}, ["title: longer than 2000 characters"]),
        ({**PRODUCT, "year": "2026"}, ["year: not a whole number"]),
        ({**PRODUCT, "year": 2026.5}, ["year: not a whole-number year"]),
        ({**PRODUCT, "gender": "Martian"}, ["gender: not one of the declared values"]),
        ({**PRODUCT, "gender": "men"}, ["gender: not one of the declared values"]),
        ({**PRODUCT, "type": 7}, ["type: not text"]),
        ({**PRODUCT, "description": ["warm"]}, ["description: not text"]),
        ({**PRODUCT, "sku": None, "season": "Monsoon", "colour": None}, ["sku: missing", "colour: missing", "season: not one of the declared values"]),
        ("nordly-900", ["the product is not an object"]),
    ],
)
def test_check_product_names_each_problem_field(product, problems):
    assert check_product(EXAMPLE, product) == problems


def test_check_product_types_a_range_field_and_leaves_it_optional():
    assert check_product(PRICED, PRODUCT) == []  # Only the card fields are required.
    assert check_product(PRICED, {**PRODUCT, "price": 159000}) == []
    assert check_product(PRICED, {**PRODUCT, "price": "159,000"}) == ["price: not a whole number"]
    assert check_product(PRICED, {**PRODUCT, "price": True}) == ["price: not a whole number"]
    assert check_product(EXAMPLE, {**PRODUCT, "price": "159,000"}) == []  # Not a field of the example: ignored.


def test_check_product_types_floats_and_booleans():
    d = full_definition()
    product = {"id": "p-1", "title": "Coat", "kind": "A", "year": 2026}
    assert check_product(d, {**product, "price": 9.5, "onSale": True}) == []
    assert check_product(d, {**product, "price": 9}) == []
    assert check_product(d, {**product, "price": math.nan}) == ["price: not a number"]
    assert check_product(d, {**product, "onSale": 1}) == ["onSale: not true or false"]
    assert check_product(d, {**product, "year": 2026.0}) == []


def test_check_product_problems_never_carry_values():
    product = {**PRODUCT, "sku": "secret sku", "gender": "SecretGender", "title": "", "year": "SecretYear"}
    problems = check_product(EXAMPLE, product)
    assert problems and not any("secret" in problem.casefold() for problem in problems)


def test_the_definition_is_immutable():
    with pytest.raises(AttributeError):
        EXAMPLE.k = 10  # type: ignore[misc]
    assert copy.deepcopy(EXAMPLE) == EXAMPLE
