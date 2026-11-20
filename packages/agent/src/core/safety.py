"""Deterministic tool boundaries; prompts and model guardrails are additional controls.

Per invocation (one shopper turn) ToolSafety allows only allowlisted tools, at most
`max_calls` tool calls and arguments up to 8 KiB, and caps results at 64 KiB. With a
guardrail configured it also screens Gateway tool results before the model reads them,
because a Converse guardrail does not evaluate toolResult content. Each activation is
logged with the tool's name and the reason, never with argument or result values.
"""

import asyncio
import json
from typing import Any, Optional

import boto3
from botocore.config import Config
from strands.hooks import AfterToolCallEvent, AfterToolsEvent, BeforeInvocationEvent, BeforeToolCallEvent, HookProvider

from utils.logger import logger

MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 65536
BLOCKED_RESULT = "The tool result was blocked by the guardrail."
# The turn's final assistant message when the model keeps asking for tools past the budget.
# The shopper sees it: app.py streams it as the turn's text.
BUDGET_TURN_END = "한 번의 요청에서 확인할 수 있는 정보가 많아서 여기서 멈췄어요. 조금 더 구체적으로 다시 물어봐 주세요."
# Screening runs inside a tool call, so a slow ApplyGuardrail call must fail fast (and closed).
SCREEN_CLIENT_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"mode": "standard", "total_max_attempts": 2})


class GuardrailScreen:
    """Bedrock ApplyGuardrail over text the model is about to read."""

    def __init__(self, guardrail_id: str, guardrail_version: str, *, region: str, client: Any = None):
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version
        self._client = client or boto3.client("bedrock-runtime", region_name=region, config=SCREEN_CLIENT_CONFIG)

    def intervenes(self, text: str) -> bool:
        response = self._client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.guardrail_version,
            # Tool output is input to the model, so the input policies (prompt attacks included) apply.
            source="INPUT",
            content=[{"text": {"text": text}}],
        )
        # Only an explicit "NONE" lets the result through.
        return response.get("action") != "NONE"


class ToolSafety(HookProvider):
    def __init__(self, allowed_tools, max_calls=8, *, screen: Optional[GuardrailScreen] = None):
        self.allowed = frozenset(allowed_tools)
        self.max_calls = max_calls
        self.screen = screen
        self.calls = 0
        self.batches_over_budget = 0

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeInvocationEvent, self.reset)
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)
        registry.add_callback(AfterToolsEvent, self.after_tools)

    def reset(self, event):
        self.calls = 0
        self.batches_over_budget = 0

    def before(self, event):
        self.calls += 1
        name = event.tool_use.get("name", "")
        if name not in self.allowed:
            _deny(event, name, "not_allowed", "Tool is not authorized")
        elif self.calls > self.max_calls:
            _deny(event, name, "budget", "Tool budget exhausted for this request; answer with what you have")
        elif len(json.dumps(event.tool_use.get("input", {})).encode()) > MAX_ARGUMENT_BYTES:
            _deny(event, name, "argument_too_large", "Tool arguments exceed the size limit")

    async def after(self, event):
        if event.cancel_message is not None:
            return  # denied in before(); the result is the denial itself
        name = event.tool_use.get("name", "")
        if len(json.dumps(event.result, default=str).encode()) > MAX_RESULT_BYTES:
            logger.warning("tool_result_oversize", tool=name)
            event.result = _error_result(event, "Tool response exceeds the size limit")
        elif self.screen is not None and "___" in name:
            await self._screen(event, name)

    async def _screen(self, event, name: str) -> None:
        """Replace a Gateway tool's result the guardrail objects to, or can't check."""
        text = _model_text(event.result)
        if text is not None and not text.strip():
            return  # nothing for the model to read
        try:
            # None means content the guardrail can't read as text (images, documents): fail closed.
            blocked = text is None or await asyncio.to_thread(self.screen.intervenes, text)
        except Exception as exc:
            logger.error("tool_result_screen_failed", tool=name, error_type=type(exc).__name__)
        else:
            if not blocked:
                return
            logger.warning("tool_result_blocked", tool=name)
        event.result = _error_result(event, BLOCKED_RESULT)

    def after_tools(self, event):
        """End the turn once the model keeps asking for tools past the budget.

        The first tool batch that runs over the budget gets "budget exhausted" results, and the
        model one more call to answer from what it has. If it asks for tools again, the turn ends
        here (AfterToolsEvent.end_turn) instead of calling the model with cancelled tools again.
        Ending at the first batch would give the shopper BUDGET_TURN_END instead of an answer
        from the results the model already has.
        """
        if self.calls <= self.max_calls:
            return
        self.batches_over_budget += 1
        if self.batches_over_budget > 1:
            event.end_turn = BUDGET_TURN_END
            logger.warning("tool_budget_ended_turn", calls=self.calls, max_calls=self.max_calls)


def _deny(event, name: str, reason: str, message: str) -> None:
    event.cancel_tool = message
    logger.warning("tool_denied", tool=name, reason=reason)


def _error_result(event, text: str) -> dict:
    return {"toolUseId": event.tool_use["toolUseId"], "status": "error", "content": [{"text": text}]}


def _model_text(result) -> Optional[str]:
    """The text a result shows the model, or None if it carries content other than text and JSON."""
    parts = []
    for block in result.get("content") or []:
        if "text" in block:
            parts.append(str(block["text"]))
        elif "json" in block:
            parts.append(json.dumps(block["json"], default=str))
        else:
            return None
    return "\n".join(parts)
