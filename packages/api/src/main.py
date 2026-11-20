"""Web-facing API in front of the AgentCore Runtime.

Endpoints:
  POST   /v1/invocations                       -> SSE textDelta/toolUse/toolResult, then done or error
  POST   /v1/sessions                          -> new session id
  GET    /v1/sessions                          -> caller's sessions, newest first, with createdAt
  GET    /v1/sessions/{session_id}/messages    -> conversation history
  DELETE /v1/sessions/{session_id}             -> delete a session
  DELETE /v1/memory                            -> delete every session and saved memory of the caller
  GET    /ping                                 -> health check

Identity comes only from the verified access token, never from a user ID the client sends.
The SSE events are `aria_contracts.events`; see events.py.
"""

from aria_contracts import events
from aria_contracts.identity import new_session_id, require_session_owner
import asyncio
import time
from contextlib import aclosing, asynccontextmanager
from typing import Any, AsyncIterator, Optional

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from auth import require_caller
from config import get_config
from events import Relay, SSEDecoder, parse_sse_line, sse
from starlette.concurrency import run_in_threadpool
from logger import logger
from runtime_client import RuntimeClient
from sessions import SessionStore
from middleware import BodyLimit

_runtime: Optional[RuntimeClient] = None
_sessions: Optional[SessionStore] = None


class InvokeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(min_length=97, max_length=97)
    stream: bool = True


class SessionResponse(BaseModel):
    session_id: str


class SessionSummary(BaseModel):
    session_id: str
    # When Memory reports it: ISO 8601, UTC.
    createdAt: Optional[str] = None


class MessageResponse(BaseModel):
    role: str
    content: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _runtime, _sessions
    get_config().validate()
    _runtime = RuntimeClient()
    _sessions = SessionStore()
    logger.info("api started", memory_configured=_sessions.enabled)
    yield
    await _runtime.close()


app = FastAPI(title="NORDLY Assistant API", version="0.1.0", lifespan=lifespan)

app.add_middleware(BodyLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_config().cors_allow_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# Error bodies that the web acts on carry a code from `aria_contracts.events.ERROR_CODES`:
# {"detail": {"code": ..., "message": ...}}. The message is developer detail, never shown.
def check_owner(session_id: str, caller: dict[str, str]) -> None:
    try:
        require_session_owner(session_id, caller["actor_id"])
    except ValueError:
        # Another caller's session, or an ID bound to no caller: the web offers a new conversation, not a new sign-in.
        detail = {"code": "owner_mismatch", "message": "Session does not belong to this caller"}
        raise HTTPException(403, detail=detail) from None


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """A body FastAPI rejects (not JSON, a field missing, a prompt over 8000 characters): 422, coded."""
    problems = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors())
    detail = {"code": "invalid_request", "message": f"Invalid request: {problems}"}
    return JSONResponse({"detail": detail}, status_code=422)


@app.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "ok"}


def _failure(exc: Exception) -> tuple[str, str]:
    """The error code and developer message for a failed Runtime call."""
    if isinstance(exc, TimeoutError):
        return "deadline", "Request deadline exceeded"
    if isinstance(exc, httpx.TimeoutException):
        # httpx's own timeouts aren't TimeoutError.
        return "deadline", f"Runtime request timed out ({type(exc).__name__})"
    if isinstance(exc, httpx.HTTPStatusError):
        return "upstream", f"Runtime returned HTTP {exc.response.status_code}"
    return "upstream", "Runtime request failed"


async def _event_stream(prompt: str, session_id: str, caller: dict[str, str]) -> AsyncIterator[str]:
    """Bridge AgentCore's SSE stream to the client's event contract: one terminal frame per turn."""
    assert _runtime is not None
    structlog.contextvars.bind_contextvars(session_id=session_id)
    started = time.monotonic()
    ttfb_ms: Optional[int] = None
    status = "ok"
    relay = Relay()
    decoder = SSEDecoder()

    def frames(found: list[dict]) -> list[str]:
        nonlocal ttfb_ms, status
        if found and ttfb_ms is None:
            ttfb_ms = round((time.monotonic() - started) * 1000)
        for event in found:
            if event["type"] == "error":
                status = "error"
                logger.warning("agent_error", code=event["data"]["code"], session_id=session_id)
        return [sse(event) for event in found]

    try:
        async with aclosing(_runtime.invoke_stream(prompt=prompt, session_id=session_id, token=caller["token"])) as chunks:
            async for raw in chunks:
                for payload in decoder.feed(raw):
                    for frame in frames(relay.feed(payload)):
                        yield frame
        for payload in decoder.feed(b"", final=True):
            for frame in frames(relay.feed(payload)):
                yield frame
        tail = relay.finish()
        if tail:
            logger.error("runtime invocation failed", session_id=session_id, reason="stream ended without a result")
        for frame in frames(tail):
            yield frame
    except (asyncio.CancelledError, GeneratorExit):
        status = "cancelled"
        raise
    except Exception as exc:
        logger.error("runtime invocation failed", session_id=session_id, exc_info=True)
        for frame in frames(relay.fail(*_failure(exc))):
            yield frame
    finally:
        logger.info("invocation_finished", session_id=session_id, status=status, ttfb_ms=ttfb_ms,
                    duration_ms=round((time.monotonic() - started) * 1000))


# The HTTP status of a turn that failed, for a caller that asked for one JSON reply. The body
# carries the code, as every coded API error does.
SYNC_ERROR_STATUS = {
    "init_failed": 503,
    "owner_mismatch": 403,
    "turn_limit": 409,
    "deadline": 504,
    "busy": 409,
    "throttled": 429,
    "internal": 502,
    "upstream": 502,
    "invalid_request": 422,
}


async def _collect(prompt: str, session_id: str, caller: dict[str, str]) -> dict:
    """The turn as one JSON reply: its text and stop reason, or its error as an HTTP error."""
    text: list[str] = []
    async for frame in _event_stream(prompt, session_id, caller):
        payload = parse_sse_line(frame)
        kind, data = (payload.get("type"), payload.get("data") or {}) if isinstance(payload, dict) else (None, {})
        if kind == events.TEXT_DELTA:
            text.append(data.get("text", ""))
        elif kind == events.ERROR:
            code = data.get("code")
            raise HTTPException(SYNC_ERROR_STATUS.get(code, 502), detail={"code": code, "message": data.get("message", "")})
        elif kind == events.DONE:
            stop_reason = data.get("stopReason")
            if stop_reason == events.GUARDRAIL_INTERVENED:
                # The guardrail's replacement is the whole answer: text streamed before the
                # intervention may be what it blocked.
                return {"data": data.get("message") or "", "stopReason": stop_reason}
            return {"data": "".join(text), "stopReason": stop_reason}
    raise HTTPException(502, detail={"code": "upstream", "message": "Agent stream ended without a result"})


@app.post("/v1/invocations")
async def invoke(request: InvokeRequest, caller: dict[str, str] = Depends(require_caller)) -> Any:
    structlog.contextvars.bind_contextvars(session_id=request.session_id)
    check_owner(request.session_id, caller)
    if not get_config().agent_runtime_arn:
        raise HTTPException(status_code=503, detail="AGENT_RUNTIME_ARN not configured")

    if not request.stream:
        return await _collect(request.message, request.session_id, caller)

    return StreamingResponse(
        _event_stream(request.message, request.session_id, caller),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/sessions", response_model=SessionResponse)
async def create_session(caller: dict[str, str] = Depends(require_caller)) -> SessionResponse:
    # Bound to the caller: their actor ID, "_", then 32 random hex characters (aria_contracts.identity).
    # 97 characters fit both the Runtime (at least 33) and Memory session ID limits.
    return SessionResponse(session_id=new_session_id(caller["actor_id"]))


@app.get("/v1/sessions", response_model=list[SessionSummary], response_model_exclude_none=True)
async def list_sessions(caller: dict[str, str] = Depends(require_caller)) -> list[SessionSummary]:
    assert _sessions is not None
    summaries = await run_in_threadpool(_sessions.list_session_summaries, caller["actor_id"])
    return [SessionSummary(session_id=s["sessionId"], createdAt=s.get("createdAt")) for s in summaries]


@app.get("/v1/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: str, caller: dict[str, str] = Depends(require_caller)
) -> list[MessageResponse]:
    assert _sessions is not None
    check_owner(session_id, caller)
    messages = await run_in_threadpool(_sessions.list_messages, caller["actor_id"], session_id)
    return [MessageResponse(**message) for message in messages]


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, caller: dict[str, str] = Depends(require_caller)) -> dict[str, str]:
    assert _sessions is not None
    check_owner(session_id, caller)
    assert _runtime is not None
    try:
        await _runtime.stop_session(session_id, caller["token"])
        await run_in_threadpool(_sessions.delete_session, caller["actor_id"], session_id)
    except Exception:
        logger.exception("session deletion incomplete", session_id=session_id)
        raise HTTPException(502, "Deletion incomplete. Retry to finish deleting the conversation.") from None
    return {"message": "Conversation events and current summary deleted. Saved preferences are retained."}


@app.delete("/v1/memory")
async def forget_memory(caller: dict[str, str] = Depends(require_caller)):
    assert _sessions is not None and _runtime is not None
    try:
        ids = await run_in_threadpool(_sessions.list_sessions, caller["actor_id"])
        for sid in ids:
            check_owner(sid, caller)
            await _runtime.stop_session(sid, caller["token"])
            await run_in_threadpool(_sessions.delete_session, caller["actor_id"], sid)
        await run_in_threadpool(_sessions.forget_preferences, caller["actor_id"])
    except Exception:
        logger.exception("memory deletion incomplete")
        raise HTTPException(502, "Memory deletion incomplete. Retry to finish deletion.") from None
    return {"message": "Current conversation events and saved memories deleted", "verificationRequired": True,
            "detail": "Asynchronous memory extraction may still be pending. Follow the deletion verification runbook before certifying erasure."}
