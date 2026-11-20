import json

import httpx
import pytest
import structlog
from aria_contracts import events
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient
from aria_contracts.identity import actor_id, new_session_id
import main
from config import get_config
from runtime_client import RuntimeClient

ALICE = actor_id({"iss": "https://issuer.example", "sub": "alice"})
BOB = actor_id({"iss": "https://issuer.example", "sub": "bob"})
SESSION = new_session_id(ALICE)
OLDER = new_session_id(ALICE)


def sse(*items) -> list[bytes]:
    """The Runtime's response bytes for these agent events, framed as the AgentCore SDK does."""
    return [f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode() for item in items]


def coded(code: str, message: str) -> dict:
    """An HTTP error body that carries one of the turn contract's error codes, as the web reads it."""
    assert code in events.ERROR_CODES
    return {"detail": {"code": code, "message": message}}


ANSWER = [events.text_delta("겨울"), events.done("end_turn")]


class FakeRuntime:
    chunks = []
    calls = []
    async def close(self): pass
    async def stop_session(self, session_id, token): pass
    async def invoke_stream(self, prompt, session_id, token):
        self.calls.append({"prompt": prompt, "session_id": session_id, "token": token})
        for chunk in self.chunks:
            if isinstance(chunk, BaseException): raise chunk
            yield chunk


class FakeSessions:
    enabled = True
    def list_sessions(self, actor): return [SESSION]
    def list_session_summaries(self, actor):
        return [{"sessionId": SESSION, "createdAt": "2026-11-18T09:30:00Z"}, {"sessionId": OLDER}]
    def list_messages(self, actor, session): return [{"role": "user", "content": "Hello"}]
    def delete_session(self, actor, session): pass


class RecordingLogger:
    def __init__(self):
        self.records = []
    def _record(self, level, event, **fields):
        self.records.append({"level": level, "event": event, **structlog.contextvars.get_contextvars(), **fields})
    def info(self, event, **fields): self._record("info", event, **fields)
    def warning(self, event, **fields): self._record("warning", event, **fields)
    def error(self, event, **fields): self._record("error", event, **fields)
    def exception(self, event, **fields): self._record("error", event, **fields)
    def named(self, name): return [r for r in self.records if r["event"] == name]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/test")
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("OIDC_ALLOWED_CLIENTS", "web")
    get_config.cache_clear()
    runtime = FakeRuntime()
    runtime.chunks, runtime.calls = [], []
    log = RecordingLogger()
    monkeypatch.setattr(main, "RuntimeClient", lambda: runtime)
    monkeypatch.setattr(main, "SessionStore", FakeSessions)
    monkeypatch.setattr(main, "logger", log)
    # Signature/claim validation is separately exercised using real RSA JWTs.
    def verified(authorization: str = Header(default="")):
        if authorization not in ("Bearer alice", "Bearer bob"): raise HTTPException(401)
        return {"token": authorization, "actor_id": ALICE if authorization.endswith("alice") else BOB}
    main.app.dependency_overrides[main.require_caller] = verified
    with TestClient(main.app) as c:
        c.runtime = runtime
        c.log = log
        yield c
    main.app.dependency_overrides.clear()
    get_config.cache_clear()


def invoke(client, **body):
    return client.post("/v1/invocations", headers={"Authorization": "Bearer alice"}, json={"session_id": SESSION, "message": "hi", **body})


def frames(response) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in response.text.split("\n\n") if line.startswith("data: ")]


def test_utf8_split_and_versioned_stream(client):
    client.runtime.chunks = [bytes([b]) for chunk in sse(*ANSWER) for b in chunk]
    response = invoke(client)
    assert "겨울" in response.text and '"version": 1' in response.text
    assert frames(response) == ANSWER
    # Only the token, session and prompt reach the Runtime: no browser-supplied identity.
    assert client.runtime.calls == [{"prompt": "hi", "session_id": SESSION, "token": "Bearer alice"}]  # nosec B105 # a fake bearer token


def test_a_finished_invocation_is_logged_with_its_session_status_and_first_byte(client):
    client.runtime.chunks = sse(*ANSWER)
    invoke(client)
    [finished] = client.log.named("invocation_finished")
    assert (finished["session_id"], finished["status"]) == (SESSION, "ok")
    assert isinstance(finished["ttfb_ms"], int) and isinstance(finished["duration_ms"], int)
    assert client.log.named("agent_error") == []


def test_an_agent_error_is_relayed_and_logged(client):
    client.runtime.chunks = sse(events.text_delta("찾아"), events.error("throttled", "The model is throttling requests"))
    response = invoke(client)
    assert frames(response)[-1] == events.error("throttled", "The model is throttling requests")
    [agent_error] = client.log.named("agent_error")
    assert (agent_error["code"], agent_error["session_id"]) == ("throttled", SESSION)
    assert client.log.named("invocation_finished")[0]["status"] == "error"
    # The agent reported it; the Runtime call itself worked.
    assert client.log.named("runtime invocation failed") == []


def test_the_sdks_error_shape_reaches_the_shopper_as_an_error(client):
    client.runtime.chunks = sse(events.text_delta("찾아"), {"error": "boom", "error_type": "KeyError", "message": "An error occurred during streaming"})
    assert frames(invoke(client))[-1]["data"]["code"] == "internal"


@pytest.mark.parametrize(
    "failure,code,message",
    [
        (httpx.HTTPStatusError("429", request=httpx.Request("POST", "https://x"), response=httpx.Response(429)), "upstream", "Runtime returned HTTP 429"),
        (httpx.ConnectError("unreachable"), "upstream", "Runtime request failed"),
        (ValueError("Oversized SSE frame"), "upstream", "Runtime request failed"),
        (TimeoutError(), "deadline", "Request deadline exceeded"),
        # httpx's own timeouts aren't TimeoutError.
        (httpx.ConnectTimeout("slow"), "deadline", "Runtime request timed out (ConnectTimeout)"),
    ],
)
def test_a_failed_runtime_call_is_a_coded_error(client, failure, code, message):
    client.runtime.chunks = [failure]
    assert frames(invoke(client)) == [events.error(code, message)]
    [failed] = client.log.named("runtime invocation failed")
    assert failed["session_id"] == SESSION
    assert client.log.named("agent_error")[0]["code"] == code


def test_a_runtime_read_timeout_is_a_deadline_error(client, monkeypatch):
    async def body():
        yield sse(events.text_delta("찾아"))[0]
        raise httpx.ReadTimeout("no data from the Runtime")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body()))
    monkeypatch.setattr(main, "_runtime", RuntimeClient(transport))
    deadline = events.error("deadline", "Runtime request timed out (ReadTimeout)")
    assert frames(invoke(client)) == [events.text_delta("찾아"), deadline]


def test_a_stream_that_ends_without_a_result_is_upstream(client):
    client.runtime.chunks = sse(events.text_delta("찾아"))
    assert frames(invoke(client)) == [events.text_delta("찾아"), events.error("upstream", "Agent stream ended without a result")]
    assert client.log.named("runtime invocation failed")[0]["reason"] == "stream ended without a result"


def test_a_failure_after_the_answer_adds_no_second_terminal_frame(client):
    client.runtime.chunks = [*sse(*ANSWER), httpx.ReadError("connection reset")]
    assert frames(invoke(client)) == ANSWER
    assert client.log.named("invocation_finished")[0]["status"] == "ok"


@pytest.mark.parametrize("path,method", [("/v1/invocations", "post"), (f"/v1/sessions/{SESSION}/messages", "get"), (f"/v1/sessions/{SESSION}", "delete")])
def test_cross_user_denied(client, path, method):
    kwargs = {"json": {"message": "hi", "session_id": SESSION}} if method == "post" else {}
    response = getattr(client, method)(path, headers={"Authorization": "Bearer bob"}, **kwargs)
    assert response.status_code == 403
    # Coded, so the web offers a new conversation: signing in again can't help.
    assert response.json() == coded("owner_mismatch", "Session does not belong to this caller")
    assert client.runtime.calls == []


def test_sessions_require_identity_and_generate_bound_ids(client):
    assert client.post("/v1/sessions").status_code == 401
    created = client.post("/v1/sessions", headers={"Authorization": "Bearer alice"}).json()["session_id"]
    assert created.startswith(ALICE + "_") and len(created) == 97


def test_the_session_list_labels_each_session_with_its_creation_time(client):
    response = client.get("/v1/sessions", headers={"Authorization": "Bearer alice"})
    assert response.json() == [
        {"session_id": SESSION, "createdAt": "2026-11-18T09:30:00Z"},
        {"session_id": OLDER},
    ]


def test_nonstreaming_error_is_not_empty_success(client):
    client.runtime.chunks = [RuntimeError("secret internal detail")]
    response = invoke(client, stream=False)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "upstream"
    assert "secret" not in response.text


def test_nonstreaming_text(client):
    client.runtime.chunks = sse(events.text_delta("hel"), events.text_delta("lo"), events.done("end_turn"))
    assert invoke(client, stream=False).json() == {"data": "hello", "stopReason": "end_turn"}


def test_nonstreaming_guardrail_reply_is_the_replacement_not_the_streamed_text(client):
    replacement = "이 요청에는 답변할 수 없어요."
    client.runtime.chunks = sse(events.text_delta("blocked words"), events.done(events.GUARDRAIL_INTERVENED, replacement))
    assert invoke(client, stream=False).json() == {"data": replacement, "stopReason": events.GUARDRAIL_INTERVENED}
    client.runtime.chunks = sse(events.text_delta("blocked words"), events.done(events.GUARDRAIL_INTERVENED))
    assert invoke(client, stream=False).json() == {"data": "", "stopReason": events.GUARDRAIL_INTERVENED}


def test_every_error_code_has_a_status_for_nonstreaming_callers():
    assert set(main.SYNC_ERROR_STATUS) == set(events.ERROR_CODES)


@pytest.mark.parametrize("code", sorted(events.ERROR_CODES))
def test_nonstreaming_errors_carry_their_code_and_status(client, code):
    client.runtime.chunks = sse(events.text_delta("partial"), events.error(code, "developer detail"))
    response = invoke(client, stream=False)
    assert response.status_code == main.SYNC_ERROR_STATUS[code]
    assert response.json() == coded(code, "developer detail")


def test_oversized_prompt_rejected(client):
    response = invoke(client, message="x" * 8001)
    assert response.status_code == 422
    message = "Invalid request: body.message: String should have at most 8000 characters"
    assert response.json() == coded("invalid_request", message)


@pytest.mark.parametrize("body", [{"message": ""}, {"session_id": "s" * 33}, {"stream": "sometimes"}])
def test_an_invalid_invocation_is_a_coded_422(client, body):
    response = invoke(client, **body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert client.runtime.calls == []


def test_a_body_that_is_not_json_is_a_coded_422(client):
    headers = {"Authorization": "Bearer alice", "Content-Type": "application/json"}
    response = client.post("/v1/invocations", headers=headers, content=b'{"message": ')
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_an_oversized_body_is_a_coded_413(client):
    response = client.post("/v1/invocations", headers={"Authorization": "Bearer alice"}, content=b"x" * 32769)
    assert response.status_code == 413
    assert response.json() == coded("invalid_request", "Request body too large")


def test_a_user_id_in_the_body_is_ignored_and_never_chooses_the_caller(client):
    client.runtime.chunks = sse(*ANSWER)
    response = invoke(client, user_id="bob")
    assert frames(response) == ANSWER
    assert client.runtime.calls == [{"prompt": "hi", "session_id": SESSION, "token": "Bearer alice"}]  # nosec B105 # a fake bearer token


def test_ping(client):
    assert client.get("/ping").json() == {"status": "ok"}


async def test_a_browser_that_goes_away_closes_the_runtime_stream(monkeypatch):
    closed = []

    class Runtime:
        async def invoke_stream(self, prompt, session_id, token):
            try:
                for chunk in sse(events.text_delta("a"), events.text_delta("b"), events.done("end_turn")):
                    yield chunk
            finally:
                closed.append(True)

    log = RecordingLogger()
    monkeypatch.setattr(main, "_runtime", Runtime())
    monkeypatch.setattr(main, "logger", log)
    stream = main._event_stream("hi", SESSION, {"token": "Bearer alice"})  # nosec B105 # a fake bearer token
    assert json.loads((await anext(stream))[len("data: "):]) == events.text_delta("a")
    await stream.aclose()
    assert closed == [True]
    assert log.named("invocation_finished")[0]["status"] == "cancelled"


async def test_every_invocation_log_line_carries_the_session_id(monkeypatch):
    class Runtime:
        async def invoke_stream(self, prompt, session_id, token):
            raise httpx.ConnectError("unreachable")
            yield  # pragma: no cover

    log = RecordingLogger()
    monkeypatch.setattr(main, "_runtime", Runtime())
    monkeypatch.setattr(main, "logger", log)
    structlog.contextvars.clear_contextvars()
    [frame async for frame in main._event_stream("hi", SESSION, {"token": "Bearer alice"})]  # nosec B105 # a fake bearer token
    assert {r["event"] for r in log.records} == {"runtime invocation failed", "agent_error", "invocation_finished"}
    # Bound by the stream itself, so the logs of a StreamingResponse task carry it too.
    assert all(r["session_id"] == SESSION for r in log.records)


def test_the_memory_for_history_comes_from_agentcore_memory_id(monkeypatch):
    # Every deployment sets AGENTCORE_MEMORY_ID; the session list and history read that Memory.
    from config import ApiConfig

    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-1")
    assert ApiConfig.from_env().memory_id == "mem-1"
