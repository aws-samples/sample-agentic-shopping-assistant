"""Keep the conversation valid after a turn that was cut off.

A turn cut off by the request deadline or by the caller leaving keeps what reached history: the
shopper's message with no reply, or a tool result with no answer after it. The next turn would
then send two user messages in a row, which Bedrock's Converse API rejects for some models, and
the session would fail every turn from then on. Strands itself appends a "Tool was interrupted."
result for a tool call left without one, which is a user message too.

Before each turn, with the agent's invocation lock held, `InterruptedTurns` closes such a turn
with a short assistant message. It goes through Strands' own append path, so a Memory session
manager records it like any other message.
"""

from strands.hooks import BeforeInvocationEvent, HookProvider

from utils.logger import logger

# What the model, and a restored transcript, see in place of the reply that never arrived.
INTERRUPTED_REPLY = "(이전 답변이 중간에 끊겼어요.)"


class InterruptedTurns(HookProvider):
    """Close a cut-off turn before the next one starts, so roles keep alternating."""

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeInvocationEvent, self.before_invocation)

    def before_invocation(self, event: BeforeInvocationEvent) -> None:
        incoming = event.messages or []
        if not incoming or incoming[0].get("role") != "user":
            return  # nothing new from the shopper (e.g. resuming an interrupt)
        history = event.agent.messages
        if not history or history[-1].get("role") != "user":
            return
        event.messages = [{"role": "assistant", "content": [{"text": INTERRUPTED_REPLY}]}, *incoming]
        logger.info("interrupted_turn_closed")
