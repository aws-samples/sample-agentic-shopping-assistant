"""The Gateway's HTTP transport: the agent's minted token on every request, and an HTTP
failure confined to the request that met it.

The token is read at request time rather than set as a client header, because HTTPX copies
headers when a client is created and a header would go stale. Reading it per request also
lets the token source mint a fresh token once the cached one nears expiry. The shopper's
own JWT never reaches here.

mcp 1.30 calls `raise_for_status()` inside the streamable HTTP transport's task group, so a
single non-2xx reply (an interceptor denial comes back as HTTP 500), a network error, or an
exception while minting the token would close the whole MCP session and fail every later
Gateway call in it. This transport answers those cases itself: a JSON-RPC request gets a
JSON-RPC error for its own id, so only that call fails, and a notification or response,
which gets no reply, is acknowledged with 202.
"""

import json
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio
import httpx
from mcp.client.streamable_http import streamable_http_client

from utils.logger import logger

# Each Gateway request gets 10 s to connect, write or wait for a pooled connection, and 30 s
# for each read, including the wait between events of a streamed reply.
GATEWAY_TIMEOUT = httpx.Timeout(10, read=30)
# JSON-RPC's implementation-defined server error, carried by the errors made here.
SERVER_ERROR = -32000


class GatewayTransport(httpx.AsyncBaseTransport):
    """Authorizes each Gateway request with the minted token and keeps HTTP failures per request."""

    def __init__(self, source, gateway_url: str, inner: Optional[httpx.AsyncBaseTransport] = None):
        self._source = source
        self._gateway = httpx.URL(gateway_url)
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        message = _jsonrpc_message(await request.aread()) if request.method == "POST" else None
        if _same_origin(request.url, self._gateway):
            try:
                # Minting is a blocking AWS call, so it runs off the MCP event loop.
                request.headers["Authorization"] = await anyio.to_thread.run_sync(self._source.bearer)
            except Exception as exc:
                logger.error("gateway_token_failed", error_type=type(exc).__name__, code=_error_code(exc))
                if message is None:
                    raise
                return _reply(request, message, "Could not authorize the Gateway request")
        if message is None:
            # mcp's SSE GET and session DELETE already survive their own errors.
            return await self._inner.handle_async_request(request)
        return await self._send(request, message)

    async def _send(self, request: httpx.Request, message: dict[str, Any]) -> httpx.Response:
        """Send a JSON-RPC message, answering a failure with a reply of its own instead of raising."""
        response = None
        try:
            response = await self._inner.handle_async_request(request)
            if response.is_success:
                if _is_json(response):
                    # Read the body here, so a connection that breaks mid-body fails only this call.
                    await response.aread()
                return response
        except httpx.RequestError as exc:  # transport failures, and a body that can't be decoded
            if response is not None:
                await response.aclose()
            logger.warning("gateway_request_failed", error_type=type(exc).__name__, method=message.get("method"))
            return _reply(request, message, f"Gateway request failed ({type(exc).__name__})")
        await response.aclose()
        logger.warning("gateway_http_error", status=response.status_code, method=message.get("method"))
        return _reply(request, message, f"Gateway returned HTTP {response.status_code}")

    async def aclose(self) -> None:
        await self._inner.aclose()


@asynccontextmanager
async def gateway_transport(
    url: str, source, headers: dict[str, str], *, inner: Optional[httpx.AsyncBaseTransport] = None
):
    """The MCP transport for the Gateway: mcp's streamable HTTP client over GatewayTransport.

    `inner` replaces the network transport in tests.
    """
    async with httpx.AsyncClient(
        transport=GatewayTransport(source, url, inner),
        headers=headers,
        timeout=GATEWAY_TIMEOUT,
        # The Gateway doesn't redirect. mcp follows a redirect only within the Gateway's origin and
        # doesn't read this setting, and GatewayTransport answers a redirected POST with a JSON-RPC
        # error, so the token never follows a redirect elsewhere.
        follow_redirects=False,
    ) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            yield streams


def _jsonrpc_message(content: bytes) -> Optional[dict[str, Any]]:
    try:
        message = json.loads(content)
    except ValueError:
        return None
    return message if isinstance(message, dict) and message.get("jsonrpc") == "2.0" else None


def _reply(request: httpx.Request, message: dict[str, Any], text: str) -> httpx.Response:
    """A JSON-RPC error for a request, or 202 Accepted for a message that gets no reply."""
    request_id = message.get("id")
    if "method" in message and isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
        error = {"jsonrpc": "2.0", "id": request_id, "error": {"code": SERVER_ERROR, "message": text}}
        return httpx.Response(200, json=error, request=request)
    return httpx.Response(202, request=request)


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").lower().startswith("application/json")


def _same_origin(url: httpx.URL, gateway: httpx.URL) -> bool:
    """The token goes only to the Gateway itself."""
    return (url.scheme, url.host, url.port) == (gateway.scheme, gateway.host, gateway.port)


def _error_code(exc: Exception) -> Optional[str]:
    """The AWS error code of a botocore ClientError, if that is what `exc` is."""
    response = getattr(exc, "response", None)
    return response.get("Error", {}).get("Code") if isinstance(response, dict) else None
