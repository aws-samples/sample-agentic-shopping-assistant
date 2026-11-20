"""Drive the deployed agent by calling its AgentCore Runtime endpoint over HTTPS.

The Runtime uses a CUSTOM_JWT authorizer, so each call carries the eval user's
bearer access token (not SigV4). See evals/login.py for getting one.

Session IDs: the AgentCore dataset runner names each scenario's session
`<scenario_id>-<uuid4>`, but the agent only accepts
`<sha256(principal)>_<hex32>` IDs that match the caller (aria_contracts.identity).
So:

* RuntimeInvoker derives the principal namespace from the token's own claims,
  opens one owner-bound session ID per scenario, sends every turn of that
  scenario to it, and records runner ID -> Runtime ID.
* SessionMappedCollector queries CloudWatch with the Runtime ID, then relabels
  the spans with the runner ID. The runner attaches ground truth (assertions,
  expected trajectory, expected responses) to its own ID, so without the
  relabel the service would not match it to the spans.
"""

import base64
import json
import re
import threading
from datetime import datetime
from typing import Any, Callable, List, Optional
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from aria_contracts import events as turn_events
from aria_contracts.identity import actor_id, new_session_id
from bedrock_agentcore.evaluation import AgentInvokerInput, AgentInvokerOutput, AgentSpanCollector

SESSION_ATTRIBUTE = "session.id"
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"

# (runtime session ID, request body) -> decoded SSE event payloads
Send = Callable[[str, dict], List[Any]]


def _parse_runtime_arn(runtime_arn: str) -> tuple[str, str]:
    parts = runtime_arn.split(":")
    if len(parts) != 6 or parts[2] != "bedrock-agentcore" or not parts[5].startswith("runtime/"):
        raise ValueError("Expected an AgentCore Runtime ARN: arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>")
    return parts[3], parts[5].removeprefix("runtime/")


def runtime_region(runtime_arn: str) -> str:
    return _parse_runtime_arn(runtime_arn)[0]


def runtime_log_group(runtime_arn: str, qualifier: str = "DEFAULT") -> str:
    """The log group the Runtime stack creates for an endpoint (agent_runtime_stack.py)."""
    return f"/aws/bedrock-agentcore/runtimes/{_parse_runtime_arn(runtime_arn)[1]}-{qualifier}"


def runtime_invocation_url(runtime_arn: str, qualifier: str = "DEFAULT") -> str:
    region, _ = _parse_runtime_arn(runtime_arn)
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(runtime_arn, safe='')}/invocations?qualifier={quote(qualifier, safe='')}"
    )


def token_owner(token: str, tenant_claim: str = "") -> str:
    """The principal namespace the agent will derive from this token.

    Decodes the caller's own token without verifying it: the Runtime verifies
    the signature, and this only predicts the owner the agent compares against.
    """
    try:
        payload = token.removeprefix("Bearer ").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise ValueError("ARIA_ACCESS_TOKEN is not a JWT access token") from exc
    return actor_id(claims, tenant_claim)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirects are refused for Runtime invocations")


def parse_sse(raw: bytes) -> List[Any]:
    """Decode `data:` frames (CRLF and multi-line data included) into JSON payloads."""
    payloads = []
    for frame in re.split(r"\r?\n\r?\n", raw.decode("utf-8")):
        data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
        if data and data != "[DONE]":
            payloads.append(json.loads(data))
    return payloads


def https_sender(url: str, token: str, timeout: float = 130) -> Send:
    if not url.startswith("https://"):
        raise ValueError("Runtime invocations require HTTPS")
    bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
    opener = build_opener(_NoRedirect())

    def send(session_id: str, body: dict) -> List[Any]:
        request = Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": bearer,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                SESSION_HEADER: session_id,
            },
        )
        with opener.open(request, timeout=timeout) as response:
            return parse_sse(response.read())

    return send


def reply_text(payloads: List[Any]) -> str:
    """Assemble the assistant's answer from the agent's turn events (`aria_contracts.events`).

    Raises on an error event, including the AgentCore SDK's own error shape, which
    `events.decode` turns into an `internal` error. When the guardrail intervened
    and `done` carries its replacement text, that text is the answer the shopper
    is left with.
    """
    text = []
    for payload in payloads:
        event = turn_events.decode(payload)
        if event is None:
            continue
        data = event["data"]
        if event["type"] == turn_events.ERROR:
            raise RuntimeError(f"Agent error: {data['code']}: {data['message']}")
        if event["type"] == turn_events.TEXT_DELTA:
            text.append(data["text"])
        elif event["type"] == turn_events.DONE and data.get("message"):
            return data["message"]
    return "".join(text)


def _message(payload: Any) -> str:
    """A dataset turn `input` is a string or an object such as {"role": "user", "content": "..."}."""
    if isinstance(payload, dict):
        payload = payload.get("prompt") or payload.get("message") or payload.get("content")
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("Turn input must be a non-empty string, or an object with prompt/message/content")
    return payload


class RuntimeInvoker:
    def __init__(self, send: Send, owner: str):
        self._send = send
        self._owner = owner
        self._sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def sessions(self) -> dict[str, str]:
        """Runner session ID -> Runtime session ID, for finding a scenario's traces later."""
        with self._lock:
            return dict(self._sessions)

    def runtime_session_id(self, runner_session_id: str) -> Optional[str]:
        with self._lock:
            return self._sessions.get(runner_session_id)

    def __call__(self, invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        with self._lock:
            session_id = self._sessions.setdefault(invoker_input.session_id, new_session_id(self._owner))
        reply = reply_text(self._send(session_id, {"prompt": _message(invoker_input.payload)}))
        if not reply:
            # Mark the scenario FAILED rather than let the judges score a missing answer.
            raise RuntimeError("No assistant response")
        return AgentInvokerOutput(agent_output=reply)


class SessionMappedCollector(AgentSpanCollector):
    def __init__(self, inner: AgentSpanCollector, invoker: RuntimeInvoker):
        self._inner = inner
        self._invoker = invoker

    def collect(self, session_id: str, start_time: datetime, end_time: datetime) -> List[dict]:
        runtime_session_id = self._invoker.runtime_session_id(session_id)
        if runtime_session_id is None:
            return []
        documents = self._inner.collect(runtime_session_id, start_time, end_time)
        for document in documents:
            _relabel(document.get("attributes"), runtime_session_id, session_id)
        return documents


def _relabel(attributes: Any, old: str, new: str) -> None:
    # ADOT documents carry the ID as a flat "session.id" key; accept a nested form too.
    if not isinstance(attributes, dict):
        return
    if attributes.get(SESSION_ATTRIBUTE) == old:
        attributes[SESSION_ATTRIBUTE] = new
    nested = attributes.get("session")
    if isinstance(nested, dict) and nested.get("id") == old:
        nested["id"] = new
