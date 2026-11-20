"""What leaves the container: only aria_contracts events, never reasoning, tool input or invocation state."""

import json
from types import SimpleNamespace

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from app import for_client
from aria_contracts import events

TOOL_USE = {"toolUse": {"toolUseId": "t1", "name": "search_products", "input": {"season": "Winter"}}}
PRODUCTS = {"schemaVersion": "product.v1", "count": 1, "products": [{"id": "p-1", "productDisplayName": "겨울 자켓"}]}


class Unserializable:
    """Stands in for the Agent, spans and traces Strands merges into text-delta events."""

    def __repr__(self):
        return "<Agent system_prompt='You are Aria...' messages=[...]>"


def serialized(event):
    """The event as the AgentCore SDK writes it to the stream."""
    return json.loads(BedrockAgentCoreApp()._safe_serialize_to_json_string(event))


def assistant_message():
    return {
        "message": {
            "role": "assistant",
            "content": [
                {"reasoningContent": {"redactedContent": b"\x00opaque"}},
                {"text": "남성 겨울 자켓을 찾아볼게요."},
                TOOL_USE,
            ],
        }
    }


def test_a_text_delta_is_text_only():
    strands_event = {"data": "안녕", "delta": {"text": "안녕"}, "agent": Unserializable(), "forwarded_headers": {"x-tenant-id": "acme"}}
    assert for_client(strands_event) == [events.text_delta("안녕")]
    assert serialized(for_client(strands_event)[0]) == events.text_delta("안녕")


def test_without_mapping_the_sdk_sends_the_invocation_state_as_a_string():
    # Documents why for_client exists: without it, every token goes out as this string, Agent repr included.
    strands_event = {"data": "안녕", "delta": {"text": "안녕"}, "agent": Unserializable()}
    assert isinstance(serialized(strands_event), str) and "system_prompt" in serialized(strands_event)


def test_each_text_chunk_is_sent_once():
    raw_model_chunk = {"event": {"contentBlockDelta": {"delta": {"text": "안녕"}}}}
    assert for_client(raw_model_chunk) == []
    assert for_client({"data": "", "delta": {"text": ""}}) == []


def test_tool_calls_keep_id_and_name_only():
    outgoing = for_client(assistant_message())
    assert outgoing == [events.tool_use("t1", "search_products")]
    assert serialized(outgoing[0]) == events.tool_use("t1", "search_products")


def test_the_agents_own_message_keeps_its_reasoning():
    event = assistant_message()
    for_client(event)
    assert "reasoningContent" in event["message"]["content"][0]


def test_tool_results_show_only_display_schemas():
    message = {"message": {"role": "user", "content": [
        {"toolResult": {"toolUseId": "t1", "status": "success", "content": [{"text": json.dumps(PRODUCTS, ensure_ascii=False)}]}},
        {"toolResult": {"toolUseId": "t2", "status": "success", "content": [{"text": "internal order notes"}]}},
        {"toolResult": {"toolUseId": "t3", "status": "error", "content": [{"text": "Tool is not authorized"}]}},
    ]}}
    assert for_client(message) == [
        events.tool_result("t1", "success", [{"text": json.dumps(PRODUCTS)}]),
        {"version": 1, "type": "toolResult", "data": {"toolUseId": "t2", "status": "success"}},
        {"version": 1, "type": "toolResult", "data": {"toolUseId": "t3", "status": "error"}},
    ]
    assert json.loads(for_client(message)[0]["data"]["content"][0]["text"]) == PRODUCTS


def test_the_result_becomes_done_with_the_stop_reason():
    result = SimpleNamespace(stop_reason="end_turn", message={"role": "assistant", "content": [{"text": "full answer"}]}, metrics=Unserializable())
    assert for_client({"result": result}) == [events.done("end_turn")]


def test_a_guardrail_stop_carries_its_replacement_text_in_sync_mode_only():
    blocked = SimpleNamespace(stop_reason="guardrail_intervened", message={"role": "assistant", "content": [{"text": "답변할 수 없는 요청입니다."}]})
    assert for_client({"result": blocked}) == [events.done("guardrail_intervened", "답변할 수 없는 요청입니다.")]
    # In async mode the text can be the unchecked reply the guardrail cut off.
    assert for_client({"result": blocked}, guardrail_message=False) == [events.done("guardrail_intervened")]
    empty = SimpleNamespace(stop_reason="guardrail_intervened", message={"role": "assistant", "content": []})
    assert for_client({"result": empty}) == [events.done("guardrail_intervened")]


def test_everything_else_stays_in_the_container():
    state = {"agent": Unserializable(), "request_state": {}, "forwarded_headers": {"x-tenant-id": "acme"}}
    for strands_event in [
        "a string",
        None,
        {"init_event_loop": True, **state},
        {"start": True},
        {"start_event_loop": True},
        {"event": {"messageStart": {"role": "assistant"}}},
        {"event": {"metadata": {"usage": {"inputTokens": 1}}}},
        {"reasoningText": "thinking", "delta": {"reasoningContent": {"text": "thinking"}}, "reasoning": True, **state},
        {"reasoningRedactedContent": b"\x00", "delta": {"reasoningContent": {"redactedContent": b"\x00"}}, "reasoning": True},
        {"type": "tool_use_stream", "delta": {"toolUse": {"input": '{"season": "Win'}}, "current_tool_use": TOOL_USE["toolUse"], **state},
        {"type": "tool_stream", "tool_stream_event": {"tool_use": TOOL_USE["toolUse"], "data": "partial"}},
        {"tool_cancel_event": {"tool_use": TOOL_USE["toolUse"], "message": "Tool budget exhausted"}},
        {"event_loop_throttled_delay": 4, **state},
        {"force_stop": True, "force_stop_reason": "boom"},
        {"citation": {"title": "x"}, "delta": {"citation": {"title": "x"}}},
        {"message": {"role": "assistant", "content": [{"text": "plain text arrives as deltas"}]}},
        {"error": "x"},
    ]:
        assert for_client(strands_event) == [], strands_event


def test_the_tool_budget_notice_is_streamed_before_the_turn_ends():
    # ToolSafety ends a runaway turn with this text; Strands stores it but never streams it.
    from core.safety import BUDGET_TURN_END

    result = SimpleNamespace(stop_reason="end_turn", message={"role": "assistant", "content": [{"text": BUDGET_TURN_END}]})
    assert for_client({"result": result}) == [events.text_delta(BUDGET_TURN_END), events.done("end_turn")]


def test_an_ordinary_answer_is_not_repeated_at_the_end():
    result = SimpleNamespace(stop_reason="end_turn", message={"role": "assistant", "content": [{"text": "추천드립니다."}]})
    assert for_client({"result": result}) == [events.done("end_turn")]
