"""System prompt builder.

The prompt is static and holds nothing user-specific: with long-term memory on,
AgentCore Memory's `retrieval_config` adds the shopper's preferences and facts to
each user message, never to the prompt. Keeping the prefix byte-stable is what
makes Bedrock prompt caching effective.
"""

from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


def build_system_prompt() -> str:
    return (_PROMPT_DIR / "system.md").read_text(encoding="utf-8").strip()
