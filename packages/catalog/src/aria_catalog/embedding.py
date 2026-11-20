"""Text embeddings from Cohere Embed v4 on Amazon Bedrock: 1024 float dimensions.

The indexer embeds products with `input_type` `search_document`; a search embeds its query with
`search_query`. The caller passes the Bedrock Runtime client: the search path uses
`query_client()`, whose short timeouts let a slow embedding fall back to a lexical search
instead of stalling the turn.
"""

import json
from collections.abc import Iterable
from functools import lru_cache

from aria_catalog.definition import DEFAULT_MODEL, DIMENSION
from aria_catalog.errors import CatalogBackendError

MODEL = DEFAULT_MODEL
BATCH = 96  # Embed v4 takes at most 96 texts per call.
INPUT_TYPES = ("search_document", "search_query")


def embed(texts: Iterable[str], input_type: str, *, model: str = MODEL, client) -> list[list[float]]:
    """One vector per text, in order, from one `invoke_model` call per batch of 96.

    A response with the wrong number of vectors, or a vector of the wrong length, raises
    `CatalogBackendError`. The client's own errors (throttling, timeouts) propagate unchanged.
    """
    if input_type not in INPUT_TYPES:
        raise ValueError(f"input_type must be one of {', '.join(INPUT_TYPES)}")
    texts = list(texts)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start : start + BATCH]
        body = {"texts": batch, "input_type": input_type, "embedding_types": ["float"], "output_dimension": DIMENSION}
        response = client.invoke_model(
            modelId=model, body=json.dumps(body), contentType="application/json", accept="application/json"
        )
        vectors.extend(_vectors(response, len(batch)))
    return vectors


def _vectors(response, expected: int) -> list[list[float]]:
    try:
        payload = json.loads(response["body"].read())
        embeddings = payload["embeddings"]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CatalogBackendError("Embedding response has no embeddings") from None
    # With one embedding type the response is a plain list of vectors; with several it is keyed by
    # type ({"float": [...]}). Accept both.
    if isinstance(embeddings, dict):
        embeddings = embeddings.get("float")
    if not isinstance(embeddings, list) or len(embeddings) != expected:
        raise CatalogBackendError("Embedding response has the wrong number of vectors")
    for vector in embeddings:
        if not isinstance(vector, list) or len(vector) != DIMENSION or not all(_number(x) for x in vector):
            raise CatalogBackendError(f"Embedding response has a vector that isn't {DIMENSION} numbers")
    return embeddings


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@lru_cache(1)
def query_client():
    """The search path's Bedrock Runtime client, cached: connect 1 s, read 3 s, one attempt.

    No retries, so a hung or throttled Bedrock falls back to the lexical search within about 4 s.
    """
    # Imported here so the package imports without boto3, and the Region is read at first use.
    import boto3
    from botocore.config import Config

    from aria_catalog.search import aws_region

    # botocore's `max_attempts` counts retries; `total_max_attempts` counts the first attempt too.
    config = Config(connect_timeout=1, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 1})
    return boto3.client("bedrock-runtime", region_name=aws_region(), config=config)
