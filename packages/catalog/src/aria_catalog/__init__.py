"""Catalog search driven by a catalog definition.

A catalog definition (`aria_catalog.definition`) describes a customer's catalog: its fields,
filters, search fields, embedded text and card mapping. From it this package builds the agent's
`search_products` tool schema, validates the model's arguments, generates the OpenSearch index,
search pipeline and hybrid query, and maps products onto product.v1 cards. The agent's search
tool, the indexer and the saved-items sample share it.

The provider is chosen per deployment with SEARCH_PROVIDER: `fixture` (the packaged example
catalog, in memory), `opensearch` (hybrid lexical and vector search) or `http` (the customer's
catalog API). `aria_catalog.embedding` embeds text with Cohere Embed v4 on Bedrock.
"""

from aria_catalog.definition import (
    EMBEDDING_FIELD,
    CatalogDefinition,
    CatalogField,
    DefinitionError,
    SearchArguments,
    canonical_arguments,
    check_product,
    definition_environment,
    definition_from_env,
    definition_to_dict,
    embedding_text,
    example_products,
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
from aria_catalog.errors import CatalogBackendError, CatalogQueryError
from aria_catalog.search import DEFAULT_LIMIT, MAX_LIMIT, search_products, search_provider

SCHEMA_VERSION = "product.v1"


def result(products: list[dict]) -> dict:
    """The versioned envelope the web validates before it renders product cards."""
    return {"schemaVersion": SCHEMA_VERSION, "count": len(products), "products": products}


__all__ = [
    "DEFAULT_LIMIT",
    "EMBEDDING_FIELD",
    "MAX_LIMIT",
    "SCHEMA_VERSION",
    "CatalogBackendError",
    "CatalogDefinition",
    "CatalogField",
    "CatalogQueryError",
    "DefinitionError",
    "SearchArguments",
    "canonical_arguments",
    "check_product",
    "definition_environment",
    "definition_from_env",
    "definition_to_dict",
    "embedding_text",
    "example_products",
    "index_body",
    "is_empty",
    "load_definition",
    "parse_definition",
    "pipeline_body",
    "pipeline_name",
    "result",
    "search_body",
    "search_products",
    "search_provider",
    "source_fields",
    "to_card",
    "tool_spec",
]
