"""Session — thin runtime container for one AgentCore session.

AgentCore Runtime is VM-per-session: one container serves exactly one session,
and global state persists for the container's lifetime. So there is no session map,
just one Agent. Requests can still overlap (the Runtime keeps running a turn after its
caller leaves), so each turn carries a cancel event in `in_flight`, and a newer turn
cancels an older one (see app.py).

Does NOT create its own dependencies; SessionBuilder supplies them. The
`downstream` token source is shared with the MCP transport, so
refresh_identity() hands it each request's workload access token without
reconnecting. The shopper's sign-in JWT is never stored here.
"""

import threading
from collections.abc import AsyncIterator
from typing import Any, Optional

from strands import Agent

from core.downstream import DownstreamToken


class Session:
    __slots__ = ("agent", "actor_id", "downstream", "turns", "in_flight")

    def __init__(self, agent: Agent, actor_id: str, downstream: Optional[DownstreamToken] = None):
        self.turns = 0
        # The cancel event of each turn under way, the one holding the agent included.
        self.in_flight: set[threading.Event] = set()
        self.actor_id = actor_id
        self.agent = agent
        self.downstream = downstream

    async def stream_async(
        self,
        message: str,
        *,
        invocation_state: dict[str, Any] | None = None,
        cancel_signal: Optional[threading.Event] = None,
    ) -> AsyncIterator[Any]:
        """Yield all agent events (text deltas, tool calls, tool results).

        `invocation_state` is request-scoped context handed to tools via
        ToolContext (Strands deprecated the old ``**kwargs`` form in 1.55).
        Setting `cancel_signal` stops this invocation at Strands' next safe point.
        """
        async for event in self.agent.stream_async(message, invocation_state=invocation_state, cancel_signal=cancel_signal):
            yield event

    def refresh_identity(self, workload_token: Optional[str]) -> None:
        """Give the Gateway token source this request's workload access token."""
        if self.downstream is not None:
            self.downstream.set_workload_token(workload_token)
