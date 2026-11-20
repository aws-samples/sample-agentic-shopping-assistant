import json

import pytest
from aria_contracts import events

from events import Relay, SSEDecoder, parse_sse_line, translate

PRODUCTS = json.dumps({"schemaVersion": "product.v1", "count": 0, "products": []})


def frames(chunk) -> list[dict]:
    return [parse_sse_line(f) for f in translate(chunk)]


class TestParseSseLine:
    def test_parses_data_line(self):
        assert parse_sse_line('data: {"a": 1}') == {"a": 1}

    def test_tolerates_no_space_after_colon(self):
        assert parse_sse_line('data:{"a": 1}') == {"a": 1}

    def test_ignores_non_data_lines(self):
        assert parse_sse_line("event: message") is None
        assert parse_sse_line("") is None
        assert parse_sse_line(": keep-alive") is None

    def test_ignores_done_sentinel(self):
        assert parse_sse_line("data: [DONE]") is None

    def test_ignores_malformed_json(self):
        assert parse_sse_line("data: {not json") is None


class TestTurnEvents:
    @pytest.mark.parametrize(
        "event",
        [
            events.text_delta("Hello"),
            events.tool_use("t1", "search_products"),
            events.tool_result("t1", "success", [{"text": PRODUCTS}]),
            events.tool_result("t1", "error"),
            events.error("busy", "Another turn is in progress in this session"),
            events.done("end_turn"),
            events.done("guardrail_intervened", "답변할 수 없는 요청입니다."),
        ],
    )
    def test_the_agents_events_reach_the_browser_unchanged(self, event):
        assert frames(event) == [event]

    def test_tool_frames_never_carry_input_or_undisplayable_output(self):
        use = {"version": 1, "type": "toolUse", "data": {"toolUseId": "t1", "name": "search_products", "input": {"name": "tee"}}}
        assert frames(use) == [{"version": 1, "type": "toolUse", "data": {"toolUseId": "t1", "name": "search_products"}}]
        result = {"version": 1, "type": "toolResult", "data": {"toolUseId": "t1", "status": "success", "content": [{"text": "order notes"}]}}
        assert frames(result) == [{"version": 1, "type": "toolResult", "data": {"toolUseId": "t1", "status": "success"}}]

    def test_preserves_korean_text(self):
        out = list(translate(events.text_delta("안녕하세요")))
        # Must not be \u-escaped: the client renders it directly.
        assert "안녕하세요" in out[0]


class TestErrors:
    def test_the_sdks_own_error_shape_is_an_internal_error(self):
        sdk_error = {"error": "boom: arn:aws:bedrock:...", "error_type": "ValueError", "message": "An error occurred during streaming"}
        [frame] = frames(sdk_error)
        assert frame == events.error("internal", "Agent runtime error (ValueError)")
        assert "arn" not in json.dumps(frame)

    @pytest.mark.parametrize("chunk", [{"error": "unexpected"}, {"error": "Serialization failed", "original_type": "dict"}])
    def test_any_error_shaped_object_is_an_internal_error(self, chunk):
        assert [(f["type"], f["data"]["code"], f["data"]["retryable"]) for f in frames(chunk)] == [("error", "internal", True)]

    def test_unknown_codes_become_internal(self):
        chunk = {"version": 1, "type": "error", "data": {"code": "exploded", "retryable": False, "message": "x"}}
        assert frames(chunk) == [events.error("internal", "x")]


class TestJunk:
    def test_non_dict_ignored(self):
        assert list(translate("string")) == []
        assert list(translate(None)) == []
        assert list(translate([1, 2])) == []

    def test_unknown_shape_ignored(self):
        assert frames({"init_event_loop": True}) == []
        assert frames({"version": 2, "type": "textDelta", "data": {"text": "x"}}) == []
        assert frames({"event": {"contentBlockDelta": {"delta": {"text": "x"}}}}) == []
        assert frames({"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": "search_products"}}]}}) == []


class TestRelay:
    def test_nothing_follows_the_terminal_frame(self):
        relay = Relay()
        assert relay.feed(events.text_delta("a")) == [events.text_delta("a")]
        assert relay.feed(events.done("end_turn")) == [events.done("end_turn")]
        assert relay.feed(events.text_delta("late")) == []
        assert relay.feed(events.error("internal", "late")) == []
        assert relay.fail("upstream", "x") == [] and relay.finish() == []
        assert relay.terminal == events.done("end_turn")

    def test_a_stream_that_ends_without_a_result_is_upstream(self):
        relay = Relay()
        relay.feed(events.text_delta("a"))
        assert relay.finish() == [events.error("upstream", "Agent stream ended without a result")]

    def test_an_empty_stream_is_upstream(self):
        assert Relay().finish()[0]["data"]["code"] == "upstream"

    def test_an_api_side_failure_is_sent_once(self):
        relay = Relay()
        assert relay.fail("deadline", "Request deadline exceeded") == [events.error("deadline", "Request deadline exceeded")]
        assert relay.fail("upstream", "x") == []


class TestSseFraming:
    def test_frame_ends_with_blank_line(self):
        out = list(translate(events.text_delta("x")))[0]
        assert out.startswith("data: ")
        assert out.endswith("\n\n")

    def test_frame_payload_is_single_line(self):
        # A newline inside the JSON would split the SSE frame and corrupt it.
        out = list(translate(events.text_delta("line1\nline2")))[0]
        assert out.count("\n") == 2
        assert json.loads(out[len("data: ") :].strip())["data"]["text"] == "line1\nline2"


def test_utf8_and_multiline_frames_split_at_every_byte():
    decoder = SSEDecoder()
    raw = 'data: {"version": 1, "type": "textDelta",\r\ndata: "data": {"text":"겨울 자켓"}}\r\n\r\n'.encode()
    payloads = []
    for byte in raw:
        payloads.extend(decoder.feed(bytes([byte])))
    payloads.extend(decoder.feed(b"", final=True))
    assert payloads == [events.text_delta("겨울 자켓")]


def test_an_oversized_frame_is_refused():
    with pytest.raises(ValueError, match="Oversized"):
        SSEDecoder().feed(b"data: " + b"x" * 262145)
