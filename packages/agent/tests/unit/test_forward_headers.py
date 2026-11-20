"""Entrypoint wiring for custom-header propagation, driven locally (no AWS).

Proves the full agent-side path: the entrypoint reads allowlisted headers off
the request, hands them to build() (Gateway path) and to stream_async as
`forwarded_headers` invocation state (local-tool path).
"""

import base64
import json
from types import SimpleNamespace

import pytest

import app
from aria_contracts.identity import actor_id, new_session_id


def _token(sub: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": "https://issuer.example", "sub": sub}).encode()
    ).decode().rstrip("=")
    return f"Bearer header.{payload}.signature"


@pytest.mark.asyncio
async def test_allowlisted_headers_flow_to_build_and_stream(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    alice = actor_id({"iss": "https://issuer.example", "sub": "alice"})
    context = SimpleNamespace(
        session_id=new_session_id(alice),
        request_headers={
            "Authorization": _token("alice"),
            "X-Tenant-Id": "acme",     # mixed casing, allowlisted
            "x-locale": "ko-KR",
            "x-not-allowed": "secret",  # present but not allowlisted -> dropped
        },
    )
    captured = {}

    class FakeSession:
        actor_id = alice
        turns = 0
        in_flight: set = set()

        def refresh_identity(self, workload_token):
            captured["refreshed"] = workload_token

        async def stream_async(self, prompt, **kwargs):
            captured["stream"] = kwargs
            yield {"data": "ok", "delta": {"text": "ok"}, **kwargs["invocation_state"]}
            yield {"result": SimpleNamespace(stop_reason="end_turn", message={"role": "assistant", "content": [{"text": "ok"}]})}

    def fake_build(token, session_id, forward_headers=None, workload_token=None):
        captured["build"] = forward_headers
        captured["workload_token"] = workload_token
        return FakeSession()

    fake_builder = SimpleNamespace(
        config=SimpleNamespace(forward_headers=("x-tenant-id", "x-locale"), max_turns=100, request_timeout=120),
        build=fake_build,
    )
    monkeypatch.setattr(app, "_session", None)
    monkeypatch.setattr(app, "_builder", fake_builder)
    from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

    BedrockAgentCoreContext.set_workload_access_token("wat-for-alice")

    events = [e async for e in app.invoke({"prompt": "hi"}, context)]

    # The forwarded headers ride the invocation state, which never leaves the container.
    assert events == [
        {"version": 1, "type": "textDelta", "data": {"text": "ok"}},
        {"version": 1, "type": "done", "data": {"stopReason": "end_turn"}},
    ]
    # The Gateway token is minted from the Runtime's workload token, not the shopper's JWT.
    assert captured["workload_token"] == captured["refreshed"] == "wat-for-alice"
    expected = {"x-tenant-id": "acme", "x-locale": "ko-KR"}   # x-not-allowed excluded
    assert captured["build"] == expected                      # Gateway path
    # local-tool path: headers ride invocation_state (Strands 1.55 API)
    assert captured["stream"]["invocation_state"] == {"forwarded_headers": expected}
    assert set(captured["stream"]) == {"invocation_state", "cancel_signal"}
