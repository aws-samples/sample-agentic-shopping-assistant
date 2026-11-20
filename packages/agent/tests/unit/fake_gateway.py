"""A minimal MCP server standing in for the AgentCore Gateway, for httpx.MockTransport."""

import json

import httpx

TOOL = "saved-items___get_saved_items"


class FakeGateway:
    """Answers initialize, tools/list and tools/call with JSON responses.

    `call_replies` scripts tools/call: a string is the tool's text result, an int is an HTTP
    error status, and a dict is a JSON-RPC error answered over HTTP 200, as the interceptor
    answers a call it refuses. `initialized_status` is the reply to notifications/initialized, and
    `on_initialized` runs when that notification arrives.
    """

    def __init__(self, *call_replies, initialized_status=202, on_initialized=None):
        self.call_replies = list(call_replies)
        self.initialized_status = initialized_status
        self.on_initialized = on_initialized
        self.requests = []

    def methods(self):
        return [json.loads(request.content).get("method") for request in self.requests]

    def __call__(self, request):
        self.requests.append(request)
        body = json.loads(request.content)
        if body.get("method") == "notifications/initialized":
            if self.on_initialized:
                self.on_initialized()
            return httpx.Response(self.initialized_status)
        if "id" not in body:
            return httpx.Response(202)
        method = body["method"]
        if method == "initialize":
            result = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-gateway", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": TOOL, "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            reply = self.call_replies.pop(0)
            if isinstance(reply, int):
                return httpx.Response(reply, json={"message": "An internal error occurred. Please retry later."})
            if isinstance(reply, dict):
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": reply})
            result = {"content": [{"type": "text", "text": reply}], "isError": False}
        else:
            return httpx.Response(400)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
