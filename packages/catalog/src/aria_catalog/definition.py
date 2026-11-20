"""The catalog definition, and everything generated from it.

A customer describes their catalog in a TOML file: which fields exist, which become filters,
which are searched as text, what text is embedded, and how a product maps onto a product.v1
card. `parse_definition()` validates it, so a bad definition fails at synth, not in a shopper's
search. From a `CatalogDefinition` this module builds:

- the `search_products` tool schema the model sees (`tool_spec`);
- the canonical, validated form of the model's arguments (`canonical_arguments`);
- the OpenSearch index body, search pipeline and query (`index_body`, `pipeline_body`,
  `search_body`);
- product.v1 cards, embedding text and the indexer's per-product check (`to_card`,
  `embedding_text`, `check_product`).

The model never writes a query: its arguments are checked against the definition and the query
is built here. This module uses only the standard library, so the infrastructure loader can
import it at synth.
"""

import json
import math
import os
import re
import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from aria_catalog.contract import MAX_TEXT_LENGTH, PRODUCT_FIELDS, _product_id, _year, product_v1
from aria_catalog.errors import CatalogQueryError

TOOL_NAME = "search_products"
TYPES = ("keyword", "text", "integer", "float", "boolean")
ANALYZERS = ("standard", "nori")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
RESERVED_PARAMETERS = ("query", "limit")
# Bedrock offers Embed v4 in-Region, through the us and eu geographic profiles and through the
# global profile; there is no apac profile.
MODEL = re.compile(r"((global|us|eu)\.)?cohere\.embed-v4:0")
DEFAULT_MODEL = "global.cohere.embed-v4:0"
DIMENSION = 1024
EMBEDDING_FIELD = "embedding"

MAX_VALUES = 100
MAX_VALUE_LENGTH = 100
MAX_FILTERS = 12
MAX_BOOST = 10
DEFAULT_K = 50
MAX_K = 100
MAX_QUERY_LENGTH = 200
DEFAULT_LIMIT = 3
MAX_LIMIT = 20

EXAMPLE_DIR = Path(__file__).with_name("example")

_NUMERIC = ("integer", "float")
_SEARCHABLE = ("text", "keyword")
_CATALOG_KEYS = ("name", "description", "idField", "analyzer", "fields", "embedding", "card", "search")
_FIELD_KEYS = ("name", "type", "filter", "values", "search", "boost", "description", "parameter")
# The catalog field types each product.v1 card field can come from; every other card field is text.
_CARD_TYPES = {"id": ("keyword", "integer"), "year": ("integer", "keyword")}
_TEXT_CARD_TYPES = ("keyword", "text")


class DefinitionError(ValueError):
    """The catalog definition is invalid. The message names the field and the rule it breaks."""


@dataclass(frozen=True)
class CatalogField:
    """One catalog field. `parameter` is its tool parameter name, and defaults to `name`."""

    name: str
    type: str
    filter: Optional[str] = None  # "exact", "range" or None
    values: tuple = ()
    search: bool = False
    boost: float = 1.0
    description: str = ""
    parameter: str = ""

    def __post_init__(self):
        if not self.parameter:
            object.__setattr__(self, "parameter", self.name)


@dataclass(frozen=True)
class CatalogDefinition:
    """A validated catalog definition. Build one with `parse_definition()` or `load_definition()`."""

    name: str
    description: str
    id_field: str
    analyzer: str
    fields: tuple[CatalogField, ...]
    embedding_text: str
    embedding_model: str = DEFAULT_MODEL
    card: dict[str, str] = field(default_factory=dict)
    k: int = DEFAULT_K


@dataclass(frozen=True)
class SearchArguments:
    """The model's arguments, validated and canonical. Filters and ranges hold catalog field names."""

    query: Optional[str]
    filters: tuple[tuple[str, Any], ...]
    ranges: tuple[tuple[str, Optional[float], Optional[float]], ...]
    limit: int


# --- Parsing and validation ---


def parse_definition(data: Mapping) -> CatalogDefinition:
    """Validate a definition in its TOML shape (a `[catalog]` table) and return it.

    Raises `DefinitionError` naming the first field that breaks a rule. Unknown keys are errors,
    so a misspelt key fails instead of being ignored.
    """
    if not isinstance(data, Mapping):
        raise DefinitionError("the definition must be a table holding a [catalog] table")
    _known_keys(data, ("catalog",), "the definition")
    catalog = data.get("catalog")
    if not isinstance(catalog, Mapping):
        raise DefinitionError("catalog: a [catalog] table is required")
    _known_keys(catalog, _CATALOG_KEYS, "catalog")

    name = _required_text(catalog, "name", "catalog.name")
    description = _required_text(catalog, "description", "catalog.description")
    analyzer = catalog.get("analyzer", "standard")
    if analyzer not in ANALYZERS:
        _fail("catalog.analyzer", f"must be one of {', '.join(ANALYZERS)}")

    fields = _fields(catalog.get("fields"))
    by_name = {f.name: f for f in fields}
    id_field = catalog.get("idField")
    if not isinstance(id_field, str) or id_field not in by_name:
        _fail("catalog.idField", "must name a defined field")
    if by_name[id_field].type not in _CARD_TYPES["id"]:
        _fail("catalog.idField", "must name a keyword or integer field")

    embedding, model = _embedding(catalog.get("embedding"), by_name)
    card = _card(catalog.get("card"), by_name, id_field)
    k = _k(catalog.get("search", {}))
    return CatalogDefinition(
        name=name,
        description=description,
        id_field=id_field,
        analyzer=analyzer,
        fields=fields,
        embedding_text=embedding,
        embedding_model=model,
        card=card,
        k=k,
    )


def _fail(where: str, rule: str):
    raise DefinitionError(f"{where}: {rule}")


def _known_keys(table: Mapping, allowed: tuple, where: str) -> None:
    for key in table:
        if key not in allowed:
            _fail(where, f"unknown key {key!r}; expected {', '.join(allowed)}")


def _required_text(table: Mapping, key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(where, "is required, as non-blank text")
    return value.strip()


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fields(raw: Any) -> tuple[CatalogField, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail("catalog.fields", "at least one [[catalog.fields]] entry is required")
    fields = tuple(_field(index, entry) for index, entry in enumerate(raw))

    names: set[str] = set()
    for f in fields:
        if f.name in names:
            _fail(f"catalog.fields.{f.name}", "the field name is defined twice")
        names.add(f.name)
    parameters: set[str] = set()
    for f in fields:
        if f.filter is None:
            continue
        if f.parameter in parameters:
            _fail(f"catalog.fields.{f.name}.parameter", f"{f.parameter!r} is already another filter's parameter")
        parameters.add(f.parameter)
    if len(parameters) > MAX_FILTERS:
        _fail("catalog.fields", f"at most {MAX_FILTERS} filters, so the tool schema stays small")
    if not any(f.search for f in fields):
        _fail("catalog.fields", "at least one field needs search = true")
    return fields


def _field(index: int, raw: Any) -> CatalogField:
    where = f"catalog.fields[{index}]"
    if not isinstance(raw, Mapping):
        _fail(where, "must be a table")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        _fail(f"{where}.name", f"is required and must match {NAME.pattern}")
    where = f"catalog.fields.{name}"
    if name.startswith("_"):
        _fail(f"{where}.name", "can't start with an underscore, which OpenSearch keeps for metadata fields")
    if name == EMBEDDING_FIELD:
        _fail(f"{where}.name", f"{EMBEDDING_FIELD!r} is the index's vector field")
    _known_keys(raw, _FIELD_KEYS, where)

    type_ = raw.get("type")
    if type_ not in TYPES:
        _fail(f"{where}.type", f"must be one of {', '.join(TYPES)}")
    filter_ = _filter(raw.get("filter"), type_, f"{where}.filter")
    values = _values(raw.get("values"), type_, filter_, f"{where}.values")

    search = raw.get("search", False)
    if not isinstance(search, bool):
        _fail(f"{where}.search", "must be true or false")
    if search and type_ not in _SEARCHABLE:
        _fail(f"{where}.search", "only text and keyword fields can be searched")
    boost = raw.get("boost", 1.0)
    if not _number(boost) or not 0 < boost <= MAX_BOOST:
        _fail(f"{where}.boost", f"must be a number greater than 0 and at most {MAX_BOOST}")
    if "boost" in raw and not search:
        _fail(f"{where}.boost", "applies only to a field with search = true")

    description = raw.get("description", "")
    if not isinstance(description, str):
        _fail(f"{where}.description", "must be text")
    parameter = raw.get("parameter", name)
    if "parameter" in raw:
        if not isinstance(parameter, str) or not NAME.fullmatch(parameter):
            _fail(f"{where}.parameter", f"must match {NAME.pattern}")
        if filter_ is None:
            _fail(f"{where}.parameter", "applies only to a filter")
    if filter_ is not None and parameter in RESERVED_PARAMETERS:
        _fail(f"{where}.parameter", f"{parameter!r} is reserved; set parameter to another name")

    return CatalogField(
        name=name,
        type=type_,
        filter=filter_,
        values=values,
        search=search,
        boost=float(boost),
        description=description.strip(),
        parameter=parameter,
    )


def _filter(raw: Any, type_: str, where: str) -> Optional[str]:
    if raw is None or raw is False:
        return None
    if raw is True or raw == "exact":
        kind = "exact"
    elif raw == "range":
        kind = "range"
    else:
        _fail(where, 'must be true (an exact filter) or "range"')
    if type_ == "text":
        _fail(where, "a text field can't be a filter")
    if kind == "range" and type_ not in _NUMERIC:
        _fail(where, "a range filter needs an integer or float field")
    return kind


def _values(raw: Any, type_: str, filter_: Optional[str], where: str) -> tuple:
    if type_ != "keyword" or filter_ != "exact":
        if raw is not None:
            _fail(where, "only a keyword field with filter = true declares values")
        return ()
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail(where, "a keyword filter must list its values")
    if len(raw) > MAX_VALUES:
        _fail(where, f"at most {MAX_VALUES} values")
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            _fail(where, "each value must be non-blank text without surrounding spaces")
        if len(value) > MAX_VALUE_LENGTH:
            _fail(where, f"each value can be at most {MAX_VALUE_LENGTH} characters")
        if value.casefold() in seen:
            _fail(where, "values must differ ignoring case, since arguments match them ignoring case")
        seen.add(value.casefold())
    return tuple(raw)


def _embedding(raw: Any, by_name: dict[str, CatalogField]) -> tuple[str, str]:
    if not isinstance(raw, Mapping):
        _fail("catalog.embedding", "a [catalog.embedding] table with text is required")
    _known_keys(raw, ("text", "model"), "catalog.embedding")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        _fail("catalog.embedding.text", "is required, as a template such as \"{title}. {description}\"")
    placeholders = _placeholders(text, "catalog.embedding.text")
    if not placeholders:
        _fail("catalog.embedding.text", "needs at least one {field} placeholder")
    for name in placeholders:
        if name not in by_name:
            _fail("catalog.embedding.text", f"{{{name}}} doesn't name a defined field")
    model = raw.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or not MODEL.fullmatch(model):
        _fail("catalog.embedding.model", f"must match {MODEL.pattern}")
    return text, model


def _placeholders(template: str, where: str) -> list[str]:
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError:
        _fail(where, "is not a valid template; write a literal brace as {{ or }}")
    names = []
    for _, name, spec, conversion in parts:
        if name is None:
            continue
        if not name:
            _fail(where, "each placeholder must name a field, as {title}")
        if spec or conversion:
            _fail(where, "placeholders take no format spec or conversion")
        names.append(name)
    return names


def _card(raw: Any, by_name: dict[str, CatalogField], id_field: str) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        _fail("catalog.card", "a [catalog.card] table is required")
    for key in raw:
        if key not in PRODUCT_FIELDS:
            _fail("catalog.card", f"unknown card field {key!r}; the card has {', '.join(PRODUCT_FIELDS)}")
    missing = [key for key in PRODUCT_FIELDS if key not in raw]
    if missing:
        _fail("catalog.card", f"missing {', '.join(missing)}; the card needs every product.v1 field")
    card = {}
    for key in PRODUCT_FIELDS:
        name = raw[key]
        if not isinstance(name, str) or name not in by_name:
            _fail(f"catalog.card.{key}", "must name a defined field")
        allowed = _CARD_TYPES.get(key, _TEXT_CARD_TYPES)
        if by_name[name].type not in allowed:
            _fail(f"catalog.card.{key}", f"must name a field of type {' or '.join(allowed)}")
        card[key] = name
    if card["id"] != id_field:
        _fail("catalog.card.id", "must be the idField")
    return card


def _k(raw: Any) -> int:
    if not isinstance(raw, Mapping):
        _fail("catalog.search", "must be a table")
    _known_keys(raw, ("k",), "catalog.search")
    k = raw.get("k", DEFAULT_K)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        _fail("catalog.search.k", f"must be a whole number from 1 to {MAX_K}")
    return k


# --- Loading and serializing ---


def load_definition(path: Optional[str | os.PathLike] = None) -> CatalogDefinition:
    """Parse the definition TOML at `path`; with None, the packaged example (`example/catalog.toml`)."""
    if path is None:
        return _example_definition()
    return _parse_toml(Path(path).read_text(encoding="utf-8"))


@lru_cache(1)
def _example_definition() -> CatalogDefinition:
    return _parse_toml((EXAMPLE_DIR / "catalog.toml").read_text(encoding="utf-8"))


def _parse_toml(text: str) -> CatalogDefinition:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise DefinitionError(f"the definition is not valid TOML: {error}") from None
    return parse_definition(data)


@lru_cache(1)
def _example_products_text() -> str:
    return (EXAMPLE_DIR / "products.json").read_text(encoding="utf-8")


def example_products() -> list[dict]:
    """The example catalog's synthetic products (`example/products.json`), in catalog order."""
    return json.loads(_example_products_text())


def definition_to_dict(d: CatalogDefinition) -> dict:
    """The definition as a compact JSON-able dict, in the TOML shape, that `parse_definition` reads back."""
    fields = []
    for f in d.fields:
        entry: dict[str, Any] = {"name": f.name, "type": f.type}
        if f.filter:
            entry["filter"] = True if f.filter == "exact" else f.filter
        if f.values:
            entry["values"] = list(f.values)
        if f.search:
            entry["search"] = True
        if f.boost != 1.0:
            entry["boost"] = f.boost
        if f.description:
            entry["description"] = f.description
        if f.filter and f.parameter != f.name:
            entry["parameter"] = f.parameter
        fields.append(entry)
    return {
        "catalog": {
            "name": d.name,
            "description": d.description,
            "idField": d.id_field,
            "analyzer": d.analyzer,
            "fields": fields,
            "embedding": {"text": d.embedding_text, "model": d.embedding_model},
            "card": dict(d.card),
            "search": {"k": d.k},
        }
    }


# The definition reaches the Runtime and the indexing task as environment values. AgentCore
# Runtime's CloudFormation resource takes at most 2,048 characters per value, so a longer definition
# is split, in order, across CATALOG_DEFINITION, CATALOG_DEFINITION_2, CATALOG_DEFINITION_3 and on.
ENVIRONMENT_NAME = "CATALOG_DEFINITION"
ENVIRONMENT_PART_BYTES = 2048
MAX_ENVIRONMENT_PARTS = 8


def definition_environment(definition_json: str) -> dict[str, str]:
    """The environment values that carry `definition_json`, each at most ENVIRONMENT_PART_BYTES in UTF-8.

    No cut falls next to whitespace, so no part starts or ends with a space. A definition that needs
    more than MAX_ENVIRONMENT_PARTS values is a DefinitionError.
    """
    text, parts, start = definition_json, [], 0
    while start < len(text):
        end, size = start, 0
        while end < len(text) and size + len(text[end].encode("utf-8")) <= ENVIRONMENT_PART_BYTES:
            size += len(text[end].encode("utf-8"))
            end += 1
        while end < len(text) and end - start > 1 and (text[end - 1].isspace() or text[end].isspace()):
            end -= 1
        parts.append(text[start:end])
        start = end
    if len(parts) > MAX_ENVIRONMENT_PARTS:
        raise DefinitionError(
            f"catalog: the definition is {len(text.encode('utf-8'))} bytes as JSON, more than the "
            f"{MAX_ENVIRONMENT_PARTS} environment values of {ENVIRONMENT_PART_BYTES} bytes the Runtime gets it in; "
            "use fewer or shorter filter values and descriptions")
    return {(ENVIRONMENT_NAME if n == 0 else f"{ENVIRONMENT_NAME}_{n + 1}"): part for n, part in enumerate(parts or [""])}


def definition_from_env(environ: Mapping[str, str] = os.environ) -> CatalogDefinition:
    """The definition in CATALOG_DEFINITION and the parts after it (`definition_environment`), the JSON
    of `definition_to_dict`; the example when it's empty."""
    raw, n = environ.get(ENVIRONMENT_NAME) or "", 2
    while (part := environ.get(f"{ENVIRONMENT_NAME}_{n}")) is not None:
        raw += part
        n += 1
    raw = raw.strip()
    return _from_json(raw) if raw else load_definition()


@lru_cache(4)
def _from_json(raw: str) -> CatalogDefinition:
    try:
        data = json.loads(raw)
    except ValueError:
        raise DefinitionError("CATALOG_DEFINITION is not valid JSON") from None
    return parse_definition(data)


# --- The tool schema ---


def filter_fields(d: CatalogDefinition) -> tuple[CatalogField, ...]:
    return tuple(f for f in d.fields if f.filter)


def search_fields(d: CatalogDefinition) -> tuple[CatalogField, ...]:
    return tuple(f for f in d.fields if f.search)


def tool_spec(d: CatalogDefinition) -> dict:
    """The `search_products` tool spec for the model: a free-text query, one parameter per filter, a limit."""
    filters = filter_fields(d)
    properties: dict[str, Any] = {
        "query": {
            "type": "string",
            "maxLength": MAX_QUERY_LENGTH,
            "description": (
                "Free text for what the filters don't cover. It's matched by keyword and by meaning, "
                f"so a short description of the item works best. At most {MAX_QUERY_LENGTH} characters."
            ),
        }
    }
    for f in filters:
        properties[f.parameter] = _parameter_schema(f)
    properties["limit"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_LIMIT,
        "default": DEFAULT_LIMIT,
        "description": f"Max results to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
    }
    if filters:
        order = ", ".join(f.parameter for f in filters)
        guidance = f"Prefer the filters to free text, in this order: {order}. Use query only for what they leave out."
    else:
        guidance = "Describe what the shopper wants in query."
    return {
        "name": TOOL_NAME,
        "description": f"{d.description}\n{guidance}",
        "inputSchema": {"json": {"type": "object", "properties": properties, "required": []}},
    }


def _parameter_schema(f: CatalogField) -> dict:
    lead = f.description.rstrip(". ")
    if f.filter == "range":
        hint = "An object with min, max or both."
        return {
            "type": "object",
            "description": f"{lead}. {hint}" if lead else hint,
            "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
            "additionalProperties": False,
        }
    if f.type == "keyword":
        listed = f"One of: {', '.join(f.values)}"
        return {"type": "string", "enum": list(f.values), "description": f"{lead}. {listed}" if lead else listed}
    schema: dict[str, Any] = {"type": {"integer": "integer", "float": "number", "boolean": "boolean"}[f.type]}
    if f.description:
        schema["description"] = f.description
    return schema


# --- Arguments ---


def canonical_arguments(d: CatalogDefinition, arguments: Optional[Mapping]) -> SearchArguments:
    """Check the model's arguments against the definition and return them in canonical form.

    A blank string or null is no criterion. Keyword values match ignoring case and come back in
    their declared spelling; numbers may arrive as numeric strings. `limit` defaults to 3 and is
    capped at 20. Raises `CatalogQueryError`, with a message the model can act on, for anything
    else: an unknown parameter, a value outside the declared values, a wrong type, a range whose
    min exceeds its max, a query over 200 characters, or a limit below 1.
    """
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise CatalogQueryError("the arguments must be an object")
    filters = filter_fields(d)
    by_parameter = {f.parameter: f for f in filters}
    for key in arguments:
        if key not in by_parameter and key not in RESERVED_PARAMETERS:
            accepted = ", ".join(("query", *by_parameter, "limit"))
            raise CatalogQueryError(f"unknown parameter {_shown(key)}; expected one of: {accepted}")

    exact: list[tuple[str, Any]] = []
    ranges: list[tuple[str, Optional[float], Optional[float]]] = []
    for f in filters:
        value = arguments.get(f.parameter)
        if f.filter == "range":
            bounds = _range(f, value)
            if bounds is not None:
                ranges.append((f.name, *bounds))
        else:
            canonical = _exact(f, value)
            if canonical is not None:
                exact.append((f.name, canonical))
    return SearchArguments(
        query=_query(arguments.get("query")),
        filters=tuple(exact),
        ranges=tuple(ranges),
        limit=_limit(arguments.get("limit")),
    )


def is_empty(args: SearchArguments) -> bool:
    """True when the arguments hold no query, filter or range: a search with nothing to look for."""
    return args.query is None and not args.filters and not args.ranges


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _query(value: Any) -> Optional[str]:
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise CatalogQueryError("query must be a string")
    text = value.strip()
    if len(text) > MAX_QUERY_LENGTH:
        raise CatalogQueryError(
            f"query is longer than {MAX_QUERY_LENGTH} characters; shorten it or move details into the filters"
        )
    return text


def _exact(f: CatalogField, value: Any) -> Any:
    if _blank(value):
        return None
    if f.type == "keyword":
        return _keyword(f, value)
    if f.type == "boolean":
        return _boolean(f, value)
    return _numeric(f.type, value, f.parameter)


def _keyword(f: CatalogField, value: Any) -> str:
    listed = ", ".join(f.values)
    if not isinstance(value, str):
        raise CatalogQueryError(f"{f.parameter} must be a string, one of: {listed}")
    text = value.strip()
    if text in f.values:
        return text
    for allowed in f.values:
        if allowed.casefold() == text.casefold():
            return allowed
    raise CatalogQueryError(f"invalid {f.parameter} {_shown(text)}; expected one of: {listed}")


def _boolean(f: CatalogField, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in ("true", "false"):
        return value.strip().casefold() == "true"
    raise CatalogQueryError(f"invalid {f.parameter} {_shown(value)}; expected true or false")


def _numeric(type_: str, value: Any, where: str) -> int | float:
    """An integer or float argument; a numeric string is accepted as the number it spells."""
    number: Any = value
    if isinstance(value, str):
        text = value.strip()
        try:
            number = int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                number = None
    if not _number(number) or (isinstance(number, float) and not math.isfinite(number)):
        expected = "a whole number" if type_ == "integer" else "a number"
        raise CatalogQueryError(f"invalid {where} {_shown(value)}; expected {expected}")
    if type_ == "integer":
        if isinstance(number, float):
            if not number.is_integer():
                raise CatalogQueryError(f"invalid {where} {_shown(value)}; expected a whole number")
            number = int(number)
    return number


def _range(f: CatalogField, value: Any) -> Optional[tuple[Optional[float], Optional[float]]]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, Mapping):
        raise CatalogQueryError(f"{f.parameter} must be an object with min, max or both")
    for key in value:
        if key not in ("min", "max"):
            raise CatalogQueryError(f"{f.parameter} takes only min and max, not {_shown(key)}")
    # A bound is any number, even for an integer field: OpenSearch rounds it as a range bound.
    low, high = (
        None if _blank(value.get(bound)) else _numeric("float", value.get(bound), f"{f.parameter}.{bound}")
        for bound in ("min", "max")
    )
    if low is None and high is None:
        return None
    if low is not None and high is not None and low > high:
        raise CatalogQueryError(f"{f.parameter}.min is greater than {f.parameter}.max")
    return low, high


def _limit(value: Any) -> int:
    if _blank(value):
        return DEFAULT_LIMIT
    try:
        number = _numeric("integer", value, "limit")
    except CatalogQueryError:
        number = 0
    if number < 1:
        raise CatalogQueryError(f"invalid limit {_shown(value)}; expected a whole number from 1 to {MAX_LIMIT}")
    return min(number, MAX_LIMIT)


# --- The index and the query ---


def index_body(d: CatalogDefinition, replicas: int) -> dict:
    """The body that creates a versioned index for the definition.

    The mapping is strict: a document holds only the definition's fields and `embedding`, so the
    indexer sends nothing else.
    """
    properties: dict[str, Any] = {}
    for f in d.fields:
        mapping: dict[str, Any] = {"type": f.type}
        if f.type == "text":
            mapping["analyzer"] = d.analyzer
        properties[f.name] = mapping
    properties[EMBEDDING_FIELD] = {
        "type": "knn_vector",
        "dimension": DIMENSION,
        "method": {
            "name": "hnsw",
            "engine": "faiss",
            "space_type": "cosinesimil",
            "parameters": {"m": 16, "ef_construction": 128},
        },
    }
    return {
        "settings": {
            "index": {"knn": True, "number_of_shards": 1, "number_of_replicas": replicas, "refresh_interval": "10s"}
        },
        "mappings": {"dynamic": "strict", "properties": properties},
    }


def pipeline_name(alias: str) -> str:
    """The search pipeline that fuses the hybrid query for the index behind `alias`."""
    return f"{alias}-hybrid"


def pipeline_body() -> dict:
    """The search pipeline: reciprocal rank fusion of the lexical and vector halves."""
    return {
        "description": "Fuses the catalog's lexical and vector results by reciprocal rank fusion",
        "phase_results_processors": [{"score-ranker-processor": {"combination": {"technique": "rrf"}}}],
    }


def source_fields(d: CatalogDefinition) -> list[str]:
    """The catalog fields the card mapping reads, in card order: all a search needs to return."""
    return list(dict.fromkeys(d.card[key] for key in PRODUCT_FIELDS))


def search_body(d: CatalogDefinition, args: SearchArguments, vector: Optional[list[float]] = None) -> dict:
    """The OpenSearch query for canonical arguments.

    Filters are `term` and `range` clauses. With a query, the lexical part adds a `multi_match`
    over the search fields with their boosts. With a query and its embedding, the body is a
    `hybrid` query of the lexical part and a `knn` query carrying the same filters; otherwise it
    is the lexical part alone.

    A hybrid body must be sent with `search_pipeline=pipeline_name(alias)`, which fuses its two
    halves by RRF. Send a lexical body without it.
    """
    filters: list[dict] = [{"term": {name: value}} for name, value in args.filters]
    for name, low, high in args.ranges:
        bounds = {}
        if low is not None:
            bounds["gte"] = low
        if high is not None:
            bounds["lte"] = high
        filters.append({"range": {name: bounds}})

    lexical: dict[str, Any] = {"filter": filters}
    if args.query:
        fields = [f"{f.name}^{f.boost:g}" for f in search_fields(d)]
        lexical["must"] = [{"multi_match": {"query": args.query, "type": "best_fields", "fields": fields}}]
    query: dict[str, Any] = {"bool": lexical}

    if vector is not None and args.query:
        knn: dict[str, Any] = {"vector": vector, "k": d.k}
        if filters:
            knn["filter"] = {"bool": {"filter": filters}}
        query = {
            "hybrid": {
                "queries": [query, {"knn": {EMBEDDING_FIELD: knn}}],
                "pagination_depth": max(d.k, args.limit),
            }
        }
    return {"query": query, "size": args.limit, "_source": source_fields(d)}


# --- Products ---


def to_card(d: CatalogDefinition, source: Any) -> Optional[dict]:
    """The product.v1 card for a product in the catalog's field names, or None when it doesn't qualify."""
    if not isinstance(source, Mapping):
        return None
    try:
        item = {key: source[name] for key, name in d.card.items()}
    except KeyError:
        return None
    return product_v1(item)


def embedding_text(d: CatalogDefinition, product: Mapping) -> str:
    """The definition's embedding template filled from a product, with runs of whitespace collapsed.

    A missing or null field becomes an empty string.
    """
    parts = []
    for literal, name, _, _ in string.Formatter().parse(d.embedding_text):
        parts.append(literal)
        if name is not None:
            value = product.get(name) if isinstance(product, Mapping) else None
            parts.append("" if value is None else str(value))
    return " ".join("".join(parts).split())


def check_product(d: CatalogDefinition, product: Any) -> list[str]:
    """The reasons a product can't be indexed, or [] when it can. Reasons name fields, never values."""
    if not isinstance(product, Mapping):
        return ["the product is not an object"]
    problems: dict[str, str] = {}  # The first problem found for each field.

    def flag(name: str, rule: str) -> None:
        problems.setdefault(name, rule)

    product_id = product.get(d.id_field)
    if product_id is None:
        flag(d.id_field, "missing")
    elif _product_id(product_id) is None:
        flag(d.id_field, "not a product ID (ASCII letters, digits and ._:-, at most 128 characters)")

    for key, name in d.card.items():
        value = product.get(name)
        if key == "id":
            continue
        if value is None:
            flag(name, "missing")
        elif key == "year":
            if _year(value) is None:
                flag(name, "not a whole-number year")
        elif isinstance(value, str):
            if len(value) > MAX_TEXT_LENGTH:
                flag(name, f"longer than {MAX_TEXT_LENGTH} characters")
            elif key == "productDisplayName" and not value.strip():
                flag(name, "blank")

    for f in d.fields:
        value = product.get(f.name)
        if value is None:
            continue
        if f.name == d.id_field and f.type == "keyword":
            continue  # Checked above: product.v1 takes a numeric ID as its string.
        if not _typed(f.type, value):
            flag(f.name, f"not {_TYPE_NAMES[f.type]}")
        elif f.values and value not in f.values:
            flag(f.name, "not one of the declared values")

    if not problems and to_card(d, product) is None:
        return ["the card fields don't make a product.v1 product"]
    return [f"{name}: {rule}" for name, rule in problems.items()]


_TYPE_NAMES = {
    "keyword": "text",
    "text": "text",
    "integer": "a whole number",
    "float": "a number",
    "boolean": "true or false",
}


def _typed(type_: str, value: Any) -> bool:
    if type_ in _SEARCHABLE:
        return isinstance(value, str)
    if type_ == "boolean":
        return isinstance(value, bool)
    if not _number(value) or (isinstance(value, float) and not math.isfinite(value)):
        return False
    return type_ == "float" or isinstance(value, int) or value.is_integer()
