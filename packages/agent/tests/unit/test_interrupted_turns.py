"""A turn cut off by the deadline must not leave the next turn with two user messages in a row.

Runs the real entrypoint and a real Strands Agent with the scripted model from the stream
contract tests, and checks the roles of the messages the model receives on the next turn.
"""

import asyncio

import pytest

import app
from core.history import INTERRUPTED_REPLY, InterruptedTurns
from test_stream_contract import (  # noqa: F401  (agent is a fixture)
    ANSWER_TURN,
    SEARCH_TURN,
    ScriptedModel,
    agent,
    browser_frames,
    context,
    wire_bytes,
)
from aria_contracts import events


class RecordingModel(ScriptedModel):
    """Records the roles of the messages each model call receives."""

    def __init__(self, *responses, gate=None, hold_call=None):
        super().__init__(*responses, gate=gate)
        self.calls = []
        self.hold_call = hold_call  # (call number, event): hold that call until the event is set

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls.append([message["role"] for message in messages])
        if self.hold_call and len(self.calls) == self.hold_call[0]:
            await self.hold_call[1].wait()
        async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield chunk


def alternates(roles: list[str]) -> bool:
    return all(a != b for a, b in zip(roles, roles[1:]))


@pytest.mark.asyncio
async def test_a_turn_cut_off_mid_answer_is_closed_before_the_next(agent, monkeypatch):
    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 0.2)
    turn = agent(RecordingModel(ANSWER_TURN, gate=asyncio.Event()))
    monkeypatch.setattr("core.history.logger", turn.log)
    ctx = context()
    _, frames = browser_frames(await wire_bytes("첫 질문", ctx))
    assert frames[-1]["data"]["code"] == "deadline"

    turn.model.gate = None
    turn.model.responses.append(ANSWER_TURN)
    _, frames = browser_frames(await wire_bytes("다시 질문", ctx))
    assert frames[-1] == events.done("end_turn")
    assert turn.model.calls[-1] == ["user", "assistant", "user"]
    closing = app._session.agent.messages[1]
    assert closing["role"] == "assistant" and closing["content"] == [{"text": INTERRUPTED_REPLY}]
    assert len(turn.log.named("interrupted_turn_closed")) == 1


@pytest.mark.asyncio
async def test_a_turn_cut_off_after_a_tool_result_is_closed_before_the_next(agent, monkeypatch):
    monkeypatch.setattr(app, "_deadline_seconds", lambda config: 0.3)
    held = asyncio.Event()
    # Call 1 asks for search; call 2 (the answer after the tool result) never returns in time.
    turn = agent(RecordingModel(SEARCH_TURN, ANSWER_TURN, hold_call=(2, held)))
    ctx = context()
    _, frames = browser_frames(await wire_bytes("자켓 찾아줘", ctx))
    assert frames[-1]["data"]["code"] == "deadline"
    assert [m["role"] for m in app._session.agent.messages] == ["user", "assistant", "user"]

    turn.model.hold_call = None
    turn.model.responses = [ANSWER_TURN]
    _, frames = browser_frames(await wire_bytes("다시 질문", ctx))
    assert frames[-1] == events.done("end_turn")
    assert alternates(turn.model.calls[-1]), turn.model.calls[-1]


@pytest.mark.asyncio
async def test_a_completed_turn_is_left_alone(agent, monkeypatch):
    turn = agent(RecordingModel(ANSWER_TURN, ANSWER_TURN))
    monkeypatch.setattr("core.history.logger", turn.log)
    ctx = context()
    await wire_bytes("첫 질문", ctx)
    await wire_bytes("다음 질문", ctx)
    assert turn.model.calls[-1] == ["user", "assistant", "user"]
    assert not turn.log.named("interrupted_turn_closed")
    assert all(
        block.get("text") != INTERRUPTED_REPLY for message in app._session.agent.messages for block in message["content"]
    )


def test_nothing_is_added_without_a_new_user_message():
    class Event:
        def __init__(self, history, messages):
            self.agent = type("A", (), {"messages": history})()
            self.messages = messages

    hook = InterruptedTurns()
    for event in (Event([{"role": "user", "content": [{"text": "q"}]}], []),
                  Event([{"role": "user", "content": [{"text": "q"}]}], None),
                  Event([], [{"role": "user", "content": [{"text": "q"}]}])):
        before = event.messages
        hook.before_invocation(event)
        assert event.messages is before
