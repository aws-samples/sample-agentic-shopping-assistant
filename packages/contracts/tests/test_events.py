"""The turn-event contract shared by the agent (producer) and the API (relay to the browser)."""

import json

import pytest

from aria_contracts import events

PRODUCTS = {"schemaVersion": "product.v1", "count": 1, "products": [{"id": "p-1", "productDisplayName": "겨울 자켓"}]}
PRODUCT_TEXT = json.dumps(PRODUCTS, ensure_ascii=False, separators=(",", ":"))


def test_the_error_codes_and_their_retry_hints_are_fixed():
    # The web and the CloudWatch metric filters depend on these exact codes.
    assert dict(events.ERROR_CODES) == {
        "init_failed": True,
        "owner_mismatch": False,
        "turn_limit": False,
        "deadline": True,
        "busy": True,
        "throttled": True,
        "internal": True,
        "upstream": True,
        "invalid_request": False,
    }
    with pytest.raises(TypeError):
        events.ERROR_CODES["new"] = True


def test_constructors_build_the_versioned_envelope():
    assert events.text_delta("안녕") == {"version": 1, "type": "textDelta", "data": {"text": "안녕"}}
    assert events.tool_use("t1", "search_products") == {"version": 1, "type": "toolUse", "data": {"toolUseId": "t1", "name": "search_products"}}
    assert events.error("deadline", "Request deadline exceeded") == {
        "version": 1, "type": "error", "data": {"code": "deadline", "retryable": True, "message": "Request deadline exceeded"}}
    assert events.done("end_turn") == {"version": 1, "type": "done", "data": {"stopReason": "end_turn"}}
    assert events.done("guardrail_intervened", "차단되었습니다.")["data"] == {"stopReason": "guardrail_intervened", "message": "차단되었습니다."}


def test_unknown_error_codes_are_a_bug():
    with pytest.raises(ValueError):
        events.error("oops", "x")


class TestToolResults:
    def test_a_display_schema_is_shown_as_one_compact_text_block(self):
        pretty = json.dumps(PRODUCTS, ensure_ascii=False, indent=2)
        result = events.tool_result("t1", "success", [{"text": pretty}])
        assert result["data"] == {"toolUseId": "t1", "status": "success", "content": [{"text": PRODUCT_TEXT}]}

    def test_text_blocks_are_joined_with_newlines_like_the_web(self):
        between_tokens = [{"text": '{"schemaVersion": "product.v1",'}, {"image": {}}, {"text": '"products": []}'}]
        assert json.loads(events.display_content(between_tokens)[0]["text"]) == {"schemaVersion": "product.v1", "count": 0, "products": []}
        inside_a_string = [{"text": '{"schemaVersion": "product'}, {"text": '.v1", "products": []}'}]
        assert events.display_content(inside_a_string) is None

    @pytest.mark.parametrize(
        "content",
        [
            None,
            [],
            "text",
            [{"text": "Customer search result"}],
            [{"text": json.dumps({"schemaVersion": "orders.v1", "orders": []})}],
            [{"text": json.dumps({"products": []})}],
            [{"text": json.dumps({"schemaVersion": ["product.v1"]})}],
            [{"text": json.dumps([PRODUCTS])}],
            [{"json": PRODUCTS}],
            [{"text": "[" * 100000}],
        ],
    )
    def test_anything_else_is_not_shown(self, content):
        assert "content" not in events.tool_result("t1", "success", content)["data"]

    @pytest.mark.parametrize("status", ["error", None, "cancelled"])
    def test_a_failed_result_shows_nothing(self, status):
        result = events.tool_result("t1", status, [{"text": PRODUCT_TEXT}])
        assert result["data"] == {"toolUseId": "t1", "status": "error"}


class TestDecode:
    @pytest.mark.parametrize(
        "event",
        [
            events.text_delta("안녕"),
            events.tool_use("t1", "saved-items___get_saved_items"),
            events.tool_result("t1", "success", [{"text": PRODUCT_TEXT}]),
            events.tool_result("t1", "error"),
            events.error("busy", "Another turn is in progress"),
            events.done("max_tokens"),
            events.done("guardrail_intervened", "차단되었습니다."),
        ],
    )
    def test_events_round_trip_through_json(self, event):
        assert events.decode(json.loads(json.dumps(event))) == event

    def test_fields_outside_the_contract_are_dropped(self):
        tool = {"version": 1, "type": "toolUse", "data": {"toolUseId": "t1", "name": "search_products", "input": {"q": "x"}}, "extra": 1}
        assert events.decode(tool) == events.tool_use("t1", "search_products")
        result = {"version": 1, "type": "toolResult", "data": {"toolUseId": "t1", "status": "success", "content": [{"text": "secret"}]}}
        assert events.decode(result) == events.tool_result("t1", "success")
        done = {"version": 1, "type": "done", "data": {"stopReason": "end_turn", "message": "full transcript"}}
        assert events.decode(done) == events.done("end_turn")

    def test_errors_are_normalized_to_known_codes(self):
        assert events.decode({"version": 1, "type": "error", "data": {"code": "turn_limit", "retryable": True, "message": "x"}}) == events.error("turn_limit", "x")
        assert events.decode({"version": 1, "type": "error", "data": {"code": "nope"}}) == events.error("internal", "")
        assert events.decode({"version": 1, "type": "error", "data": {"code": ["busy"]}})["data"]["code"] == "internal"

    @pytest.mark.parametrize(
        "foreign,message",
        [
            ({"error": "boom: arn:aws:...", "error_type": "ValueError", "message": "An error occurred during streaming"}, "Agent runtime error (ValueError)"),
            ({"error": "Serialization failed", "original_type": "dict"}, "Agent runtime error"),
            ({"error": {"nested": True}, "error_type": "not an identifier!"}, "Agent runtime error"),
        ],
    )
    def test_error_shaped_objects_become_internal_errors_without_their_text(self, foreign, message):
        assert events.decode(foreign) == events.error("internal", message)

    @pytest.mark.parametrize(
        "event",
        [
            None,
            "data: text",
            ["textDelta"],
            {"delta": {"text": "raw strands event"}},
            {"init_event_loop": True},
            {"version": 2, "type": "textDelta", "data": {"text": "x"}},
            {"version": True, "type": "textDelta", "data": {"text": "x"}},
            {"version": 1, "type": "textDelta", "data": "x"},
            {"version": 1, "type": "textDelta", "data": {"text": ""}},
            {"version": 1, "type": "textDelta", "data": {"text": 1}},
            {"version": 1, "type": "toolUse", "data": {"toolUseId": "t1"}},
            {"version": 1, "type": "toolResult", "data": {"toolUseId": "t1", "status": "maybe"}},
            {"version": 1, "type": "done", "data": {}},
            {"version": 1, "type": "reasoning", "data": {"text": "x"}},
        ],
    )
    def test_anything_else_is_dropped(self, event):
        assert events.decode(event) is None


def test_a_display_result_keeps_only_the_schemas_fields():
    payload = {
        "schemaVersion": "product.v1",
        "count": 1,
        "internalNotes": "margin 42%",
        "products": [{"id": "sample-1", "productDisplayName": "Winter Jacket", "debug": {"score": 0.9}, "year": 2026}],
    }
    shown = json.loads(events.display_content([{"text": json.dumps(payload)}])[0]["text"])
    assert shown == {"schemaVersion": "product.v1", "count": 1,
                     "products": [{"id": "sample-1", "productDisplayName": "Winter Jacket", "year": 2026}]}
