"""The events of one shopper turn: what the agent streams and what the API relays to the browser.

Both hops carry the same envelope::

    {"version": 1, "type": <type>, "data": {...}}

The agent yields these from its Runtime entrypoint, and the AgentCore SDK sends each one as an
SSE `data:` line. The API checks each one again with `decode` and sends it to the browser as the
same frame.

    textDelta   {"text": str}
    toolUse     {"toolUseId": str, "name": str}. Never the tool's input.
    toolResult  {"toolUseId": str, "status": "success" | "error", "content"?: [{"text": str}]}.
                `content` only when the result's text is JSON whose `schemaVersion` is in
                DISPLAY_SCHEMAS; otherwise the browser sees no tool output at all.
    error       {"code": str, "retryable": bool, "message": str}. `code` is one of ERROR_CODES;
                `message` is developer detail in English, not text to show the shopper.
    done        {"stopReason": str, "message"?: str}. The Strands stop reason, once, when the
                turn ends normally. `message` is the guardrail's replacement text, only with
                "guardrail_intervened".

A turn ends with exactly one `done` or `error`. Nothing else leaves the agent: no model reasoning,
tool inputs, invocation state, system prompt or transcript.
"""

import json
from types import MappingProxyType
from typing import Any, Optional

VERSION = 1

TEXT_DELTA = "textDelta"
TOOL_USE = "toolUse"
TOOL_RESULT = "toolResult"
ERROR = "error"
DONE = "done"
TERMINAL = frozenset({ERROR, DONE})

# Tool results whose JSON the browser may render. Everything else stays in the agent.
DISPLAY_SCHEMAS = frozenset({"product.v1"})
# The fields of a product.v1 product the browser renders; any other key stays in the agent.
PRODUCT_FIELDS = (
    "id", "productDisplayName", "gender", "masterCategory", "subCategory",
    "articleType", "baseColour", "season", "year", "usage",
)

# Error code -> whether sending the same request again can succeed.
ERROR_CODES = MappingProxyType({
    "init_failed": True,  # the session could not be built
    "owner_mismatch": False,  # the session belongs to another caller
    "turn_limit": False,  # the session's turn budget is spent
    "deadline": True,  # the turn ran past its deadline
    "busy": True,  # another turn is in flight on this session
    "throttled": True,  # the model kept throttling after retries
    "internal": True,  # any other failure
    "upstream": True,  # the Runtime call failed, as seen by the API
    "invalid_request": False,  # the request itself is invalid
})

GUARDRAIL_INTERVENED = "guardrail_intervened"


def _event(kind: str, data: dict) -> dict:
    return {"version": VERSION, "type": kind, "data": data}


def text_delta(text: str) -> dict:
    return _event(TEXT_DELTA, {"text": text})


def tool_use(tool_use_id: str, name: str) -> dict:
    return _event(TOOL_USE, {"toolUseId": tool_use_id, "name": name})


def tool_result(tool_use_id: str, status: Any, content: Any = None) -> dict:
    """A finished tool call. `content` is the tool's result content, kept only if displayable."""
    data = {"toolUseId": tool_use_id, "status": "success" if status == "success" else "error"}
    shown = display_content(content) if data["status"] == "success" else None
    if shown is not None:
        data["content"] = shown
    return _event(TOOL_RESULT, data)


def error(code: str, message: str) -> dict:
    if code not in ERROR_CODES:
        raise ValueError(f"Unknown error code: {code}")
    return _event(ERROR, {"code": code, "retryable": ERROR_CODES[code], "message": message})


def done(stop_reason: str, message: Optional[str] = None) -> dict:
    data = {"stopReason": stop_reason}
    if message:
        data["message"] = message
    return _event(DONE, data)


def display_content(content: Any) -> Optional[list]:
    """The result content blocks the browser may render, or None.

    The browser joins a result's text blocks with newlines and parses them as JSON, so the same is
    done here. Only an allowlisted `schemaVersion` passes; it is sent back as one compact text block.
    """
    if not isinstance(content, list):
        return None
    texts = [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
    if not texts:
        return None
    try:
        payload = json.loads("\n".join(texts))
    except (ValueError, RecursionError):
        return None
    version = payload.get("schemaVersion") if isinstance(payload, dict) else None
    if not isinstance(version, str) or version not in DISPLAY_SCHEMAS:
        return None
    # Rebuilt from the schema's own fields: a tool (a third-party Gateway target included) can't
    # pass other keys, such as internal notes, through to the browser.
    products = payload.get("products")
    kept = [
        {field: item[field] for field in PRODUCT_FIELDS if field in item}
        for item in (products if isinstance(products, list) else [])
        if isinstance(item, dict)
    ]
    shown = {"schemaVersion": version, "count": len(kept), "products": kept}
    return [{"text": json.dumps(shown, ensure_ascii=False, separators=(",", ":"))}]


def decode(event: Any) -> Optional[dict]:
    """Check one event received from the agent and return it rebuilt from known fields, or None.

    An error-shaped object that isn't an event becomes an `internal` error: the AgentCore SDK's own
    `{"error", "error_type", "message"}` when the entrypoint raises, its `{"error": "Serialization
    failed", ...}` fallback, or any other `{"error": ...}` object. Their text is not passed on.
    """
    if not isinstance(event, dict):
        return None
    version, data = event.get("version"), event.get("data")
    if version == VERSION and not isinstance(version, bool) and isinstance(data, dict):
        return _decode(event.get("type"), data)
    if "error" in event:
        error_type = event.get("error_type")
        detail = f" ({error_type})" if isinstance(error_type, str) and error_type.isidentifier() else ""
        return error("internal", f"Agent runtime error{detail}")
    return None


def _decode(kind: Any, data: dict) -> Optional[dict]:
    if kind == TEXT_DELTA:
        text = data.get("text")
        return text_delta(text) if isinstance(text, str) and text else None
    if kind == TOOL_USE:
        tool_use_id, name = data.get("toolUseId"), data.get("name")
        return tool_use(tool_use_id, name) if isinstance(tool_use_id, str) and isinstance(name, str) else None
    if kind == TOOL_RESULT:
        tool_use_id, status = data.get("toolUseId"), data.get("status")
        if isinstance(tool_use_id, str) and status in ("success", "error"):
            return tool_result(tool_use_id, status, data.get("content"))
        return None
    if kind == ERROR:
        code, message = data.get("code"), data.get("message")
        code = code if isinstance(code, str) and code in ERROR_CODES else "internal"
        return error(code, message if isinstance(message, str) else "")
    if kind == DONE:
        stop_reason, message = data.get("stopReason"), data.get("message")
        if not isinstance(stop_reason, str) or not stop_reason:
            return None
        keep = stop_reason == GUARDRAIL_INTERVENED and isinstance(message, str)
        return done(stop_reason, message if keep else None)
    return None
