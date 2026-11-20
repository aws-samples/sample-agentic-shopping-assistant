"""The Gateway transport: a minted token on every request, and HTTP failures kept to one call.

The MCP tests drive Strands' MCPClient and mcp's streamable HTTP client against a fake
Gateway behind httpx.MockTransport, so they exercise the paths where mcp, without this
transport, would close the whole session on one bad reply.
"""

import httpx
import pytest
from strands.tools.mcp import MCPClient

from core.transport import GatewayTransport, gateway_transport
from fake_gateway import TOOL, FakeGateway

GATEWAY_URL = "https://gw-1.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"


class Tokens:
    """A token source that mints a new token per call, and can fail the next one."""

    def __init__(self):
        self.minted = 0
        self.fail_next = False

    def bearer(self):
        if self.fail_next:
            self.fail_next = False
            raise PermissionError("No workload access token for this request; cannot call Gateway tools")
        self.minted += 1
        return f"Bearer minted-{self.minted}"


class BrokenBody(httpx.AsyncByteStream):
    """A response body whose connection drops halfway through."""

    async def __aiter__(self):
        yield b'{"jsonrpc": "2.0", '
        raise httpx.ReadError("connection reset")


class Log:
    def __init__(self):
        self.events = []

    def __getattr__(self, level):
        return lambda event, **fields: self.events.append((level, event, fields))


@pytest.fixture
def log(monkeypatch):
    recorder = Log()
    monkeypatch.setattr("core.transport.logger", recorder)
    return recorder


def connect(gateway, tokens, headers=None):
    return MCPClient(
        lambda: gateway_transport(GATEWAY_URL, tokens, headers or {}, inner=httpx.MockTransport(gateway)),
        startup_timeout=10,
    )


class TestSessionSurvivesGatewayErrors:
    def test_an_http_error_fails_only_its_own_tool_call(self, log):
        # If the 500 reached mcp, it would raise and close the session, and the second call
        # would fail with "the client session is not running".
        gateway = FakeGateway(500, "2 saved items")
        with connect(gateway, Tokens()) as client:
            first = client.call_tool_sync("call-1", TOOL, {})
            second = client.call_tool_sync("call-2", TOOL, {})
        assert first["status"] == "error"
        assert first["content"] == [{"text": "Tool execution failed: Gateway returned HTTP 500"}]
        assert second["status"] == "success" and second["content"] == [{"text": "2 saved items"}]
        assert ("warning", "gateway_http_error", {"status": 500, "method": "tools/call"}) in log.events

    def test_an_interceptor_refusal_reaches_the_model_as_a_refusal(self, log):
        # The interceptor answers a refused call with a JSON-RPC error over HTTP 200. It passes
        # through unchanged, so the model reads a refusal rather than an outage.
        refusal = {"code": -32003, "message": "Access denied by the Gateway's access check; retrying won't help"}
        gateway = FakeGateway(refusal, "2 saved items")
        with connect(gateway, Tokens()) as client:
            first = client.call_tool_sync("call-1", TOOL, {})
            second = client.call_tool_sync("call-2", TOOL, {})
        assert first["status"] == "error"
        assert first["content"] == [{"text": f"Tool execution failed: {refusal['message']}"}]
        assert second["status"] == "success"
        assert not [event for event in log.events if event[1] == "gateway_http_error"]

    def test_a_failed_token_mint_fails_only_its_own_tool_call(self, log):
        tokens = Tokens()
        # Arm the failure once the session is up, so it hits the first tool call.
        gateway = FakeGateway("2 saved items", on_initialized=lambda: setattr(tokens, "fail_next", True))
        with connect(gateway, tokens) as client:
            first = client.call_tool_sync("call-1", TOOL, {})
            second = client.call_tool_sync("call-2", TOOL, {})
        assert first["status"] == "error"
        assert first["content"] == [{"text": "Tool execution failed: Could not authorize the Gateway request"}]
        assert second["status"] == "success"
        # The call without a token never reached the Gateway.
        assert gateway.methods().count("tools/call") == 1
        assert ("error", "gateway_token_failed", {"error_type": "PermissionError", "code": None}) in log.events

    @pytest.mark.parametrize("failure", ["timeout", "broken body", "undecodable body"])
    def test_a_network_error_fails_only_its_own_tool_call(self, log, failure):
        gateway, failed = FakeGateway("2 saved items"), []

        def flaky(request):
            if request.content and b'"tools/call"' in request.content and not failed:
                failed.append(request)
                if failure == "timeout":
                    raise httpx.ReadTimeout("timed out", request=request)
                if failure == "undecodable body":
                    # A body that claims gzip but isn't: reading it raises httpx.DecodingError.
                    return httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"},
                                          content=b"not gzip")
                return httpx.Response(200, headers={"content-type": "application/json"}, stream=BrokenBody())
            return gateway(request)

        with connect(flaky, Tokens()) as client:
            first = client.call_tool_sync("call-1", TOOL, {})
            second = client.call_tool_sync("call-2", TOOL, {})
        error = {"timeout": "ReadTimeout", "broken body": "ReadError", "undecodable body": "DecodingError"}[failure]
        assert first["content"] == [{"text": f"Tool execution failed: Gateway request failed ({error})"}]
        assert second["status"] == "success"
        assert ("warning", "gateway_request_failed", {"error_type": error, "method": "tools/call"}) in log.events

    def test_an_http_error_on_a_notification_does_not_close_the_session(self, log):
        # A notification is sent inline by mcp's writer, so raising there would end the writer.
        gateway = FakeGateway("2 saved items", initialized_status=503)
        with connect(gateway, Tokens()) as client:
            result = client.call_tool_sync("call-1", TOOL, {})
        assert result["status"] == "success"
        assert ("warning", "gateway_http_error", {"status": 503, "method": "notifications/initialized"}) in log.events

    def test_every_gateway_request_carries_a_minted_token_and_the_custom_headers(self):
        gateway = FakeGateway("2 saved items")
        with connect(gateway, Tokens(), headers={"x-aria-actor-id": "a" * 64}) as client:
            client.call_tool_sync("call-1", TOOL, {})
        assert [request.headers["authorization"] for request in gateway.requests] == [
            f"Bearer minted-{n}" for n in range(1, len(gateway.requests) + 1)
        ]
        assert all(request.headers["x-aria-actor-id"] == "a" * 64 for request in gateway.requests)


class TestGatewayTransport:
    async def send(self, handle, tokens, method="POST", url=GATEWAY_URL, body=None):
        transport = GatewayTransport(tokens, GATEWAY_URL, httpx.MockTransport(handle))
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.request(method, url, json=body)

    async def test_every_request_asks_the_token_source(self):
        # HTTPX copies headers when a client is created, so the token must be read per request,
        # or a refreshed token would never be sent.
        observed = []

        def handle(request):
            observed.append(request.headers["authorization"])
            return httpx.Response(200)

        transport = GatewayTransport(Tokens(), GATEWAY_URL, httpx.MockTransport(handle))
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get(GATEWAY_URL)
            await client.get(GATEWAY_URL)
        assert observed == ["Bearer minted-1", "Bearer minted-2"]

    async def test_the_token_goes_only_to_the_gateway(self):
        seen = {}

        def handle(request):
            seen.update(request.headers)
            return httpx.Response(200)

        await self.send(handle, Tokens(), method="GET", url="https://elsewhere.example/mcp")
        assert "authorization" not in seen

    async def test_a_request_error_becomes_a_jsonrpc_error_for_its_id(self, log):
        response = await self.send(lambda request: httpx.Response(403), Tokens(),
                                   body={"jsonrpc": "2.0", "id": 7, "method": "tools/call"})
        assert response.status_code == 200
        error = {"code": -32000, "message": "Gateway returned HTTP 403"}
        assert response.json() == {"jsonrpc": "2.0", "id": 7, "error": error}

    async def test_a_notification_error_is_acknowledged(self, log):
        response = await self.send(lambda request: httpx.Response(500), Tokens(),
                                   body={"jsonrpc": "2.0", "method": "notifications/cancelled"})
        assert response.status_code == 202

    async def test_other_requests_keep_their_status_and_mint_errors(self, log):
        # The SSE GET and session DELETE already handle their own errors.
        response = await self.send(lambda request: httpx.Response(405), Tokens(), method="GET")
        assert response.status_code == 405
        tokens = Tokens()
        tokens.fail_next = True
        with pytest.raises(PermissionError):
            await self.send(lambda request: httpx.Response(200), tokens, method="GET")

    async def test_success_passes_through(self):
        reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
        response = await self.send(lambda request: httpx.Response(200, json=reply), Tokens(),
                                   body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.json() == reply
