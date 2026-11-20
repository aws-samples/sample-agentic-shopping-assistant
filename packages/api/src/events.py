"""Relay the agent's turn events to the browser as server-sent events.

The agent streams `aria_contracts.events` (the module documents the schema): text deltas, tool calls
by ID and name, tool results with display-schema content only, coded errors and `done`. Each one
arrives from the Runtime as an SSE `data:` line, is checked again with `events.decode`, and goes to
the browser as `data: {"version": 1, "type": ..., "data": ...}`. The SDK's own error shape, and any
other error-shaped object, becomes an `internal` error.

`Relay` holds one invocation's stream to exactly one terminal frame (`done` or `error`).
"""

import codecs
import json
import re
from typing import Any, Iterator, Optional

from aria_contracts import events

MAX_FRAME_CHARS = 262144


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def parse_sse_line(line: str) -> Any:
    """Return the JSON payload of an SSE `data:` line, or None."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def agent_events(chunk: Any) -> list[dict]:
    """The turn events in one payload from the Runtime."""
    event = events.decode(chunk)
    return [event] if event is not None else []


def translate(chunk: Any) -> Iterator[str]:
    """Yield the browser's SSE frames for one payload from the Runtime."""
    for event in agent_events(chunk):
        yield sse(event)


class Relay:
    """One invocation's frames: exactly one terminal frame, and nothing after it."""

    def __init__(self) -> None:
        self.terminal: Optional[dict] = None

    def feed(self, chunk: Any) -> list[dict]:
        return self._send(agent_events(chunk))

    def fail(self, code: str, message: str) -> list[dict]:
        """An error the API saw itself, unless the turn already ended."""
        return self._send([events.error(code, message)])

    def finish(self) -> list[dict]:
        """At the end of the Runtime's stream: an `upstream` error if the turn never ended."""
        return self.fail("upstream", "Agent stream ended without a result")

    def _send(self, found: list[dict]) -> list[dict]:
        sent = []
        for event in found:
            if self.terminal is not None:
                break
            sent.append(event)
            if event["type"] in events.TERMINAL:
                self.terminal = event
        return sent


class SSEDecoder:
    """Incremental UTF-8 + SSE framing, including CRLF and multiline data."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer = ""

    def feed(self, raw: bytes, final: bool = False):
        self.buffer += self.decoder.decode(raw, final=final)
        frames = re.split(r"\r?\n\r?\n", self.buffer)
        self.buffer = frames.pop()
        if len(self.buffer) > MAX_FRAME_CHARS:
            raise ValueError("Oversized SSE frame")
        if final and self.buffer:
            frames.append(self.buffer)
            self.buffer = ""
        result = []
        for frame in frames:
            if len(frame) > MAX_FRAME_CHARS:
                raise ValueError("Oversized SSE frame")
            data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
            if data and data != "[DONE]":
                result.append(json.loads(data))
        return result
