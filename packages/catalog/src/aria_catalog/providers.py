"""The fixture provider and the HTTPS catalog adapter. Credentials never come from tool input.

Providers take canonical `SearchArguments` and return products in the catalog's field names, as
the backend sent them; `search.search_products()` maps each onto a product.v1 card through the
definition. A fault in the backend, its response or this deployment's settings raises
`CatalogBackendError`, never the `CatalogQueryError` that blames the arguments.
"""
import json
import os
import threading
from numbers import Real
from time import monotonic
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import boto3

from aria_catalog.definition import (
    MAX_LIMIT,
    CatalogDefinition,
    SearchArguments,
    example_products,
    filter_fields,
    load_definition,
    search_fields,
)
from aria_catalog.errors import CatalogBackendError
from aria_catalog.search import aws_region

MAX_RESPONSE_BYTES = 256 * 1024
# A cached credential is used for at most this long, so a rotated secret is picked up.
CREDENTIAL_TTL_SECONDS = 300
_CREDENTIAL_HEADERS = ("authorization", "x-api-key")


def path_value(value, path):
    for key in path.split("."):
        value = value[key]
    return value


# --- The fixture ---


def fixture_search(definition: CatalogDefinition, args: SearchArguments) -> list[dict]:
    """Search the example catalog in memory, lexically, as the example definition describes it.

    Every filter and range must hold. With a query, a product scores the boost of each search
    field for each case-folded query word found in it; products that score nothing are left out,
    and ties keep catalog order. Without a query, the filtered products come in catalog order.
    """
    if definition != load_definition():
        raise CatalogBackendError("The fixture provider serves only the example catalog definition")
    rows = [row for row in example_products() if _matches(row, args)]
    if args.query:
        words = list(dict.fromkeys(args.query.casefold().split()))
        scored = [(score, row) for row in rows if (score := _score(row, words, search_fields(definition)))]
        scored.sort(key=lambda pair: pair[0], reverse=True)  # Stable: ties keep catalog order.
        rows = [row for _, row in scored]
    return rows[: args.limit]


def _matches(row: dict, args: SearchArguments) -> bool:
    if any(row.get(name) != value for name, value in args.filters):
        return False
    for name, low, high in args.ranges:
        value = row.get(name)
        if not isinstance(value, Real) or isinstance(value, bool):
            return False
        if (low is not None and value < low) or (high is not None and value > high):
            return False
    return True


def _score(row: dict, words: list[str], fields) -> float:
    score = 0.0
    for field in fields:
        text = row.get(field.name)
        if isinstance(text, str):
            folded = text.casefold()
            score += field.boost * sum(word in folded for word in words)
    return score


# --- The HTTPS catalog ---


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# (secret ARN, monotonic time fetched, header): one credential per process, refreshed by TTL.
_credential_cache: tuple[str, float, dict[str, str]] | None = None
_credential_lock = threading.Lock()


def credential(refresh: bool = False) -> dict[str, str]:
    """The catalog's credential header from Secrets Manager, or {} when none is configured.

    Cached for CREDENTIAL_TTL_SECONDS; `refresh=True` fetches it again at once, for a catalog
    that rejected the cached value.
    """
    global _credential_cache
    arn = os.environ.get("SEARCH_SECRET_ARN")
    if not arn:
        return {}
    with _credential_lock:
        cached = _credential_cache
        if not refresh and cached and cached[0] == arn and monotonic() - cached[1] < CREDENTIAL_TTL_SECONDS:
            return cached[2]
        header = _fetch_credential(arn)
        _credential_cache = (arn, monotonic(), header)
        return header


def _fetch_credential(arn: str) -> dict[str, str]:
    secret = boto3.client("secretsmanager", region_name=aws_region()).get_secret_value(SecretId=arn)
    try:
        value = json.loads(secret["SecretString"])
        header, token = value["header"], value["value"]
    except (KeyError, TypeError, ValueError):
        raise CatalogBackendError("Catalog credential must be a JSON secret with header and value") from None
    if not isinstance(header, str) or header.lower() not in _CREDENTIAL_HEADERS or not isinstance(token, str):
        raise CatalogBackendError("Unsupported catalog credential header")
    return {header: token}


def _post(endpoint: str, body: bytes, headers: dict[str, str]) -> bytes:
    request = Request(endpoint, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise CatalogBackendError("Catalog response exceeds 256 KiB")
    return data


def _post_with_credential(endpoint: str, body: bytes) -> bytes:
    try:
        return _post(endpoint, body, credential())
    except HTTPError as error:
        error.close()
        if error.code not in (401, 403) or not os.environ.get("SEARCH_SECRET_ARN"):
            raise CatalogBackendError(f"Catalog returned HTTP {error.code}") from None
    # The catalog rejected the cached credential, which may have been rotated: fetch it again, once.
    try:
        return _post(endpoint, body, credential(refresh=True))
    except HTTPError as error:
        error.close()
        raise CatalogBackendError(f"Catalog returned HTTP {error.code}") from None


def http_body(definition: CatalogDefinition, args: SearchArguments) -> dict:
    """The JSON the catalog API receives: `query`, every filter parameter, and `limit`.

    Keys are the tool's parameter names. A criterion not given is null; a range is
    `{"min": .., "max": ..}`, with null for an open end.
    """
    exact = dict(args.filters)
    ranges = {name: (low, high) for name, low, high in args.ranges}
    body: dict = {"query": args.query}
    for field in filter_fields(definition):
        if field.filter == "range":
            low, high = ranges.get(field.name, (None, None))
            body[field.parameter] = None if field.name not in ranges else {"min": low, "max": high}
        else:
            body[field.parameter] = exact.get(field.name)
    body["limit"] = args.limit
    return body


def http_search(definition: CatalogDefinition, args: SearchArguments) -> list:
    """POST the canonical arguments to SEARCH_ENDPOINT and return the products at SEARCH_RESULT_PATH.

    The products come back as the catalog sent them, in the definition's field names. A response
    that isn't JSON, is over 256 KiB, or has no list of at most 20 products there fails the search.
    """
    endpoint = os.environ.get("SEARCH_ENDPOINT", "")
    if not endpoint.startswith("https://"):
        raise CatalogBackendError("HTTPS search endpoint required")
    data = _post_with_credential(endpoint, json.dumps(http_body(definition, args)).encode())
    try:
        payload = json.loads(data)
    except ValueError:
        raise CatalogBackendError("Catalog response is not valid JSON") from None
    try:
        products = path_value(payload, os.environ.get("SEARCH_RESULT_PATH") or "products")
    except (KeyError, IndexError, TypeError):
        raise CatalogBackendError("Catalog response has no product list at SEARCH_RESULT_PATH") from None
    if not isinstance(products, list):
        raise CatalogBackendError("Catalog response has no product list at SEARCH_RESULT_PATH")
    if len(products) > MAX_LIMIT:
        raise CatalogBackendError(f"Catalog returned more than {MAX_LIMIT} products")
    return products
