"""The entrypoint's turn contract: coded errors, one terminal event, and one outcome log per invocation.

Driven locally with a fake builder and session (no AWS). The logger is replaced by a recorder that
also captures the structlog context variables, which is where `session_id` comes from.
"""

import asyncio
import base64
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest
import structlog
from strands.types.exceptions import ConcurrencyException, EventLoopException, MaxTokensReachedException, ModelThrottledException

import app
from aria_contracts import events
from aria_contracts.identity import actor_id, new_session_id

ISSUER = "https://issuer.example"
ALICE = actor_id({"iss": ISSUER, "sub": "alice"})
SESSION = new_session_id(ALICE)
SYSTEM_PROMPT = "You are Aria, the shopping assistant for NORDLY."
PRODUCTS = json.dumps({"schemaVersion": "product.v1", "count": 0, "products": []})


def bearer(sub: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"iss": ISSUER, "sub": sub}).encode()).decode().rstrip("=")
    return f"Bearer h.{payload}.s"


CONTEXT = SimpleNamespace(session_id=SESSION, request_headers={"Authorization": bearer("alice")})


class RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, level, event, **fields):
        self.records.append({"level": level, "event": event, **structlog.contextvars.get_contextvars(), **fields})

    def debug(self, event, **fields):
        self._record("debug", event, **fields)

    def info(self, event, **fields):
        self._record("info", event, **fields)

    def warning(self, event, **fields):
        self._record("warning", event, **fields)

    def error(self, event, **fields):
        self._record("error", event, **fields)

    def named(self, *names):
        return [record for record in self.records if record["event"] in names]


class FakeSession:
    """A Session whose turn is a script: an async generator function of the session."""

    def __init__(self, script):
        self.actor_id = ALICE
        self.turns = 0
        self.script = script
        self.agent = SimpleNamespace(system_prompt=SYSTEM_PROMPT, event_loop_metrics=SimpleNamespace(agent_invocations=[]))
        self.workload_tokens = []
        self.closed = 0
        self.prompts = []
        self.in_flight = set()

    def refresh_identity(self, workload_token):
        self.workload_tokens.append(workload_token)

    def use(self, input_tokens, output_tokens):
        self.agent.event_loop_metrics.agent_invocations[-1].usage.update(inputTokens=input_tokens, outputTokens=output_tokens)

    async def stream_async(self, prompt, **kwargs):
        self.prompts.append(prompt)
        # Strands opens one invocation per call; its usage grows with each model call.
        self.agent.event_loop_metrics.agent_invocations.append(SimpleNamespace(usage={"inputTokens": 0, "outputTokens": 0}))
        try:
            async for event in self.script(self):
                yield event
        finally:
            self.closed += 1


def result(stop_reason="end_turn", text="추천드립니다."):
    return {"result": SimpleNamespace(stop_reason=stop_reason, message={"role": "assistant", "content": [{"text": text}]})}


async def answer(session):
    yield {"init_event_loop": True, "agent": object()}
    yield {"data": "찾아볼게요.", "delta": {"text": "찾아볼게요."}, "agent": object()}
    yield {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "search_products", "input": {"query": "jacket"}}}]}}
    yield {"message": {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "status": "success", "content": [{"text": PRODUCTS}]}}]}}
    yield {"data": "추천드립니다.", "delta": {"text": "추천드립니다."}}
    session.use(120, 30)
    yield result()


EXPECTED_ANSWER = [
    events.text_delta("찾아볼게요."),
    events.tool_use("t1", "search_products"),
    events.tool_result("t1", "success", [{"text": PRODUCTS}]),
    events.text_delta("추천드립니다."),
    events.done("end_turn"),
]


class FakeBuilder:
    def __init__(self, script=answer, build=None, **config):
        defaults = dict(forward_headers=(), max_turns=100, request_timeout=120, model_id="test-model", guardrail_stream_processing="sync")
        self.config = SimpleNamespace(**{**defaults, **config})
        self.script = script
        self._build = build
        self.builds = []

    def build(self, token, session_id, forward_headers=None, workload_token=None):
        self.builds.append(threading.current_thread())
        if self._build is not None:
            self._build()
        return FakeSession(self.script)


@pytest.fixture
def log(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    recorder = RecordingLogger()
    monkeypatch.setattr(app, "logger", recorder)
    monkeypatch.setattr(app, "_session", None)
    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 5)
    return recorder


def use(monkeypatch, builder):
    monkeypatch.setattr(app, "_builder", builder)
    return builder


async def run(payload=None, context=CONTEXT):
    return [event async for event in app.invoke({"prompt": "겨울 자켓 추천해 줘"} if payload is None else payload, context)]


def codes(outgoing):
    return [(event["type"], event["data"].get("code")) for event in outgoing]


def outcome(log):
    records = log.named("agent_turn_finished", "agent_turn_failed", "agent_turn_cancelled", "agent_turn_busy")
    assert len(records) == 1, records
    return records[0]


USAGE_FIELDS = {"session_id", "duration_ms", "input_tokens", "output_tokens", "stop_reason", "tool_calls", "model_id", "prompt_sha256"}


async def test_a_turn_streams_events_and_logs_its_usage(monkeypatch, log):
    use(monkeypatch, FakeBuilder())
    assert await run() == EXPECTED_ANSWER
    finished = outcome(log)
    assert finished["event"] == "agent_turn_finished" and finished["level"] == "info"
    assert USAGE_FIELDS <= set(finished)
    assert finished["session_id"] == SESSION
    assert (finished["input_tokens"], finished["output_tokens"], finished["tool_calls"]) == (120, 30, 1)
    assert finished["stop_reason"] == "end_turn" and finished["model_id"] == "test-model"
    assert finished["prompt_sha256"] == hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
    assert isinstance(finished["duration_ms"], int)


async def test_usage_is_the_turns_own_not_the_sessions_total(monkeypatch, log):
    use(monkeypatch, FakeBuilder())
    await run()
    await run()
    assert [r["input_tokens"] for r in log.named("agent_turn_finished")] == [120, 120]


async def test_logs_never_carry_message_content_or_tokens(monkeypatch, log):
    use(monkeypatch, FakeBuilder())
    await run()
    logged = json.dumps(log.records, default=str, ensure_ascii=False)
    for secret in ("겨울 자켓 추천해 줘", "추천드립니다", "Bearer", SYSTEM_PROMPT):
        assert secret not in logged


@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": "x" * 8001}, {"prompt": 42}, None, "hi"])
async def test_an_invalid_prompt_is_invalid_request(monkeypatch, log, payload):
    builder = use(monkeypatch, FakeBuilder())
    outgoing = [event async for event in app.invoke(payload, CONTEXT)]
    assert codes(outgoing) == [("error", "invalid_request")]
    assert outgoing[0]["data"]["retryable"] is False
    failed = outcome(log)
    assert (failed["event"], failed["code"], failed["level"]) == ("agent_turn_failed", "invalid_request", "warning")
    assert builder.builds == []


async def test_the_session_is_built_in_a_worker_thread(monkeypatch, log):
    builder = use(monkeypatch, FakeBuilder())
    await run()
    await run()
    assert len(builder.builds) == 1
    assert builder.builds[0] is not threading.main_thread()


async def test_a_failed_build_is_init_failed_and_does_not_poison_the_container(monkeypatch, log):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("Gateway unreachable")

    use(monkeypatch, FakeBuilder(build=flaky))
    outgoing = await run()
    assert codes(outgoing) == [("error", "init_failed")]
    assert outgoing[0]["data"]["retryable"] is True and "Gateway" not in outgoing[0]["data"]["message"]
    assert app._session is None
    [build_failed] = log.named("session_build_failed")
    # Logged from the build's worker thread, which inherits the invocation's context.
    assert (build_failed["error_type"], build_failed["session_id"], build_failed["level"]) == ("ConnectionError", SESSION, "error")
    assert outcome(log)["code"] == "init_failed"
    # The next request builds again and succeeds.
    assert await run() == EXPECTED_ANSWER
    assert len(attempts) == 2


async def test_the_build_counts_against_the_deadline_and_a_late_build_is_kept(monkeypatch, log):
    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 0.05)
    builder = use(monkeypatch, FakeBuilder(build=lambda: time.sleep(0.3)))
    outgoing = await run()
    assert codes(outgoing) == [("error", "deadline")]
    assert outgoing[0]["data"]["retryable"] is True
    assert (outcome(log)["code"], outcome(log)["error_type"]) == ("deadline", "TimeoutError")
    # The build finishes in its thread and becomes the session: the next request doesn't build again.
    for _ in range(100):
        if app._session is not None:
            break
        await asyncio.sleep(0.01)
    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 5)
    assert await run() == EXPECTED_ANSWER
    assert len(builder.builds) == 1


async def test_a_request_during_a_build_waits_for_it(monkeypatch, log):
    builder = use(monkeypatch, FakeBuilder(build=lambda: time.sleep(0.1)))
    first, second = await asyncio.gather(run(), run())
    assert first == second == EXPECTED_ANSWER
    assert len(builder.builds) == 1


async def test_a_turn_past_the_deadline_is_deadline_and_releases_the_agent(monkeypatch, log):
    async def hangs(session):
        yield {"data": "찾아", "delta": {"text": "찾아"}}
        session.use(50, 5)
        await asyncio.sleep(10)
        yield result()  # pragma: no cover

    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 0.1)
    use(monkeypatch, FakeBuilder(script=hangs))
    outgoing = await run()
    assert codes(outgoing) == [("textDelta", None), ("error", "deadline")]
    failed = outcome(log)
    assert (failed["event"], failed["code"], failed["error_type"], failed["level"]) == ("agent_turn_failed", "deadline", "TimeoutError", "error")
    assert USAGE_FIELDS <= set(failed) and failed["input_tokens"] == 50 and failed["stop_reason"] is None
    assert app._session.closed == 1


async def test_a_timeout_inside_the_agent_is_not_the_deadline(monkeypatch, log):
    async def times_out(session):
        raise TimeoutError("socket read timed out")
        yield  # pragma: no cover

    use(monkeypatch, FakeBuilder(script=times_out))
    assert codes(await run()) == [("error", "internal")]


def test_the_deadline_leaves_the_api_ten_seconds():
    deadline = app._deadline_seconds
    assert [deadline(SimpleNamespace(request_timeout=t)) for t in (120, 300, 20, 15, 10)] == [110, 290, 10, 10, 10]


async def test_the_turn_budget_is_turn_limit(monkeypatch, log):
    use(monkeypatch, FakeBuilder(max_turns=1))
    assert await run() == EXPECTED_ANSWER
    outgoing = await run()
    assert codes(outgoing) == [("error", "turn_limit")] and outgoing[0]["data"]["retryable"] is False
    assert app._session.turns == 1
    assert log.named("agent_turn_failed")[0]["code"] == "turn_limit"


@pytest.mark.parametrize(
    "raised",
    [ModelThrottledException("Too many requests"), EventLoopException(ModelThrottledException("Too many requests"))],
)
async def test_model_throttling_is_throttled(monkeypatch, log, raised):
    async def throttled(session):
        yield {"data": "찾아", "delta": {"text": "찾아"}}
        raise raised

    use(monkeypatch, FakeBuilder(script=throttled))
    outgoing = await run()
    assert codes(outgoing) == [("textDelta", None), ("error", "throttled")]
    assert outgoing[-1]["data"]["retryable"] is True
    failed = outcome(log)
    assert (failed["code"], failed["error_type"]) == ("throttled", "ModelThrottledException")


async def test_any_other_failure_is_internal_with_its_traceback_in_the_log(monkeypatch, log):
    async def fails(session):
        raise RuntimeError("secret internal detail")
        yield  # pragma: no cover

    use(monkeypatch, FakeBuilder(script=fails))
    outgoing = await run()
    assert codes(outgoing) == [("error", "internal")]
    assert "secret" not in json.dumps(outgoing)
    failed = outcome(log)
    assert (failed["code"], failed["error_type"]) == ("internal", "RuntimeError")
    assert isinstance(failed["exc_info"], RuntimeError)


async def test_a_second_request_during_a_turn_is_busy_and_not_a_turn(monkeypatch, log):
    async def busy(session):
        raise ConcurrencyException("Agent is already processing a request.")
        yield  # pragma: no cover

    # The turn holding the agent never stops, so the newer one gives up waiting for it.
    monkeypatch.setattr(app, "SUPERSEDE_WAIT_SECONDS", 0.2)
    use(monkeypatch, FakeBuilder(script=busy))
    outgoing = await run()
    assert codes(outgoing) == [("error", "busy")] and outgoing[0]["data"]["retryable"] is True
    assert app._session.turns == 0
    assert log.named("agent_turn_failed") == []
    busy_log = outcome(log)
    assert (busy_log["event"], busy_log["level"], busy_log["session_id"]) == ("agent_turn_busy", "info", SESSION)


async def test_the_max_tokens_cap_ends_the_turn_with_its_partial_answer(monkeypatch, log):
    async def truncated(session):
        yield {"data": "첫 번째", "delta": {"text": "첫 번째"}}
        session.use(100, 2048)
        raise MaxTokensReachedException("Model stopped generating due to maximum token limit.")

    use(monkeypatch, FakeBuilder(script=truncated))
    assert await run() == [events.text_delta("첫 번째"), events.done("max_tokens")]
    finished = outcome(log)
    assert (finished["event"], finished["stop_reason"], finished["output_tokens"]) == ("agent_turn_finished", "max_tokens", 2048)
    [truncation] = log.named("agent_turn_truncated")
    assert truncation["session_id"] == SESSION and "output_tokens" not in truncation


async def test_a_failure_after_done_sends_no_second_terminal_event(monkeypatch, log):
    async def fails_late(session):
        yield result()
        raise RuntimeError("trace export failed")

    use(monkeypatch, FakeBuilder(script=fails_late))
    assert await run() == [events.done("end_turn")]
    assert outcome(log)["event"] == "agent_turn_finished"
    [late] = log.named("agent_stream_failed_after_done")
    assert late["error_type"] == "RuntimeError"


async def test_a_stream_without_a_result_still_ends_with_an_error(monkeypatch, log):
    async def stops(session):
        yield {"data": "찾아", "delta": {"text": "찾아"}}

    use(monkeypatch, FakeBuilder(script=stops))
    assert codes(await run()) == [("textDelta", None), ("error", "internal")]


async def test_a_guardrail_in_async_mode_sends_no_replacement_text(monkeypatch, log):
    async def blocked(session):
        yield result("guardrail_intervened", "unchecked reply")

    use(monkeypatch, FakeBuilder(script=blocked, guardrail_stream_processing="async"))
    assert await run() == [events.done("guardrail_intervened")]
    use(monkeypatch, FakeBuilder(script=blocked, guardrail_stream_processing="sync"))
    monkeypatch.setattr(app, "_session", None)
    assert await run() == [events.done("guardrail_intervened", "unchecked reply")]


async def test_a_caller_that_goes_away_closes_the_agent_stream(monkeypatch, log):
    use(monkeypatch, FakeBuilder())
    stream = app.invoke({"prompt": "hi"}, CONTEXT)
    assert await anext(stream) == EXPECTED_ANSWER[0]
    await stream.aclose()
    assert app._session.closed == 1
    cancelled = outcome(log)
    assert (cancelled["event"], cancelled["stop_reason"]) == ("agent_turn_cancelled", "cancelled")


async def test_every_log_line_of_an_invocation_carries_its_session_id(monkeypatch, log):
    use(monkeypatch, FakeBuilder(build=lambda: (_ for _ in ()).throw(OSError("down"))))
    await run()
    use(monkeypatch, FakeBuilder())
    await run()
    assert log.records and all(record["session_id"] == SESSION for record in log.records)
