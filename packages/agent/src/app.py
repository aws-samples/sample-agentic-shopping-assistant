"""AgentCore Runtime entrypoint.

VM-per-session: the builder is created once at container startup, the Session
is created lazily on the first request and reused for the container's lifetime.
Each request hands the session its workload access token, which the Gateway token
is minted from. The shopper's sign-in JWT never leaves the agent: the Runtime
validates it, and the agent reads it only for the actor ID and session ownership.

Every invocation streams `aria_contracts.events` and ends with exactly one
`done` or `error`. `for_client()` maps Strands events onto them; nothing else
leaves the container. The session build and the turn share one deadline,
`REQUEST_TIMEOUT_SECONDS - 10`, so the agent's own coded error reaches the API
before the API stops waiting.

There is no shutdown path — AgentCore hard-kills containers with no SIGTERM,
so nothing important may live in lifespan teardown.
"""

import asyncio
import hashlib
import os
import threading
import time
# Keep AgentCore's exporter/routing configuration. Only content capture is gated.
if os.environ.get("TRACE_CAPTURE_CONTENT", "false").lower() != "true":
    existing = os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "")
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = existing + ",gen_ai_unredacted_attributes="
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"

from contextlib import asynccontextmanager
from typing import Any, Iterator, Optional

import structlog
from aria_contracts import events
from aria_contracts.identity import require_session_owner
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.types.exceptions import ConcurrencyException, MaxTokensReachedException, ModelThrottledException

from core.builder import SessionBuilder, resolve_actor_id
from core.config import AgentConfig
from core.safety import BUDGET_TURN_END
from core.session import Session
from utils.logger import logger

MAX_PROMPT_CHARS = 8000
# A newer turn supersedes one still under way on the session. The Runtime keeps running a turn
# after its caller leaves (Stop in the web), so without this the shopper's next message would be
# refused as busy until the abandoned turn finished. The newer turn cancels the older and waits
# this long, at most, for it to stop.
SUPERSEDE_WAIT_SECONDS = 10.0
_SUPERSEDE_POLL_SECONDS = 0.1

_builder: Optional[SessionBuilder] = None
_session: Optional[Session] = None
# Builds run in worker threads, so a threading lock keeps them to one at a time.
_build_lock = threading.Lock()


class BuildWaitTimeout(Exception):
    """Another request's session build was still running when this request's deadline passed."""


def _extract_token(context: Any) -> str:
    """Read the Authorization header forwarded by the Runtime.

    Requires "Authorization" in the CfnRuntime requestHeaderAllowlist,
    otherwise the Runtime strips it before the container sees it.
    """
    headers = getattr(context, "request_headers", None)
    if headers:
        return headers.get("Authorization") or headers.get("authorization") or ""
    return ""


def _extract_forward_headers(context: Any, names: tuple[str, ...]) -> dict[str, str]:
    """Read allowlisted custom headers off the request (case-insensitive).

    Each name must also be in the Runtime requestHeaderAllowlist, otherwise the
    Runtime strips it before the container sees it. These are non-credential
    context headers (e.g. x-tenant-id, x-locale) propagated to tools.
    """
    if not names:
        return {}
    headers = getattr(context, "request_headers", None) or {}
    lowered = {k.lower(): v for k, v in headers.items()}
    return {name: lowered[name.lower()] for name in names if name.lower() in lowered}


@asynccontextmanager
async def lifespan(_app):
    global _builder
    _builder = SessionBuilder(AgentConfig.from_env())
    logger.info("container ready")
    yield
    # Never relied upon: containers are hard-killed without SIGTERM.


app = BedrockAgentCoreApp(lifespan=lifespan)


def _deadline_seconds(config: Any) -> int:
    """The invocation's deadline, session build included: 10 s inside the API's, at least 10 s.

    The API stops waiting after REQUEST_TIMEOUT_SECONDS. Stopping first lets the agent's own
    `deadline` error reach the shopper instead of the API's.
    """
    return max(10, int(config.request_timeout) - 10)


def _session_for(builder: SessionBuilder, token: str, session_id: str, forward_headers: dict[str, str],
                 workload_token: Optional[str], wait: float) -> Session:
    """The container's Session, built on first use. Runs in a worker thread.

    One build at a time: a request that arrives during a build waits for it. A failed build
    leaves no session behind, so the next request builds again. A build that outlives the
    request that started it still becomes the session for the next request.
    """
    global _session
    if not _build_lock.acquire(timeout=max(wait, 0.0)):
        raise BuildWaitTimeout()
    try:
        if _session is None:
            try:
                _session = builder.build(token, session_id, forward_headers=forward_headers, workload_token=workload_token)
            except Exception as exc:
                # The MCP transport connects eagerly, so a transient Gateway failure lands here.
                logger.error("session_build_failed", error_type=type(exc).__name__, exc_info=True)
                raise
        return _session
    finally:
        _build_lock.release()


@app.entrypoint
async def invoke(payload: dict, context: Any):
    session_id = getattr(context, "session_id", "") or ""
    structlog.contextvars.bind_contextvars(session_id=session_id)
    builder = _builder
    config = getattr(builder, "config", None)
    turn = _Turn(model_id=getattr(config, "model_id", "") or "")

    prompt = payload.get("prompt", "") if isinstance(payload, dict) else ""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        yield turn.failed("invalid_request", f"Prompt must contain 1-{MAX_PROMPT_CHARS} characters")
        return

    token = _extract_token(context)
    try:
        owner = resolve_actor_id(token)
        require_session_owner(session_id, owner)
        if _session is not None and _session.actor_id != owner:
            raise ValueError("Principal changed")
    except ValueError as exc:
        yield turn.failed("owner_mismatch", "Session does not belong to this caller", exc)
        return

    if builder is None:
        yield turn.failed("init_failed", "Agent is not initialized", RuntimeError("lifespan did not run"))
        return

    # Only after the caller owns the session — no need to read headers for a
    # rejected request.
    forward_headers = _extract_forward_headers(context, builder.config.forward_headers)
    workload_token = _workload_token()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _deadline_seconds(builder.config)

    scope = asyncio.timeout_at(deadline)
    try:
        async with scope:
            session = await asyncio.to_thread(
                _session_for, builder, token, session_id, forward_headers, workload_token, deadline - loop.time()
            )
    except Exception as exc:
        if scope.expired() or isinstance(exc, BuildWaitTimeout):
            yield turn.failed("deadline", "Request deadline exceeded", exc)
        else:
            yield turn.failed("init_failed", "Agent initialization failed, please retry", exc)
        return
    except BaseException:
        turn.cancelled()
        raise
    if session.actor_id != owner:
        yield turn.failed("owner_mismatch", "Session does not belong to this caller")
        return
    # Gateway calls carry a token minted from this request's workload access token; the
    # shopper's JWT, checked above, never reaches the Gateway.
    session.refresh_identity(workload_token)

    if session.turns >= builder.config.max_turns:
        yield turn.failed("turn_limit", "Session turn budget exhausted; start a new conversation")
        return
    session.turns += 1
    turn.attach(session)
    guardrail_message = getattr(builder.config, "guardrail_stream_processing", "sync") != "async"

    # forwarded_headers land in tool_context.invocation_state for local tools.
    cancel = threading.Event()
    session.in_flight.add(cancel)
    stream = _newest_turn(session, prompt, {"forwarded_headers": forward_headers}, cancel, deadline)
    expired = done = False
    try:
        while True:
            # The deadline bounds each wait for the agent, never a yield: a cancellation must not
            # land in the SDK's code while this generator is suspended.
            scope = asyncio.timeout_at(deadline)
            try:
                async with scope:
                    event = await anext(stream)
            except StopAsyncIteration:
                break
            except TimeoutError:
                expired = scope.expired()
                raise
            for outgoing in for_client(event, guardrail_message=guardrail_message):
                if outgoing["type"] == events.TOOL_USE:
                    turn.tool_calls += 1
                elif outgoing["type"] == events.DONE:
                    turn.finished(outgoing["data"]["stopReason"])
                    done = True
                yield outgoing
        if not done:
            yield turn.failed("internal", "Agent stream ended without a result")
    except ConcurrencyException:
        # A second request while a turn is in flight. Not a failure, and not a turn.
        session.turns -= 1
        turn.busy()
        yield events.error("busy", "Another turn is in progress in this session")
    except MaxTokensReachedException:
        # The partial answer has streamed and stays in the conversation; end the turn normally.
        turn.finished("max_tokens")
        yield events.done("max_tokens")
    except Exception as exc:
        if done:
            # The answer is complete and its `done` has gone out: report nothing more to the caller.
            logger.warning("agent_stream_failed_after_done", error_type=_error_type(exc), exc_info=exc)
        elif expired:
            yield turn.failed("deadline", "Request deadline exceeded", exc)
        elif any(isinstance(cause, ModelThrottledException) for cause in _causes(exc)):
            yield turn.failed("throttled", "The model is throttling requests", exc)
        else:
            yield turn.failed("internal", "Agent invocation failed", exc)
    except BaseException:
        # The caller went away: the SDK cancelled or closed this stream.
        turn.cancelled()
        raise
    finally:
        # Closing the stream releases the Strands agent for the next turn.
        await stream.aclose()
        session.in_flight.discard(cancel)


async def _newest_turn(session: Session, prompt: str, invocation_state: dict, cancel: threading.Event, deadline: float):
    """This turn's agent events. If another turn holds the agent, cancel that one and wait for it.

    Strands refuses a second invocation with ConcurrencyException before it yields anything,
    so retrying is safe. Every other turn under way on the session is cancelled once, at
    Strands' next safe point, and this one starts as soon as the agent is free. If the agent
    isn't free within SUPERSEDE_WAIT_SECONDS (or by the deadline), the ConcurrencyException
    propagates and the caller reports `busy`.
    """
    loop = asyncio.get_running_loop()
    # Give up a little before the deadline, so the caller hears `busy` rather than `deadline`.
    give_up = min(deadline - 1.0, loop.time() + SUPERSEDE_WAIT_SECONDS)
    superseded = False
    while True:
        stream = session.stream_async(prompt, invocation_state=invocation_state, cancel_signal=cancel)
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            return
        except ConcurrencyException:
            await stream.aclose()
            if not superseded:
                superseded = True
                for other in session.in_flight - {cancel}:
                    other.set()
                logger.info("agent_turn_superseded")
            if loop.time() >= give_up:
                raise
            await asyncio.sleep(_SUPERSEDE_POLL_SECONDS)
            continue
        break
    try:
        yield first
        async for event in stream:
            yield event
    finally:
        await stream.aclose()


def _workload_token() -> Optional[str]:
    """This request's workload access token, which the Runtime sends with every invocation.

    The SDK keeps it in a context variable, which the MCP client's own thread can't see, so
    it is read here and handed to the session.
    """
    from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

    return BedrockAgentCoreContext.get_workload_access_token()


def for_client(event: Any, *, guardrail_message: bool = True) -> list[dict]:
    """The client events (`aria_contracts.events`) for one Strands event; usually none.

    Only text, tool calls (ID and name), tool results (content only for display schemas) and
    the stop reason leave the container. Strands merges the invocation state, the Agent
    included, into every text-delta event, and the AgentCore SDK sends what it can't
    JSON-encode as `str(event)`, so a raw event would carry the system prompt, the transcript
    and the tool configuration out with every token. Each text chunk also arrives twice, as
    `{"data", "delta"}` and as the raw model event `{"event": {"contentBlockDelta": ...}}`;
    only the first is used. Model reasoning never leaves.

    `guardrail_message` puts the final text in `done` when the guardrail intervened. Only in
    sync mode is that text the guardrail's: in async mode it can be the unchecked reply the
    guardrail just stopped, so it is left out.
    """
    if not isinstance(event, dict):
        return []
    delta = event.get("delta")
    if isinstance(event.get("data"), str) and isinstance(delta, dict) and isinstance(delta.get("text"), str):
        return [events.text_delta(delta["text"])] if delta["text"] else []
    message = event.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        return [outgoing for block in message["content"] if (outgoing := _tool_event(block)) is not None]
    if "result" in event:
        result = event["result"]
        stop_reason = str(getattr(result, "stop_reason", None) or "end_turn")
        final = _text(getattr(result, "message", None))
        if final == BUDGET_TURN_END:
            # ToolSafety ended the turn after the tool budget. Strands stores this text without
            # streaming it, so send it, or the shopper would see the turn end with no word.
            return [events.text_delta(final), events.done(stop_reason)]
        text = final if stop_reason == events.GUARDRAIL_INTERVENED and guardrail_message else ""
        return [events.done(stop_reason, text or None)]
    return []


def _tool_event(block: Any) -> Optional[dict]:
    if not isinstance(block, dict):
        return None
    use = block.get("toolUse")
    if isinstance(use, dict) and isinstance(use.get("toolUseId"), str) and isinstance(use.get("name"), str):
        return events.tool_use(use["toolUseId"], use["name"])
    result = block.get("toolResult")
    if isinstance(result, dict) and isinstance(result.get("toolUseId"), str):
        return events.tool_result(result["toolUseId"], result.get("status"), result.get("content"))
    return None


def _text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return ""
    return "\n".join(block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)).strip()


def _causes(exc: Optional[BaseException]) -> Iterator[BaseException]:
    """The exception and what it wraps: Strands' EventLoopException, then `raise ... from`."""
    seen: set[int] = set()
    while isinstance(exc, BaseException) and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = getattr(exc, "original_exception", None) or exc.__cause__


def _error_type(exc: Optional[BaseException]) -> Optional[str]:
    """The failing exception's type, looking through Strands' EventLoopException wrapper."""
    while exc is not None and isinstance(getattr(exc, "original_exception", None), BaseException):
        exc = exc.original_exception
    return type(exc).__name__ if exc is not None else None


class _Turn:
    """One invocation's outcome, logged once, with its usage and stop reason.

    `agent_turn_finished`, `agent_turn_failed` and `agent_turn_cancelled` carry the turn's own
    token usage (the session's running total grows every turn), so summing them counts each
    token once. `agent_turn_busy` and `agent_turn_truncated` carry none.
    """

    def __init__(self, model_id: str = ""):
        self.started = time.monotonic()
        self.model_id = model_id
        self.session: Any = None
        self.tool_calls = 0
        self.logged = False
        self._invocations_before = 0

    def attach(self, session: Any) -> None:
        self.session = session
        self._invocations_before = len(_invocations(session))

    def finished(self, stop_reason: str) -> None:
        if self._log_once():
            logger.info("agent_turn_finished", **self._fields(stop_reason))
            if stop_reason == "max_tokens":
                logger.warning("agent_turn_truncated")

    def failed(self, code: str, message: str, exc: Optional[BaseException] = None) -> dict:
        event = events.error(code, message)
        if self._log_once():
            retryable = events.ERROR_CODES[code]
            # Retryable codes are the agent's or its dependencies' failures; the rest are the request's.
            log = logger.error if retryable else logger.warning
            details = {"exc_info": exc} if code == "internal" and exc is not None else {}
            log("agent_turn_failed", code=code, retryable=retryable, error_type=_error_type(exc), **self._fields(None), **details)
        return event

    def busy(self) -> None:
        if self._log_once():
            logger.info("agent_turn_busy")

    def cancelled(self) -> None:
        if self._log_once():
            logger.info("agent_turn_cancelled", **self._fields("cancelled"))

    def _log_once(self) -> bool:
        first, self.logged = not self.logged, True
        return first

    def _fields(self, stop_reason: Optional[str]) -> dict:
        usage = self._usage()
        prompt = getattr(getattr(self.session, "agent", None), "system_prompt", None)
        return {
            "duration_ms": round((time.monotonic() - self.started) * 1000),
            "input_tokens": usage["inputTokens"],
            "output_tokens": usage["outputTokens"],
            # Input the provider served from, or wrote to, its prompt cache: not in input_tokens.
            "cache_read_tokens": usage["cacheReadInputTokens"],
            "cache_write_tokens": usage["cacheWriteInputTokens"],
            "stop_reason": stop_reason,
            "tool_calls": self.tool_calls,
            "model_id": self.model_id,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest() if isinstance(prompt, str) else None,
        }

    def _usage(self) -> dict[str, int]:
        """This turn's tokens: Strands opens one AgentInvocation per stream_async call."""
        keys = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens")
        invocations = _invocations(self.session)
        usage = (getattr(invocations[-1], "usage", None) or {}) if len(invocations) > self._invocations_before else {}
        return {key: int(usage.get(key) or 0) for key in keys}


def _invocations(session: Any) -> list:
    metrics = getattr(getattr(session, "agent", None), "event_loop_metrics", None)
    invocations = getattr(metrics, "agent_invocations", None)
    return invocations if isinstance(invocations, list) else []


if __name__ == "__main__":
    app.run()
