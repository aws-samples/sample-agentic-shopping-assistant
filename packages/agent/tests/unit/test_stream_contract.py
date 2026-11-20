"""End to end: one real Strands turn, as it leaves the container and as the browser receives it.

A real Agent, assembled by a SessionBuilder with a scripted model, answers, calls a product tool and
answers again. Its events go through `for_client`, the entrypoint and the AgentCore SDK's own SSE
serialization, then through the API's decoder and `Relay`. The API's events.py is loaded by
path because the agent and the API share no imports, only aria_contracts.
"""

import asyncio
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import tool
from strands.models import Model

import app
from aria_contracts import events
from aria_contracts.identity import actor_id, new_session_id
from core.builder import SessionBuilder
from core.config import AgentConfig
from prompts.system import build_system_prompt

ISSUER = "https://issuer.example"
OWNER = actor_id({"iss": ISSUER, "sub": "alice"})
PROMPT_MARKER = "prompt-marker-5e1f"
TOOL_INPUT_MARKER = "tool-input-marker-9c2a"
HEADER_MARKER = "header-marker-77d0"
# A real fixture row, so the frames are what the web renders as a card.
PRODUCT = {"id": "sample-1", "productDisplayName": "겨울 자켓 Winter Jacket", "gender": "Men", "masterCategory": "Apparel", "subCategory": "Topwear", "articleType": "Topwear", "baseColour": "Blue", "season": "Winter", "year": 2026, "usage": "Casual"}
RESULT = {"schemaVersion": "product.v1", "count": 1, "products": [PRODUCT]}

API_EVENTS = Path(__file__).resolve().parents[3] / "api" / "src" / "events.py"


def load_api_events():
    spec = importlib.util.spec_from_file_location("aria_api_events", API_EVENTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tool(name="search_products", description="Search the catalog.", inputSchema={"json": {"type": "object", "properties": {"query": {"type": "string"}}}})
def fake_search(query: str = "") -> dict:
    return RESULT


def text(*parts: str) -> list[dict]:
    return [{"contentBlockDelta": {"delta": {"text": part}}} for part in parts] + [{"contentBlockStop": {}}]


def usage(input_tokens: int, output_tokens: int) -> dict:
    return {"metadata": {"usage": {"inputTokens": input_tokens, "outputTokens": output_tokens, "totalTokens": input_tokens + output_tokens}, "metrics": {"latencyMs": 5}}}


SEARCH_TURN = [
    {"messageStart": {"role": "assistant"}},
    *text("겨울 자켓을 ", "찾아볼게요."),
    {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "tooluse-1", "name": "search_products"}}}},
    {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"query": TOOL_INPUT_MARKER})}}}},
    {"contentBlockStop": {}},
    {"messageStop": {"stopReason": "tool_use"}},
    usage(1200, 40),
]
ANSWER_TURN = [
    {"messageStart": {"role": "assistant"}},
    *text("추천 상품입니다.\n\n", "[[Product:sample-1]]"),
    {"messageStop": {"stopReason": "end_turn"}},
    usage(1400, 25),
]


class ScriptedModel(Model):
    """Replays one scripted response per model call; `gate`, when set, holds a call mid-stream."""

    def __init__(self, *responses, gate=None):
        self.responses = list(responses)
        self.gate = gate
        self.system_prompts = []

    def update_config(self, **model_config):
        pass

    def get_config(self):
        return {"model_id": "test.scripted-model"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.system_prompts.append(system_prompt)
        response = self.responses.pop(0)
        for index, chunk in enumerate(response):
            if self.gate is not None and index == 2:
                await self.gate.wait()
            yield chunk


class ScriptedBuilder(SessionBuilder):
    def __init__(self, config, model):
        super().__init__(config)
        self.model = model

    def _build_model(self):
        return self.model

    def _build_tools(self):
        return [fake_search]


def config(**overrides) -> AgentConfig:
    values = dict(
        gateway_url="", model_id="test.scripted-model", region="us-west-2", max_tokens=2048, temperature=0.3,
        memory_id="", memory_strategies=(), memory_top_k=5,
        memory_relevance_score=0.3, guardrail_id=None, guardrail_version=None,
        allowed_tools=("search_products",), forward_headers=("x-tenant-id",),
    )
    return AgentConfig(**{**values, **overrides})


def context():
    claims = base64.urlsafe_b64encode(json.dumps({"iss": ISSUER, "sub": "alice"}).encode()).decode().rstrip("=")
    return SimpleNamespace(
        session_id=new_session_id(OWNER),
        request_headers={"Authorization": f"Bearer h.{claims}.s", "x-tenant-id": HEADER_MARKER},
    )


class RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, level, event, **fields):
        self.records.append({"level": level, "event": event, **structlog.contextvars.get_contextvars(), **fields})

    def info(self, event, **fields):
        self._record("info", event, **fields)

    def warning(self, event, **fields):
        self._record("warning", event, **fields)

    def error(self, event, **fields):
        self._record("error", event, **fields)

    def named(self, name):
        return [record for record in self.records if record["event"] == name]


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    log = RecordingLogger()
    monkeypatch.setattr(app, "logger", log)
    monkeypatch.setattr(app, "_session", None)

    def use(model, **overrides):
        monkeypatch.setattr(app, "_builder", ScriptedBuilder(config(**overrides), model))
        return SimpleNamespace(model=model, log=log)

    return use


async def wire_bytes(prompt: str, ctx) -> bytes:
    """The bytes the AgentCore SDK writes for one invocation of the entrypoint."""
    sdk = BedrockAgentCoreApp()
    return b"".join([chunk async for chunk in sdk._stream_with_error_handling(app.invoke({"prompt": prompt}, ctx))])


def browser_frames(wire: bytes) -> tuple[list, list[dict]]:
    """The agent's payloads as the API decodes them, and the SSE frames it sends the browser."""
    api = load_api_events()
    payloads = api.SSEDecoder().feed(wire, final=True)
    relay = api.Relay()
    frames = [frame for payload in payloads for event in relay.feed(payload) for frame in [api.sse(event)]]
    frames += [api.sse(event) for event in relay.finish()]
    return payloads, [json.loads(frame.removeprefix("data: ")) for frame in frames]


EXPECTED = [
    events.text_delta("겨울 자켓을 "),
    events.text_delta("찾아볼게요."),
    events.tool_use("tooluse-1", "search_products"),
    events.tool_result("tooluse-1", "success", [{"text": json.dumps(RESULT)}]),
    events.text_delta("추천 상품입니다.\n\n"),
    events.text_delta("[[Product:sample-1]]"),
    events.done("end_turn"),
]


async def test_a_real_turn_reaches_the_browser_as_exactly_these_frames(agent):
    turn = agent(ScriptedModel(SEARCH_TURN, ANSWER_TURN))
    wire = await wire_bytes(f"남성 겨울 자켓 추천해 줘 {PROMPT_MARKER}", context())
    payloads, frames = browser_frames(wire)

    # The agent sends the contract and nothing else, each event serialized as a JSON object.
    assert payloads == EXPECTED
    assert frames == EXPECTED
    # Each text token once: the raw model chunks are not relayed a second time.
    assert "".join(f["data"]["text"] for f in frames if f["type"] == "textDelta") == "겨울 자켓을 찾아볼게요.추천 상품입니다.\n\n[[Product:sample-1]]"
    assert json.loads(frames[3]["data"]["content"][0]["text"]) == RESULT

    # The model saw the system prompt; it never left the container, nor did anything else private.
    system_prompt = build_system_prompt()
    assert turn.model.system_prompts[0] == system_prompt
    decoded = wire.decode()
    for private in (system_prompt[:40], PROMPT_MARKER, TOOL_INPUT_MARKER, HEADER_MARKER, "forwarded_headers",
                    "event_loop", "inputSchema", "toolUse\": {\"input", "Agent object", "reasoning"):
        assert private not in decoded, private
    # A few hundred bytes per frame at most: the invocation state Strands merges into each text delta stays behind.
    assert len(wire) < 2048

    [finished] = turn.log.named("agent_turn_finished")
    assert (finished["input_tokens"], finished["output_tokens"], finished["tool_calls"], finished["stop_reason"]) == (2600, 65, 1, "end_turn")
    assert finished["model_id"] == "test.scripted-model"
    assert finished["prompt_sha256"] == hashlib.sha256(system_prompt.encode()).hexdigest()


async def test_a_second_turn_logs_only_its_own_usage(agent):
    turn = agent(ScriptedModel(ANSWER_TURN, ANSWER_TURN))
    ctx = context()
    await wire_bytes("첫 질문", ctx)
    await wire_bytes("두 번째 질문", ctx)
    assert [r["input_tokens"] for r in turn.log.named("agent_turn_finished")] == [1400, 1400]


async def test_a_turn_that_does_not_stop_in_time_leaves_the_newer_one_busy(agent, monkeypatch):
    # A model call stuck with nothing to stream has no point where Strands can cancel it.
    monkeypatch.setattr(app, "SUPERSEDE_WAIT_SECONDS", 0.3)
    gate = asyncio.Event()
    agent(ScriptedModel(ANSWER_TURN, gate=gate))
    ctx = context()
    first = app.invoke({"prompt": "첫 질문"}, ctx)
    assert await anext(first) == events.text_delta("추천 상품입니다.\n\n")

    second = [event async for event in app.invoke({"prompt": "두 번째 질문"}, ctx)]
    assert [(e["type"], e["data"].get("code")) for e in second] == [("error", "busy")]

    # The older turn was asked to stop, so it ends as cancelled once it can.
    gate.set()
    rest = [event async for event in first]
    assert rest[-1] == events.done("cancelled")
    assert app._session.turns == 1


class StreamingModel(ScriptedModel):
    """Streams each chunk after a short pause, as a model mid-answer does."""

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
            await asyncio.sleep(0.02)
            yield chunk


LONG_TURN = [
    {"messageStart": {"role": "assistant"}},
    *text(*[f"{n}. 겨울 코디 " for n in range(60)]),
    {"messageStop": {"stopReason": "end_turn"}},
    usage(1500, 600),
]


async def test_a_new_request_supersedes_the_turn_still_running(agent):
    # The web's Stop leaves the turn running in the Runtime; the shopper's next message must not wait for it.
    turn = agent(StreamingModel(LONG_TURN, ANSWER_TURN))
    ctx = context()
    first = app.invoke({"prompt": "겨울 코디 자세히"}, ctx)
    assert (await anext(first))["type"] == events.TEXT_DELTA

    async def drain():
        return [event async for event in first]

    older = asyncio.create_task(drain())
    second = [event async for event in app.invoke({"prompt": "남성용 겨울 자켓 추천해 주세요."}, ctx)]
    assert second[-1] == events.done("end_turn")
    assert (await older)[-1] == events.done("cancelled")
    assert turn.log.named("agent_turn_superseded")
    assert app._session.turns == 2
    assert app._session.in_flight == set()


async def test_a_turn_cut_off_by_the_deadline_frees_the_agent_for_the_next(agent, monkeypatch):
    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 0.2)
    never = asyncio.Event()
    turn = agent(ScriptedModel(ANSWER_TURN, gate=never))
    ctx = context()
    _, frames = browser_frames(await wire_bytes("첫 질문", ctx))
    assert frames[-1]["data"]["code"] == "deadline" and frames[-1]["data"]["retryable"] is True

    turn.model.gate = None
    turn.model.responses.append(ANSWER_TURN)
    _, frames = browser_frames(await wire_bytes("다시 질문", ctx))
    assert frames[-1] == events.done("end_turn")
