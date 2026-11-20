"""Product search across the configured provider, driven by the catalog definition.

`search_products()` checks and canonicalizes the model's arguments once, against the
definition, and every provider gets the canonical values. SEARCH_PROVIDER picks the provider:

- `fixture` searches the packaged example catalog in memory, lexically;
- `opensearch` runs the definition's hybrid query, lexical and vector, on the index behind
  OPENSEARCH_PRODUCT_INDEX. The query text is embedded with Cohere Embed v4; if that fails, the
  search runs its lexical half alone;
- `http` posts the canonical arguments to the customer's catalog API.

Every provider returns products in the catalog's field names, which map onto product.v1 cards
through the definition's `[catalog.card]`. A product that doesn't make a card is dropped and
counted. The OpenSearch client is cached for the life of the process, so a Runtime session
reuses one connection for every search.

An invalid argument raises `CatalogQueryError`, with a message the model can act on. A fault in
the backend, its response or this deployment's settings raises `CatalogBackendError` or the
backend's own exception, never `CatalogQueryError`.
"""

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Callable, Optional

from aria_catalog import embedding
from aria_catalog.definition import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    CatalogDefinition,
    SearchArguments,
    canonical_arguments,
    is_empty,
    pipeline_name,
    search_body,
    to_card,
)
from aria_catalog.errors import CatalogBackendError

__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "aws_region", "get_client", "product_index", "search_products", "search_provider"]

_log = logging.getLogger(__name__)


def search_provider() -> str:
    """The catalog backend this deployment selected with SEARCH_PROVIDER (default `fixture`)."""
    return os.environ.get("SEARCH_PROVIDER") or "fixture"


def product_index() -> str:
    """The OpenSearch alias searched: OPENSEARCH_PRODUCT_INDEX (default `products`)."""
    return os.environ.get("OPENSEARCH_PRODUCT_INDEX") or "products"


def aws_region() -> str:
    """The Region for the catalog's AWS clients: AWS_REGION, else AWS_DEFAULT_REGION."""
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise CatalogBackendError("Set AWS_REGION or AWS_DEFAULT_REGION for the catalog's AWS clients")
    return region


@lru_cache(1)
def get_client():
    # Imported here so the fixture and HTTPS providers start without the OpenSearch client.
    import boto3
    from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

    host = os.environ.get("OPENSEARCH_HOST", "").replace("https://", "").rstrip("/")
    if not host:
        raise CatalogBackendError("OPENSEARCH_HOST is not set")
    region = aws_region()
    credentials = boto3.Session().get_credentials()

    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=AWSV4SignerAuth(credentials, region),
        use_ssl=True,
        verify_certs=True,
        http_compress=True,
        connection_class=RequestsHttpConnection,
        timeout=10,
        max_retries=1,
        retry_on_timeout=True,
    )


def search_products(
    definition: CatalogDefinition,
    arguments: Optional[Mapping],
    *,
    on_invalid: Optional[Callable[[int], None]] = None,
    on_fallback: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """Search the catalog with the model's arguments and return product.v1 cards, at most `limit`.

    Returns [] before calling any backend when the arguments hold no query, filter or range;
    blank strings are no criteria. Products that don't make a product.v1 card are dropped, and
    their number is passed to `on_invalid` (logged as a warning when that is None). When the
    backend returns products but none makes a card, that is a CatalogBackendError, not an empty
    match. When the OpenSearch query embedding fails, the search runs lexically and the error's
    type name goes to `on_fallback` (logged as a warning when that is None).
    """
    args = canonical_arguments(definition, arguments)
    if is_empty(args):
        return []

    provider = search_provider()
    rows = _provider_search(provider, definition, args, on_fallback)
    cards = [card for card in (to_card(definition, row) for row in rows) if card is not None]
    if invalid := len(rows) - len(cards):
        if on_invalid is None:
            _log.warning("product_search_invalid_product provider=%s count=%d", provider, invalid)
        else:
            on_invalid(invalid)
        if not cards:
            raise CatalogBackendError("No product in the catalog response matches product.v1")
    return cards[: args.limit]


def _provider_search(
    provider: str, definition: CatalogDefinition, args: SearchArguments, on_fallback: Optional[Callable[[str], None]]
) -> list:
    """The products the provider returns, in the catalog's field names, before the card mapping."""
    if provider == "fixture":
        from aria_catalog.providers import fixture_search
        return fixture_search(definition, args)
    if provider == "http":
        from aria_catalog.providers import http_search
        return http_search(definition, args)
    if provider != "opensearch":
        raise CatalogBackendError("Unknown search provider")
    return _opensearch(definition, args, on_fallback)


def _opensearch(definition: CatalogDefinition, args: SearchArguments, on_fallback) -> list:
    client = get_client()
    alias = product_index()
    vector = _query_vector(definition, args.query, on_fallback) if args.query else None
    body = search_body(definition, args, vector)
    if vector is None:
        result = client.search(index=alias, body=body)
    else:
        result = client.search(index=alias, body=body, params={"search_pipeline": pipeline_name(alias)})
    return [hit.get("_source") for hit in result["hits"]["hits"]]


def _query_vector(definition: CatalogDefinition, query: str, on_fallback) -> Optional[list[float]]:
    """The query's embedding, or None, after reporting the failure, when Bedrock doesn't give one."""
    try:
        [vector] = embedding.embed(
            [query], "search_query", model=definition.embedding_model, client=embedding.query_client()
        )
        return vector
    except Exception as error:  # Any embedding failure falls back to the lexical search.
        name = type(error).__name__
        if on_fallback is None:
            _log.warning("product_search_embedding_failed provider=opensearch error_type=%s", name)
        else:
            on_fallback(name)
        return None
