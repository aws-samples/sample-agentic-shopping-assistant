"""Product search as a local Strands tool, built from the catalog definition.

Search runs inside the agent rather than behind the Gateway. It is called on most turns and
needs no per-user authorization, so it avoids the Gateway, interceptor and target hop a
Gateway tool pays on every call: on Dev, a local search takes under 10 ms and a Gateway tool
call 0.15-0.6 s. Tools that need the shopper's identity or their own credentials belong
behind the Gateway; see docs/design-docs/tool-placement.md.

The tool's schema is generated from the deployment's catalog definition (CATALOG_DEFINITION and
the values after it, or the packaged example when it's empty): a free-text `query`, one parameter per filter, and
`limit`. The arguments come from the model, so aria_catalog validates them against the
definition and builds the query itself. Only a `CatalogQueryError` reaches the model as an
error it can fix. Any other failure is a backend fault: it is logged as `product_search_failed`
and reported without its details. Products that fail the product.v1 check are dropped and
counted in `product_search_invalid_product`; a failed query embedding falls back to a lexical
search and is logged as `product_search_embedding_failed`. Logs carry the provider, counts,
timings, error types and fixed fault descriptions, never argument values, products or
credentials. Results use the product.v1 envelope the web renders as cards.

The tool result has the shape Strands' `@tool` decorator gives a function tool: the envelope
as JSON text on success, `Error: <message>` for a `CatalogQueryError`, and
`Error: RuntimeError - Product search failed` for anything else.
"""

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any, Optional

from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolGenerator, ToolResult, ToolSpec, ToolUse

import aria_catalog
from aria_catalog import CatalogDefinition
from utils.logger import logger

FAILED = "Product search failed"


class ProductSearchTool(AgentTool):
    """`search_products` for one catalog definition; the deployment's (`definition_from_env()`) by default."""

    def __init__(self, definition: Optional[CatalogDefinition] = None) -> None:
        super().__init__()
        self.definition = definition if definition is not None else aria_catalog.definition_from_env()
        self._tool_spec: ToolSpec = aria_catalog.tool_spec(self.definition)

    @property
    def tool_name(self) -> str:
        return self._tool_spec["name"]

    @property
    def tool_spec(self) -> ToolSpec:
        return self._tool_spec

    @property
    def tool_type(self) -> str:
        return "python"

    async def stream(self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any) -> ToolGenerator:
        """Run the search off the event loop and yield its tool result."""
        tool_use_id = tool_use.get("toolUseId", "unknown")
        try:
            envelope = await asyncio.to_thread(self.search, tool_use.get("input"))
        except aria_catalog.CatalogQueryError as error:
            # An invalid argument: the message lists the allowed values so the model can fix the call.
            yield ToolResultEvent(_error(tool_use_id, f"Error: {error}"), exception=error)
            return
        except RuntimeError as error:  # search() reports every other failure as RuntimeError(FAILED).
            yield ToolResultEvent(_error(tool_use_id, f"Error: RuntimeError - {error}"), exception=error)
            return
        text = json.dumps(envelope, ensure_ascii=False)
        yield ToolResultEvent({"toolUseId": tool_use_id, "status": "success", "content": [{"text": text}]})

    def search(self, arguments: Optional[Mapping]) -> dict:
        """The product.v1 envelope for the model's arguments.

        Raises `CatalogQueryError` for arguments the model can fix, and `RuntimeError(FAILED)`,
        after logging `product_search_failed`, for any other failure.
        """
        provider = aria_catalog.search_provider()
        started = time.monotonic()

        def dropped(count: int) -> None:
            logger.warning("product_search_invalid_product", provider=provider, count=count)

        def fell_back(error_type: str) -> None:
            logger.warning("product_search_embedding_failed", provider=provider, error_type=error_type)

        try:
            products = aria_catalog.search_products(
                self.definition, arguments, on_invalid=dropped, on_fallback=fell_back
            )
        except aria_catalog.CatalogQueryError:
            raise
        except Exception as error:
            # A backend fault, not the model's. A CatalogBackendError's message is a fixed description.
            reason = {"reason": str(error)} if isinstance(error, aria_catalog.CatalogBackendError) else {}
            logger.error(
                "product_search_failed",
                provider=provider,
                error_type=type(error).__name__,
                duration_ms=_elapsed_ms(started),
                **reason,
            )
            raise RuntimeError(FAILED) from None
        logger.info("product_search_finished", provider=provider, results=len(products), duration_ms=_elapsed_ms(started))
        return aria_catalog.result(products)


def _error(tool_use_id: str, text: str) -> ToolResult:
    return {"toolUseId": tool_use_id, "status": "error", "content": [{"text": text}]}


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
