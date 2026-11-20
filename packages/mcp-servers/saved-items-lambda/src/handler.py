"""Sample AgentCore Gateway Lambda target: the shopper's saved items.

It shows the Gateway path for a tool that needs to know who the shopper is. The agent
never forwards the shopper's sign-in token. It calls the Gateway with a token minted for
itself, plus the shopper's actor ID in `x-aria-actor-id`. The Gateway's REQUEST
interceptor checks that header, and the Gateway passes it here in the Lambda client
context, with `x-correlation-id`, the agent's trace ID: log it, and an operator can go
from a target's log line to the agent's trace. Product search, which needs no identity,
runs in the agent instead.

The saved items are a deterministic sample drawn from the packaged example catalog, mapped
onto product.v1 cards through the example's catalog definition. A real implementation would
read the shopper's wishlist from its own store, scoped by actor ID. Apart from aria_catalog's
definition module, which is itself stdlib-only, the handler uses only the standard library.
"""

import hashlib
import logging
import os
import re
from typing import Any

from aria_catalog.definition import example_products, load_definition, to_card

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TOOL_NAME = "get_saved_items"
ACTOR_HEADER = "x-aria-actor-id"
CORRELATION_HEADER = "x-correlation-id"
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
# ARIA actor IDs are SHA-256 hex digests of the verified issuer, subject and tenant.
_ACTOR_ID = re.compile(r"^[0-9a-f]{64}$")
# A trace ID, 32 hex characters, as the interceptor sets it.
_TRACE_ID = re.compile(r"[0-9a-f]{32}")


def _custom(context: Any) -> dict:
    client_context = getattr(context, "client_context", None)
    return getattr(client_context, "custom", None) or {}


def _tool_name(context: Any) -> str:
    raw = _custom(context).get("bedrockAgentCoreToolName", "")
    return raw.split("___")[-1] if "___" in raw else raw


def _propagated(context: Any, name: str) -> Any:
    headers = _custom(context).get("bedrockAgentCorePropagatedHeaders") or {}
    return next((v for k, v in headers.items() if k.lower() == name), "")


def actor_id(context: Any) -> str:
    """The shopper's actor ID, as the Gateway interceptor forwarded it."""
    value = _propagated(context, ACTOR_HEADER)
    if not isinstance(value, str) or not _ACTOR_ID.match(value):
        raise PermissionError("Saved items need the shopper's identity")
    return value


def correlation_id(context: Any) -> str:
    """The agent's trace ID, or "" when the request carries none. Only a well-formed ID is logged."""
    value = _propagated(context, CORRELATION_HEADER)
    return value if isinstance(value, str) and _TRACE_ID.fullmatch(value) else ""


def _catalog() -> list[dict]:
    """The example catalog's products as product.v1 cards, in catalog order."""
    definition = load_definition()
    return [card for card in (to_card(definition, product) for product in example_products()) if card is not None]


def saved_items(actor: str, limit: int) -> list[dict]:
    """A stable, per-shopper sample: the same shopper always sees the same items."""
    products = sorted(_catalog(), key=lambda p: hashlib.sha256(f"{actor}:{p['id']}".encode()).hexdigest())
    count = 1 + int(actor[:2], 16) % len(products)
    return products[: min(count, max(1, min(int(limit), MAX_LIMIT)))]


def lambda_handler(event, context):
    tool = _tool_name(context)
    if tool != TOOL_NAME:
        raise ValueError("Unknown saved-items tool")
    arguments = event if isinstance(event, dict) else {}
    if set(arguments) - {"limit"}:
        raise ValueError("Unsupported saved-items arguments")
    actor = actor_id(context)
    items = saved_items(actor, arguments.get("limit", DEFAULT_LIMIT))
    logger.info("saved items: actor=%s… count=%d trace=%s", actor[:8], len(items), correlation_id(context) or "-")
    return {"schemaVersion": "product.v1", "count": len(items), "products": items}
