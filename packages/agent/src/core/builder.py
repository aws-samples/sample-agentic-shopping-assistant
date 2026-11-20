"""SessionBuilder — assembles the agent's dependencies with overridable steps.

Each _build_* method owns one concern and is the extension seam: subclass and
override the step you need rather than rewriting the pipeline.

Extension points:
    _build_model        — Bedrock model (caching, guardrails)
    _build_mcp_client   — Gateway MCP transport
    _build_tools        — local Strands tools (product search)
    _build_hooks        — HookProvider instances
    _build_memory       — AgentCore Memory session manager
    _build_agent        — assemble the Strands Agent
"""

import os
from aria_contracts.identity import runtime_actor
from typing import Optional

from strands import Agent, ModelRetryStrategy
from strands.models.bedrock import BedrockModel, CacheConfig
from botocore.config import Config as BotoConfig
from strands.tools.mcp import MCPClient

from core.config import AgentConfig
from core.memory import (
    FACTS_NAMESPACE,
    PREFERENCES_NAMESPACE,
    SUMMARIES_NAMESPACE,
)
from core.session import Session
from core.downstream import DownstreamToken
from core.transport import gateway_transport
from tools.product_search import ProductSearchTool
from core.history import InterruptedTurns
from core.safety import GuardrailScreen, ToolSafety
from prompts.system import build_system_prompt
from utils.logger import logger


def resolve_actor_id(token: str) -> str:
    return runtime_actor(token, os.environ.get("OIDC_ISSUER", ""), os.environ.get("OIDC_TENANT_CLAIM", ""))


# The shopper's actor ID on Gateway requests. Set by agent code, never by the model.
ACTOR_HEADER = "x-aria-actor-id"
# Forward headers never carry these: the agent sets the actor and its own Authorization, and
# x-aria-* is the agent's namespace.
_RESERVED_HEADERS = frozenset({ACTOR_HEADER, "authorization", "auth"})
_RESERVED_PREFIX = "x-aria-"
# Strands starts the MCP client and lists its tools while constructing the Agent, so this
# bounds how long a slow Gateway can hold up a session build.
MCP_STARTUP_TIMEOUT_SECONDS = 10


# How the model call's guardrail treats the conversation. By default Converse checks every message
# on every call, so one blocked reply, or a tool result the filters dislike, would stay in history
# and block every later turn of the session, however harmless. So the guardrail checks only the
# shopper's latest message, plus every reply as it is generated, and a blocked message is replaced
# in history. Gateway tool results are screened separately before the model reads them
# (ToolSafety, SCREEN_TOOL_RESULTS).
# The reply's replacement is also the turn's final message, which the shopper sees in sync stream
# mode (done.message) and in a restored transcript, so it is a polite refusal, not a log note.
GUARDRAIL_REFUSAL = "이 요청에는 답변할 수 없어요. 쇼핑에 관해 다른 질문을 해 주세요."
GUARDRAIL_HISTORY_OPTIONS = {
    "guardrail_latest_message": True,
    "guardrail_redact_input": True,
    "guardrail_redact_input_message": "(안전 정책에 따라 이 메시지는 보관하지 않았어요.)",
    "guardrail_redact_output": True,
    "guardrail_redact_output_message": GUARDRAIL_REFUSAL,
}


def model_retry_strategy() -> ModelRetryStrategy:
    """Retries for a throttled model call: 3 attempts in all, 1 s then 2 s apart.

    Strands' default (6 attempts, 4-64 s apart, about two minutes) would outlast the
    request deadline, and botocore retries each attempt as well (see _build_model).
    """
    return ModelRetryStrategy(max_attempts=3, initial_delay=1, max_delay=4)


class SessionBuilder:
    """Constructs a Session with all dependencies wired up."""

    def __init__(self, config: AgentConfig):
        self.config = config

    def build(
        self,
        token: str,
        session_id: str,
        forward_headers: Optional[dict[str, str]] = None,
        workload_token: Optional[str] = None,
    ) -> Session:
        """Build the session for the shopper `token` identifies.

        The shopper's JWT is read here only to derive the actor ID. Gateway calls use a
        token minted for the agent (see core/downstream.py), never this one.
        """
        actor_id = resolve_actor_id(token)

        tools = self._allowed_local_tools(self._build_tools())
        downstream = self._build_downstream_token()
        if downstream is not None:
            downstream.set_workload_token(workload_token)
        mcp_client = self._build_mcp_client(downstream, actor_id, forward_headers or {})
        if mcp_client is not None:
            # Strands starts the client and lists its tools while constructing the Agent
            # below, so a Gateway that is down, or slower than MCP_STARTUP_TIMEOUT_SECONDS,
            # fails this build.
            tools = tools + [mcp_client]

        agent = self._build_agent(
            tools=tools,
            hooks=self._build_hooks(),
            session_manager=self._build_memory(actor_id, session_id),
        )
        self._init_agent_state(agent, actor_id)

        logger.info(
            "🤖 session built",
            actor_id=actor_id,
            session_id=session_id,
            memory=("long-term" if self.config.memory_strategies else "short-term") if self.config.memory_id else "none",
            mcp="on" if mcp_client is not None else "off",
        )
        return Session(agent, actor_id, downstream)

    # ── overridable steps ───────────────────────────────────────

    def _gateway_tools(self) -> list[str]:
        # Gateway tools are named "<target>___<tool>"; local tools are not.
        return [name for name in self.config.allowed_tools if "___" in name] if self.config.gateway_url else []

    def _build_downstream_token(self) -> Optional[DownstreamToken]:
        """The agent's own Gateway token source, when there are Gateway tools to call."""
        if not self._gateway_tools():
            return None
        if not self.config.gateway_credential_provider:
            raise ValueError("Gateway tools need GATEWAY_CREDENTIAL_PROVIDER: the agent mints its own Gateway token")
        return DownstreamToken(
            provider=self.config.gateway_credential_provider,
            flow=self.config.gateway_auth_flow,
            scopes=self.config.gateway_token_scopes,
            audience=self.config.gateway_token_audience,
            region=self.config.region,
        )

    def _build_mcp_client(
        self,
        downstream: Optional[DownstreamToken],
        actor_id: str,
        forward_headers: Optional[dict[str, str]] = None,
    ) -> Optional[MCPClient]:
        """Connect to the AgentCore Gateway (single endpoint for all Gateway tools).

        Requests carry the agent's minted token, read per request so it can be
        refreshed, and `x-aria-actor-id`: the shopper's actor ID, which the
        Gateway's interceptor checks and passes on to targets. `forward_headers`
        are static custom headers (e.g. x-tenant-id, x-locale) for the interceptor
        to propagate; they are fixed for the session's one connection. An HTTP
        error or a failed mint fails only the call it hit (see core/transport.py).
        """
        if not any("___" in name for name in self.config.allowed_tools):
            # With no Gateway tools allowed there is nothing to list, so skip the
            # connection instead of opening one per session.
            logger.info("no Gateway tools allowed, running with local tools only")
            return None
        if downstream is None:
            logger.warning("⚠️ no gateway url configured, running without MCP tools")
            return None
        headers = {**_custom_headers(forward_headers or {}), ACTOR_HEADER: actor_id}
        return MCPClient(
            lambda: gateway_transport(self.config.gateway_url, downstream, headers),
            startup_timeout=MCP_STARTUP_TIMEOUT_SECONDS,
            tool_filters={"allowed": list(self.config.allowed_tools)},
        )

    def _build_tools(self) -> list:
        """Local Strands tools: called often, and no per-user authorization needed.

        Product search runs here rather than behind the Gateway to skip a network hop on
        every search. Tools that need the shopper's identity or their own credentials are
        Gateway targets instead; see docs/design-docs/tool-placement.md. Only the ones
        named in ALLOWED_TOOLS are offered to the model. The search tool's schema comes
        from the catalog definition in CATALOG_DEFINITION and the values after it (the
        packaged example when it's empty); an invalid definition fails the build.
        """
        return [ProductSearchTool()] if self.config.search_provider != "disabled" else []

    def _allowed_local_tools(self, tools: list) -> list:
        """The local tools in the allowlist; ToolSafety would refuse any other at call time."""
        allowed = []
        for local_tool in tools:
            if local_tool.tool_name in self.config.allowed_tools:
                allowed.append(local_tool)
            else:
                logger.warning("local_tool_not_allowed", tool=local_tool.tool_name)
        return allowed

    def _build_hooks(self) -> list:
        """Override to add HookProviders (e.g. a confirmation hook for HITL).

        Tools do not need an identity-injection hook: Gateway requests carry the
        actor ID header, which the interceptor checks and passes to targets, and
        memory is scoped by actor_id. With SCREEN_TOOL_RESULTS and a guardrail,
        ToolSafety screens Gateway tool results, which a Converse guardrail does
        not evaluate, before the model reads them.
        """
        screen = None
        if self.config.screen_tool_results and self.config.guardrail_id and self.config.guardrail_version:
            screen = GuardrailScreen(self.config.guardrail_id, self.config.guardrail_version, region=self.config.region)
        return [
            ToolSafety(self.config.allowed_tools, self.config.max_tool_calls, screen=screen),
            # A turn cut off by the deadline or a disconnect must not leave two user messages in a row.
            InterruptedTurns(),
        ]

    def _build_memory(self, actor_id: str, session_id: str):
        """Build the AgentCore Memory session manager, or None when no Memory is configured.

        Every deployment has Memory: conversation turns always persist as events (short-term),
        so a conversation survives an idle Runtime session. With long-term strategies on,
        `retrieval_config` also injects relevant preferences and facts into each turn.
        """
        if not self.config.memory_id:
            return None

        from bedrock_agentcore.memory.integrations.strands.config import (
            AgentCoreMemoryConfig,
            RetrievalConfig,
        )
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        # Retrieve only from namespaces whose long-term strategy is enabled.
        # Short-term-only (no strategies) leaves this empty: events still
        # persist, but nothing is injected back into the turn.
        strategy_namespaces = {
            "user_preference": PREFERENCES_NAMESPACE,
            "semantic": FACTS_NAMESPACE,
            "summarization": SUMMARIES_NAMESPACE,
        }
        retrieval = {
            strategy_namespaces[strategy]: RetrievalConfig(
                top_k=self.config.memory_top_k,
                relevance_score=self.config.memory_relevance_score,
            )
            for strategy in self.config.memory_strategies
            if strategy in strategy_namespaces
        }

        memory_config = AgentCoreMemoryConfig(
            memory_id=self.config.memory_id,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval,
            # batch_size=1 (the default) is the only safe value: AgentCore
            # hard-kills containers with no SIGTERM, so anything buffered is lost.
            batch_size=1,
        )
        return AgentCoreMemorySessionManager(
            agentcore_memory_config=memory_config,
            region_name=self.config.region,
        )

    def _build_model(self) -> BedrockModel:
        options = {}
        if self.config.cache_enabled:
            # Cache the static prefixes to cut input tokens on repeat calls: the
            # system prompt, the tool schema, and the conversation so far (a
            # message cache point on the last user turn via cache_config). Only
            # Claude reaches here — config.py gates cache_enabled to Claude
            # because OpenAI rejects cachePoints on Converse (see
            # docs/design-docs/model-caching.md). strategy="auto" resolves to the Claude
            # cachePoint format and self-skips for any non-Claude model.
            options["cache_prompt"] = "default"
            options["cache_tools"] = "default"
            options["cache_config"] = CacheConfig(strategy="auto")
        # Bedrock GPT-5 rejects temperature, including the configured default.
        # Other model families still receive the operator's sampling setting.
        if "openai.gpt-5" not in self.config.model_id:
            options["temperature"] = self.config.temperature
        if self.config.guardrail_id:
            # "sync" holds each reply until the guardrail has checked it; "async" streams it
            # while the check runs. Chosen per deployment in the infra config.
            options["guardrail_stream_processing_mode"] = self.config.guardrail_stream_processing
            options.update(GUARDRAIL_HISTORY_OPTIONS)
        return BedrockModel(
            # One botocore retry per attempt; Strands' retry strategy (see _build_agent) adds
            # at most two more attempts on throttling.
            boto_client_config=BotoConfig(
                connect_timeout=5, read_timeout=60, retries={"mode": "standard", "total_max_attempts": 2}
            ),
            **options,
            model_id=self.config.model_id,
            region_name=self.config.region,
            max_tokens=self.config.max_tokens,
            guardrail_id=self.config.guardrail_id,
            guardrail_version=self.config.guardrail_version,
            streaming=True,
        )

    def _build_agent(self, tools: list, hooks: list, session_manager=None) -> Agent:
        kwargs: dict = {
            "agent_id": "default",
            "model": self._build_model(),
            "tools": tools,
            "system_prompt": build_system_prompt(),
            # Events flow through stream_async, not stdout.
            "callback_handler": None,
            "retry_strategy": model_retry_strategy(),
        }
        if hooks:
            kwargs["hooks"] = hooks
        if session_manager is not None:
            # AgentCore Memory manages history itself; a conversation manager
            # would fight it for control of the message list.
            kwargs["session_manager"] = session_manager
        return Agent(**kwargs)

    def _init_agent_state(self, agent: Agent, actor_id: str) -> None:
        """Durable session metadata; tools read it via tool_context.agent.state."""
        agent.state.set("user_id", actor_id)


def _custom_headers(forward_headers: dict[str, str]) -> dict[str, str]:
    """`forward_headers` without the reserved names, in any casing.

    HTTPX would send a differently cased `X-Aria-Actor-Id` alongside the agent's own
    `x-aria-actor-id`, so the comparison is case-insensitive.
    """
    kept = {}
    for name, value in forward_headers.items():
        lowered = name.lower()
        if lowered in _RESERVED_HEADERS or lowered.startswith(_RESERVED_PREFIX):
            logger.warning("forward_header_dropped", header=lowered)
        else:
            kept[name] = value
    return kept
