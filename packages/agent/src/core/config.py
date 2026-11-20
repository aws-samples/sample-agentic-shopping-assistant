"""Immutable agent configuration — loaded once at container startup.

Frozen dataclass: config cannot change after creation. Environment variables
come from CfnRuntime `environment_variables`; the Gateway URL comes from SSM so
the Gateway can be redeployed without redeploying the runtime.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional

import boto3
import httpx
from botocore.config import Config

from utils.logger import logger

# Read at container start-up, which a slow SSM call must not hold up for long.
SSM_CLIENT_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"mode": "standard", "total_max_attempts": 3})
# AgentCore Gateway endpoints are https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp.
# Matching the whole host matters: anyone can create other amazonaws.com hosts (an API Gateway
# execute-api stage, an S3 bucket), and the agent sends its minted token to this URL.
GATEWAY_HOST = re.compile(r"[a-z0-9-]+\.gateway\.bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com")
# Names FORWARD_HEADERS can't hold, in any casing: Authorization (and auth) carries the shopper's token,
# and x-aria-* is the agent's own namespace, x-aria-actor-id included. Forwarded headers reach local
# tools' invocation_state with the caller's values. The infra loader rejects the same names, and
# core/builder.py also keeps them off Gateway requests.
_RESERVED_FORWARD_HEADERS = frozenset({"authorization", "auth"})
_AGENT_HEADER_PREFIX = "x-aria-"


def checked_gateway_url(url: str, source: str) -> str:
    """`url` if it is an https:// AgentCore Gateway URL, else "" (no Gateway).

    The agent sends its minted token to this URL, so one from the environment or SSM that
    points anywhere else is refused. It is parsed the way the Gateway client will parse it.
    """
    try:
        parsed = httpx.URL(url)
        trusted = parsed.scheme == "https" and bool(GATEWAY_HOST.fullmatch(parsed.host))
    except httpx.InvalidURL:
        trusted = False
    if trusted:
        return url
    logger.warning("gateway_url_rejected", source=source, reason="not an https:// AgentCore Gateway URL")
    return ""


@dataclass(frozen=True)
class AgentConfig:
    gateway_url: str
    model_id: str
    region: str
    max_tokens: int
    temperature: float
    memory_id: str
    memory_strategies: tuple[str, ...]
    memory_top_k: int
    memory_relevance_score: float
    guardrail_id: Optional[str]
    guardrail_version: Optional[str]
    # "sync" (Bedrock's default) or "async"; see infra GuardrailConfig.stream_processing.
    guardrail_stream_processing: str = "sync"
    # Run Gateway tool results through the guardrail (ApplyGuardrail) before the model reads
    # them; Converse guardrails don't evaluate toolResult content. Needs the guardrail above.
    screen_tool_results: bool = False
    # Catalog behind the local search tool: fixture | opensearch | http | disabled. The
    # provider's own settings (SEARCH_ENDPOINT, OPENSEARCH_HOST, ...) are read by aria_catalog.
    search_provider: str = "fixture"
    # How the agent gets its own token for the Gateway (see core/downstream.py). The
    # shopper's JWT is never forwarded; these name the AgentCore Identity provider to mint from.
    gateway_auth_flow: str = "ON_BEHALF_OF_TOKEN_EXCHANGE"
    gateway_credential_provider: str = ""
    gateway_token_scopes: tuple[str, ...] = ()
    gateway_token_audience: str = ""

    cache_enabled: bool = False
    allowed_tools: tuple[str, ...] = ()
    # Custom request headers to read off the inbound request and propagate to
    # tools (to the Gateway for remote tools, into invocation_state for local).
    # from_env() refuses Authorization, auth and x-aria-* names.
    forward_headers: tuple[str, ...] = ()
    max_tool_calls: int = 8
    max_turns: int = 100
    request_timeout: int = 120

    @classmethod
    def from_env(cls) -> "AgentConfig":
        region = os.environ.get("AWS_REGION", "ap-northeast-2")
        allowed_tools = tuple(filter(None, os.environ.get("ALLOWED_TOOLS", "").split(",")))

        # A Gateway URL is needed only when the Gateway is on and a Gateway tool
        # ("<target>___<tool>") is allowed; otherwise the agent runs with local tools and
        # makes no SSM call.
        gateway_enabled = os.environ.get("GATEWAY_ENABLED", "true").lower() == "true"
        gateway_url = ""
        if gateway_enabled and any("___" in name for name in allowed_tools):
            gateway_url, source = os.environ.get("GATEWAY_URL", ""), "env"
            if not gateway_url:
                # Resolved at startup so Gateway redeploys don't require a runtime redeploy.
                prefix = os.environ.get("MCP_SSM_PREFIX", "")
                if not prefix:
                    raise ValueError("Set GATEWAY_URL or a namespaced MCP_SSM_PREFIX")
                ssm = boto3.client("ssm", region_name=region, config=SSM_CLIENT_CONFIG)
                gateway_url, source = ssm.get_parameter(Name=f"{prefix}/unified")["Parameter"]["Value"], "ssm"
            gateway_url = checked_gateway_url(gateway_url, source)

        guardrail_id = os.environ.get("GUARDRAIL_ID") or None
        guardrail_version = os.environ.get("GUARDRAIL_VERSION") or None
        # Bedrock rejects a guardrail id without a version, so require both or neither.
        if bool(guardrail_id) != bool(guardrail_version):
            raise ValueError("GUARDRAIL_ID and GUARDRAIL_VERSION must be supplied together")
        search_provider = os.environ.get("SEARCH_PROVIDER", "fixture") or "fixture"
        if search_provider not in ("fixture", "opensearch", "http", "disabled"):
            raise ValueError("SEARCH_PROVIDER must be fixture, opensearch, http or disabled")
        gateway_auth_flow = os.environ.get("GATEWAY_AUTH_FLOW", "ON_BEHALF_OF_TOKEN_EXCHANGE") or "ON_BEHALF_OF_TOKEN_EXCHANGE"
        if gateway_auth_flow not in ("ON_BEHALF_OF_TOKEN_EXCHANGE", "M2M"):
            raise ValueError("GATEWAY_AUTH_FLOW must be ON_BEHALF_OF_TOKEN_EXCHANGE or M2M")
        stream_processing = os.environ.get("GUARDRAIL_STREAM_PROCESSING", "sync") or "sync"
        if stream_processing not in ("sync", "async"):
            raise ValueError("GUARDRAIL_STREAM_PROCESSING must be sync or async")
        forward_headers = tuple(filter(None, os.environ.get("FORWARD_HEADERS", "").split(",")))
        reserved = [name for name in forward_headers
                    if name.lower() in _RESERVED_FORWARD_HEADERS or name.lower().startswith(_AGENT_HEADER_PREFIX)]
        if reserved:
            raise ValueError(f"FORWARD_HEADERS can't name {', '.join(reserved)}: Authorization and auth carry the "
                             "shopper's token, and x-aria-* headers are the agent's own")

        model_id = os.environ.get("MODEL_ID", "global.openai.gpt-5.6-luna")
        cache = os.environ.get("MODEL_CACHE_ENABLED", "false").lower() == "true"
        # Caching is Converse-only here, and Bedrock Converse honours cache points
        # only for Anthropic Claude. OpenAI models reject cachePoint blocks on
        # Converse ("AccessDeniedException: unsupported model ... prompt caching",
        # verified live) — their caching lives on the Responses/Mantle path, which
        # this agent does not use (no Bedrock guardrail there). So gate caching to
        # Claude. See docs/design-docs/model-caching.md.
        if cache and "anthropic.claude" not in model_id:
            raise ValueError("Prompt caching is only supported for Anthropic Claude models on Converse; set MODEL_CACHE_ENABLED=false")
        return cls(
            cache_enabled=cache,
            allowed_tools=allowed_tools,
            forward_headers=forward_headers,
            max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "8")),
            max_turns=int(os.environ.get("MAX_SESSION_TURNS", "100")),
            request_timeout=int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120")),
            gateway_url=gateway_url,
            model_id=model_id,
            region=region,
            max_tokens=int(os.environ.get("MODEL_MAX_TOKENS", 2048)),
            temperature=float(os.environ.get("MODEL_TEMPERATURE", 0.3)),
            memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
            memory_strategies=tuple(filter(None, os.environ.get("AGENTCORE_MEMORY_STRATEGIES", "").split(","))),
            memory_top_k=int(os.environ.get("AGENTCORE_MEMORY_TOP_K", 5)),
            memory_relevance_score=float(os.environ.get("AGENTCORE_MEMORY_RELEVANCE_SCORE", 0.3)),
            guardrail_id=guardrail_id,
            guardrail_version=guardrail_version,
            guardrail_stream_processing=stream_processing,
            screen_tool_results=os.environ.get("SCREEN_TOOL_RESULTS", "false").lower() == "true",
            search_provider=search_provider,
            gateway_auth_flow=gateway_auth_flow,
            gateway_credential_provider=os.environ.get("GATEWAY_CREDENTIAL_PROVIDER", ""),
            gateway_token_scopes=tuple(filter(None, os.environ.get("GATEWAY_TOKEN_SCOPES", "").split())),
            gateway_token_audience=os.environ.get("GATEWAY_TOKEN_AUDIENCE", ""),
        )
