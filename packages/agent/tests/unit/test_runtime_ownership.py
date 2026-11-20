import base64
import json
from types import SimpleNamespace
import pytest
import app
from aria_contracts.identity import actor_id, new_session_id


def bearer(sub: str) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps({"iss": "https://issuer.example", "sub": sub}).encode()).decode().rstrip("=")
    return f"Bearer header.{encoded}.signature"


OWNER_MISMATCH = {"version": 1, "type": "error", "data": {"code": "owner_mismatch", "retryable": False, "message": "Session does not belong to this caller"}}


@pytest.mark.asyncio
async def test_direct_runtime_rejects_other_owner_before_build(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    alice = actor_id({"iss": "https://issuer.example", "sub": "alice"})
    context = SimpleNamespace(session_id=new_session_id(alice), request_headers={"Authorization": bearer("bob")})
    monkeypatch.setattr(app, "_session", None)
    monkeypatch.setattr(app, "_builder", SimpleNamespace(build=lambda *a: pytest.fail("Must reject before constructing agent or memory")))
    result = [event async for event in app.invoke({"prompt": "hello"}, context)]
    assert result == [OWNER_MISMATCH]


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", ["", "Bearer not-a-jwt", bearer("")])
async def test_a_request_without_a_verified_caller_is_rejected(monkeypatch, authorization):
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    alice = actor_id({"iss": "https://issuer.example", "sub": "alice"})
    context = SimpleNamespace(session_id=new_session_id(alice), request_headers={"Authorization": authorization})
    monkeypatch.setattr(app, "_session", None)
    monkeypatch.setattr(app, "_builder", SimpleNamespace(build=lambda *a: pytest.fail("Must reject before constructing agent or memory")))
    assert [event async for event in app.invoke({"prompt": "hello"}, context)] == [OWNER_MISMATCH]


@pytest.mark.asyncio
async def test_a_cached_session_rejects_another_principal(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    bob = actor_id({"iss": "https://issuer.example", "sub": "bob"})
    alice = actor_id({"iss": "https://issuer.example", "sub": "alice"})
    # Bob's own session ID, but the container already serves Alice.
    context = SimpleNamespace(session_id=new_session_id(bob), request_headers={"Authorization": bearer("bob")})
    monkeypatch.setattr(app, "_session", SimpleNamespace(actor_id=alice, stream_async=lambda *a, **k: pytest.fail("Must not run")))
    monkeypatch.setattr(app, "_builder", SimpleNamespace(build=lambda *a: pytest.fail("Must not rebuild")))
    assert [event async for event in app.invoke({"prompt": "hello"}, context)] == [OWNER_MISMATCH]
