"""Typed config loader.

Reads `.toml` from the package root (copy an example from config/) and validates
it with pydantic so a malformed config fails at synth rather than at deploy.
"""

import os
import re
import tomllib
from functools import lru_cache
import json
from pathlib import Path
from typing import Literal, Optional

from aria_catalog.definition import CatalogDefinition, DefinitionError, definition_to_dict, load_definition
from pydantic import BaseModel as PydanticBaseModel, Field, IPvAnyNetwork, PrivateAttr, ValidationInfo, field_validator, model_validator


# An AgentCore Gateway host, the only one the agent sends its token to (agent core/config.py).
_GATEWAY_HOST = re.compile(r"[a-z0-9-]+\.gateway\.bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com")

class BaseModel(PydanticBaseModel):
    model_config = {"extra": "forbid"}

_CONFIG_PATH = Path(__file__).resolve().parent.parent / ".toml"


class AppConfig(BaseModel):
    ns: str
    stage: Literal["Dev", "Prod"]
    account: str = Field(default="", pattern=r"^([0-9]{12})?$")
    region: str = ""


class VpcConfig(BaseModel):
    vpc_id: str = Field(default="", alias="vpcId")
    nat_gateways: int = Field(default=1, alias="natGateways", ge=1, le=3)
    interface_endpoints: list[str] = Field(default_factory=lambda: ["ecr", "ecr-docker", "logs"], alias="interfaceEndpoints")


class BuildEnvironmentConfig(BaseModel):
    cpu_architecture: Literal["ARM64", "X86_64"] = Field(alias="cpuArchitecture")


class MemoryStrategiesConfig(BaseModel):
    """Long-term extraction strategies, each an independent pipeline.

    Only consulted when `memory.longTerm` is on; each maps to one CfnMemory
    strategy and one retrieval namespace in the agent. Toggling them updates the
    Memory resource in place, so the conversation history is kept.
    """

    # user_facts — semantic facts about the shopper.
    semantic: bool = True
    # conversation_summaries — per-session recap for a returning conversation.
    summarization: bool = True
    # user_preferences — cross-session sizes, colours, styles, brands.
    user_preference: bool = Field(default=True, alias="userPreference")

    def any_enabled(self) -> bool:
        return self.semantic or self.summarization or self.user_preference


class MemoryConfig(BaseModel):
    """AgentCore Memory settings.

    Short-term memory, the store of conversation events, is always on: it is how a
    conversation survives a Runtime session that idled out, and what the session list
    and history read. `longTerm` turns extraction on: the strategies below distil
    events into preferences, facts and summaries retrieved into later turns. Off by
    default, since it keeps derived data about each shopper.
    """

    long_term: bool = Field(default=False, alias="longTerm")
    strategies: MemoryStrategiesConfig = Field(default_factory=MemoryStrategiesConfig)
    # Days AgentCore Memory keeps conversation events; the service accepts 7 to 365.
    event_expiry_days: int = Field(default=90, alias="eventExpiryDays", ge=7, le=365)
    top_k: int = Field(default=5, alias="topK")
    relevance_score: float = Field(default=0.3, alias="relevanceScore")

    @property
    def active_strategies(self) -> list[str]:
        """Enabled long-term strategy keys, or [] for short-term only."""
        if not self.long_term:
            return []
        # Order pinned to the CfnMemory strategy list and its namespace test.
        selected = {
            "user_preference": self.strategies.user_preference,
            "semantic": self.strategies.semantic,
            "summarization": self.strategies.summarization,
        }
        return [key for key, on in selected.items() if on]


class RuntimeConfig(BaseModel):
    """AgentCore Runtime settings for the agent container."""

    model_id: str = Field(alias="modelId")
    max_tokens: int = Field(default=2048, alias="maxTokens")
    temperature: float = 0.3
    idle_session_timeout: int = Field(default=900, alias="idleSessionTimeout")
    max_lifetime: int = Field(default=3600, alias="maxLifetime")
    # Tool calls the agent may make in one turn; the agent refuses further calls.
    max_tool_calls: int = Field(default=8, alias="maxToolCalls", ge=1, le=20)
    # Turns one conversation may run before the agent asks the shopper to start a new one.
    max_session_turns: int = Field(default=100, alias="maxSessionTurns", ge=1, le=500)


class ApiConfig(BaseModel):
    """The ECS FastAPI service that fronts the Runtime for the web client."""

    # Who may reach the load balancer, besides the VPC. In Prod each entry must be /8 or longer
    # (IPv6: /32) unless allowPublicIngress is set.
    allow_ip_list: list[IPvAnyNetwork] = Field(default_factory=list, alias="allowIpList")
    # Prod only: set true to accept allowIpList entries broader than /8 (IPv6: /32), such as
    # 0.0.0.0/0, which open the load balancer to the internet. WAF still fronts it.
    allow_public_ingress: bool = Field(default=False, alias="allowPublicIngress")
    server_port: int = Field(default=8000, alias="serverPort")
    certificate_arn: str = Field(default="", alias="certificateArn")
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4000"], alias="corsAllowOrigins")
    min_tasks: int = Field(default=1, alias="minTasks", ge=1)
    max_tasks: int = Field(default=3, alias="maxTasks", ge=1)
    # Seconds one turn may take, end to end. At most 120: the load balancer's idle timeout is
    # 150 s and the browser gives up after 130 s.
    request_timeout: int = Field(default=120, alias="requestTimeout", ge=10, le=120)
    # Requests one client IP may send in any 5 minutes before WAF blocks it. Shoppers behind one
    # NAT share an IP, so raise it for offices and campuses; the WafRateLimitBlocks alarm reports blocks.
    waf_rate_limit: int = Field(default=100, alias="wafRateLimit", ge=10, le=2_000_000_000)

    @property
    def broad_ingress(self) -> list[str]:
        """allowIpList entries broader than /8 (IPv6: /32), which open the load balancer to much of the internet."""
        return [str(network) for network in self.allow_ip_list if network.prefixlen < (8 if network.version == 4 else 32)]



class PolicyConfig(BaseModel):
    """AgentCore Policy (Cedar) settings for Gateway tool authorization.

    Cedar is default-deny, so an incorrect policy set in ENFORCE mode removes
    the agent's tools entirely. Roll out in LOG_ONLY, read the decision logs,
    then switch.
    """

    enabled: bool = False
    # LOG_ONLY evaluates and logs without blocking; ENFORCE actually denies.
    mode: Literal["LOG_ONLY", "ENFORCE"] = "LOG_ONLY"


class GuardrailConfig(BaseModel):
    """Bedrock Guardrail applied to the agent's model invocations.

    AUDIT evaluates the content filters and reports matches in the trace
    without blocking; ENFORCE blocks. Disabling removes the guardrail entirely
    and is forbidden in prod, which also requires ENFORCE. streamProcessing is the stage's choice.
    """

    enabled: bool = True
    mode: Literal["AUDIT", "ENFORCE"] = "ENFORCE"
    # How replies stream through the guardrail. "sync" checks each reply before any of it
    # reaches the shopper. On Dev, with the guardrail checking the whole
    # conversation (Converse's default), "sync" reached first text about 0.9 s later than
    # "async" (2.61 s vs 1.71 s; 0.76 s with no guardrail); checking only the latest message,
    # as the agent does, "async" took 1.56 s. "async" streams at once and checks in the
    # background: a reply that breaks a filter can show briefly before the stream is cut, and
    # sensitive-information masking is unsupported. The shipped filters block rather than mask.
    stream_processing: Literal["sync", "async"] = Field(default="sync", alias="streamProcessing")
    # Also check what Gateway tools return with the guardrail (ApplyGuardrail, as input)
    # before the model reads it, against prompt injection planted in tool data. Local
    # search results come from the configured catalog and aren't screened. A result the
    # guardrail blocks, or can't check, reaches the model as an error. Has no effect when
    # the guardrail is disabled.
    screen_tool_results: bool = Field(default=True, alias="screenToolResults")

    @property
    def screens_tool_results(self) -> bool:
        """What the Runtime is told: screening needs a guardrail to screen with."""
        return self.enabled and self.screen_tool_results


class EvaluationsConfig(BaseModel):
    """AgentCore Evaluations settings.

    `builtinEvaluators` is the customer-facing knob: add or remove built-in
    evaluator IDs (`aws bedrock-agentcore-control list-evaluators --region <region>`
    lists them all). Custom (domain-specific) evaluators are declared in
    components/evaluators.py and picked up automatically.
    """

    enabled: bool = False
    # 0.01–100. Every evaluated session costs judge-model tokens, so keep this
    # low on live traffic and raise it when investigating a regression.
    sampling_percentage: float = Field(default=10.0, alias="samplingPercentage", ge=0.01, le=100.0)
    session_timeout_minutes: int = Field(default=15, alias="sessionTimeoutMinutes", ge=1, le=60)
    builtin_evaluators: list[str] = Field(
        default_factory=lambda: [
            # Did the agent pick search_products when it should have?
            "Builtin.ToolSelectionAccuracy",
            # Did it map "winter jacket for men" to the right season/gender/category?
            "Builtin.ToolParameterAccuracy",
            # Did the shopper actually get what they asked for?
            "Builtin.GoalSuccessRate",
            # Are recommendations grounded in the tool results, not invented?
            "Builtin.Faithfulness",
            # Does it respect the system prompt (shopping-only, concise, Korean default)?
            "Builtin.InstructionFollowing",
        ],
        alias="builtinEvaluators",
    )
    # Set false to deploy only the built-ins and skip the example custom evaluator.
    include_example_custom_evaluator: bool = Field(
        default=True, alias="includeExampleCustomEvaluator"
    )
    judge_model_id: str = Field(
        default="global.anthropic.claude-haiku-4-5-20251001-v1:0", alias="judgeModelId"
    )


class ProductSearchConfig(BaseModel):
    """The agent's local search tool and the catalog behind it.

    The catalog definition (`catalogFile`) names the catalog's fields, filters, search fields,
    embedded text and card mapping; the search tool, the index and the indexing job come from it.
    It is validated at load, so a bad definition fails synth.
    """

    # fixture is the packaged example catalog, synthetic products searched in memory; Prod rejects it.
    provider: Literal["fixture", "opensearch", "http", "disabled"] = "fixture"
    endpoint: str = ""
    secret_arn: str = Field(default="", alias="secretArn")
    # The OpenSearch alias searched. Each load is a versioned index, <alias>-<version>.
    open_search_index: str = Field(default="products", alias="openSearchIndex", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    result_path: str = Field(default="products", alias="resultPath")
    # The catalog definition, a TOML file, relative to this config file. Empty for the packaged
    # example (aria_catalog/example/catalog.toml), which the fixture provider always uses.
    catalog_file: str = Field(default="", alias="catalogFile")
    _definition: Optional[CatalogDefinition] = PrivateAttr(default=None)

    @field_validator("catalog_file")
    @classmethod
    def resolve_catalog_file(cls, value: str, info: ValidationInfo) -> str:
        """An absolute path, so the config survives a dump and reload. Relative paths start at the
        config file's directory (load_config passes it), else packages/infra, the default .toml's."""
        if not value.strip():
            return ""
        base = Path((info.context or {}).get("config_dir") or _CONFIG_PATH.parent)
        return str((base / value.strip()).resolve())

    @model_validator(mode="after")
    def validate_catalog(self):
        if self.provider == "fixture" and self.catalog_file:
            raise ValueError("tools.productSearch.catalogFile must be empty with provider = fixture: the fixture is "
                             "the packaged example catalog, and searches it with the example's definition")
        if not self.catalog_file:
            self._definition = load_definition()
            return self
        path = Path(self.catalog_file)
        if path.suffix != ".toml" or not path.is_file():
            raise ValueError(f"tools.productSearch.catalogFile {path} must be an existing .toml catalog definition")
        try:
            self._definition = load_definition(path)
        except DefinitionError as error:
            raise ValueError(f"tools.productSearch.catalogFile {path} is not a valid catalog definition: {error}") from None
        return self

    @property
    def definition(self) -> CatalogDefinition:
        """The catalog definition: catalogFile's, or the packaged example's."""
        return self._definition or load_definition(self.catalog_file or None)

    @property
    def definition_json(self) -> str:
        """The definition as compact JSON, CATALOG_DEFINITION for the Runtime and the indexing task."""
        return json.dumps(definition_to_dict(self.definition), separators=(",", ":"), ensure_ascii=False)


class ToolsConfig(BaseModel):
    product_search: ProductSearchConfig = Field(
        default_factory=ProductSearchConfig, alias="productSearch"
    )


class AuthConfig(BaseModel):
    provider: Literal["cognito", "oidc"] = "cognito"
    issuer: str = ""
    client_id: str = Field(default="", alias="clientId")
    audiences: list[str] = Field(default_factory=list)
    allowed_clients: list[str] = Field(default_factory=list, alias="allowedClients")
    scopes: list[str] = Field(default_factory=lambda: ["aria/chat"])
    tenant_claim: str = Field(default="", alias="tenantClaim")
    callback_urls: list[str] = Field(default_factory=lambda: ["http://localhost:4000/auth/callback"], alias="callbackUrls")
    logout_urls: list[str] = Field(default_factory=lambda: ["http://localhost:4000/"], alias="logoutUrls")
    # Managed Cognito only: the prefix of the sign-in domain, <prefix>.auth.<region>.amazoncognito.com.
    # Empty uses "<namespace>-<account ID>". Set it to keep the account ID out of the public login
    # URL, before the first deploy: a pool has one prefix domain and CloudFormation creates the new
    # domain before deleting the old one, so changing it later fails the update. The web app finds
    # the domain through the issuer's discovery document.
    domain_prefix: str = Field(default="", alias="domainPrefix", pattern=r"^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)?$")
    # Managed Cognito only: multi-factor sign-in for shoppers with an authenticator app (TOTP).
    # "optional": a shopper who has registered an authenticator is asked for a code; managed login
    # offers no enrolment, and the web app has no enrolment screen. Cognito advises it with threat
    # protection. "required": managed login makes every shopper register an authenticator, and asks
    # for a code at every sign-in. "off": no MFA. A deployed pool can switch between them.
    mfa: Literal["off", "optional", "required"] = "optional"

    @model_validator(mode="after")
    def validate_oidc(self):
        if self.provider == "oidc" and (not self.issuer.startswith("https://") or not self.client_id or not (self.audiences or self.allowed_clients)):
            raise ValueError("BYO OIDC requires HTTPS issuer, clientId, and audiences or allowedClients")
        if not self.scopes:
            raise ValueError("At least one API access scope is required")
        if any(word in self.domain_prefix for word in ("aws", "amazon", "cognito")):
            raise ValueError("auth.domainPrefix can't contain aws, amazon or cognito; Cognito reserves them")
        if self.provider == "oidc" and self.mfa != "optional":
            raise ValueError("auth.mfa applies to managed Cognito only; with provider = oidc, MFA is your IdP's setting")
        return self


_INFRA_DIR = Path(__file__).resolve().parent.parent


class ApiOperation(BaseModel):
    """One API Gateway operation exposed as a tool, with a fixed tool name."""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    path: str = Field(pattern=r"^/")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    description: str = ""


class ToolTargetConfig(BaseModel):
    """A customer tool behind the Gateway. See docs/guides/gateway-onboarding.md for each kind.

    lambda: a Lambda function with an inline tool schema, called with the Gateway's IAM role.
    mcp: a remote MCP server, called with an OAuth or API-key credential provider.
    openapi: an HTTPS API described by an OpenAPI document (a JSON file in this repo, or S3).
      Each operationId becomes a tool; called with an OAuth or API-key credential provider.
    apigateway: a REST API stage. `operations` names the tools; called with the Gateway's
      IAM role (SigV4, execute-api:Invoke on that stage only) or an API key.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,50}$")
    kind: Literal["lambda", "mcp", "openapi", "apigateway"]
    endpoint: str = ""
    lambda_arn: str = Field(default="", alias="lambdaArn")
    tool_schema: list[dict] = Field(default_factory=list, alias="toolSchema")
    # openapi: exactly one of these. A file path is relative to packages/infra.
    schema_file: str = Field(default="", alias="schemaFile")
    schema_s3_uri: str = Field(default="", alias="schemaS3Uri")
    # apigateway
    rest_api_id: str = Field(default="", alias="restApiId")
    stage: str = ""
    operations: list[ApiOperation] = Field(default_factory=list)
    allowed_tools: list[str] = Field(alias="allowedTools", min_length=1)
    # A Cedar template (.cedar) under components/policies/, relative to packages/infra, deployed once
    # per allowed tool. Any folder there except sample/, which holds the saved-items sample's own policies.
    policy_file: str = Field(default="", alias="policyFile")
    credential_type: Literal["GATEWAY_IAM_ROLE", "OAUTH", "API_KEY"] = Field(default="GATEWAY_IAM_ROLE", alias="credentialType")
    provider_arn: str = Field(default="", alias="providerArn")
    scopes: list[str] = Field(default_factory=list)
    # OAUTH targets: the Gateway gets a token for your API's own client, and x-aria-actor-id names the shopper.
    grant_type: Literal["CLIENT_CREDENTIALS"] = Field(default="CLIENT_CREDENTIALS", alias="grantType")
    # API_KEY targets: where the Gateway puts the key, e.g. HEADER "x-api-key".
    api_key_location: Literal["HEADER", "QUERY_PARAMETER"] = Field(default="HEADER", alias="apiKeyLocation")
    api_key_name: str = Field(default="", alias="apiKeyName")

    @field_validator("grant_type", mode="before")
    @classmethod
    def validate_grant_type(cls, value):
        if value == "AUTHORIZATION_CODE":
            raise ValueError("grantType AUTHORIZATION_CODE needs a consent flow in the shopper's browser and a return "
                             "URL, and ARIA has neither; use CLIENT_CREDENTIALS, the default, and identify the shopper "
                             "by the x-aria-actor-id header")
        return value

    @model_validator(mode="after")
    def validate_target(self):
        if self.kind == "lambda" and not set(self.allowed_tools).issubset({t.get("name") for t in self.tool_schema}):
            raise ValueError("allowedTools must appear in the Lambda toolSchema")
        if self.kind == "lambda" and (not self.lambda_arn or not self.tool_schema or self.credential_type != "GATEWAY_IAM_ROLE"):
            raise ValueError("Lambda targets require lambdaArn, toolSchema and GATEWAY_IAM_ROLE")
        if self.kind == "mcp" and (not self.endpoint.startswith("https://") or self.credential_type == "GATEWAY_IAM_ROLE" or not self.provider_arn):
            raise ValueError("MCP targets require HTTPS endpoint and OAuth/API key providerArn")
        if self.kind == "openapi":
            if bool(self.schema_file) == bool(self.schema_s3_uri):
                raise ValueError("OpenAPI targets need exactly one of schemaFile or schemaS3Uri")
            if self.schema_s3_uri and not self.schema_s3_uri.startswith("s3://"):
                raise ValueError("schemaS3Uri must be an s3:// URI")
            if self.credential_type == "GATEWAY_IAM_ROLE" or not self.provider_arn:
                raise ValueError("OpenAPI targets need an OAuth or API key providerArn; use kind=apigateway for IAM-signed API Gateway calls")
            if self.schema_file:
                operation_ids = self.openapi_operation_ids()
                if not set(self.allowed_tools) <= operation_ids:
                    raise ValueError(f"allowedTools must be operationIds in {self.schema_file}: {sorted(operation_ids)}")
        if self.kind == "apigateway":
            if not self.rest_api_id or not self.stage or not self.operations:
                raise ValueError("API Gateway targets need restApiId, stage and operations")
            if self.credential_type == "OAUTH":
                raise ValueError("API Gateway targets use GATEWAY_IAM_ROLE or API_KEY")
            if self.credential_type == "API_KEY" and not self.provider_arn:
                raise ValueError("API_KEY targets need providerArn")
            if not set(self.allowed_tools) <= {op.name for op in self.operations}:
                raise ValueError("allowedTools must be operation names")
        if self.credential_type == "API_KEY" and self.kind in ("openapi", "apigateway") and not self.api_key_name:
            raise ValueError("API_KEY targets need apiKeyName, the header or query parameter that carries the key")
        if self.policy_file:
            self.policy_document()
        return self

    def policy_document(self) -> str:
        """The target's Cedar template. Confined to components/policies/, outside the sample's folder."""
        root = (_INFRA_DIR / "components" / "policies").resolve()
        path = (_INFRA_DIR / self.policy_file).resolve()
        if not path.is_relative_to(root) or path.is_relative_to(root / "sample") or path.suffix != ".cedar":
            raise ValueError(f"policyFile must be a .cedar file under components/policies/, outside sample/: {self.policy_file}")
        if not path.is_file():
            raise ValueError(f"policyFile {self.policy_file} does not exist under packages/infra")
        return path.read_text(encoding="utf-8")

    def openapi_document(self) -> str:
        """The inline OpenAPI document. JSON only, so synth needs no YAML parser."""
        path = (_INFRA_DIR / self.schema_file).resolve()
        if not path.is_relative_to(_INFRA_DIR) or path.suffix != ".json":
            raise ValueError("schemaFile must be a .json file under packages/infra")
        return path.read_text(encoding="utf-8")

    def openapi_header_parameters(self) -> set[str]:
        """Header parameters the inline document declares, lowercased. Resolves local $refs."""
        document = json.loads(self.openapi_document())
        shared = document.get("components", {}).get("parameters", {})

        def resolve(parameter):
            ref = parameter.get("$ref", "")
            return shared.get(ref.removeprefix("#/components/parameters/"), {}) if ref else parameter

        declared = [parameter for item in document.get("paths", {}).values() for parameter in item.get("parameters", [])]
        declared += [parameter for item in document.get("paths", {}).values() for op in item.values()
                     if isinstance(op, dict) for parameter in op.get("parameters", [])]
        return {resolve(p).get("name", "").lower() for p in declared if resolve(p).get("in") == "header"}

    def openapi_operation_ids(self) -> set[str]:
        document = json.loads(self.openapi_document())
        return {op["operationId"] for item in document.get("paths", {}).values() for op in item.values()
                if isinstance(op, dict) and "operationId" in op}


# The shopper's actor ID on Gateway requests. Agent code sets it; the interceptor checks it.
ACTOR_HEADER = "x-aria-actor-id"
# The calling trace's ID on requests to targets. The interceptor sets it from the trace headers.
CORRELATION_HEADER = "x-correlation-id"
# The agent's own header namespace, the actor header's included; gateway.forwardHeaders can't name one.
AGENT_HEADER_PREFIX = "x-aria-"
# The resource server and scope the agent's Gateway token is minted for (managed Cognito).
GATEWAY_TOKEN_AUDIENCE = "aria-tools"  # nosec B105 # the Gateway token's audience, a public resource-server name
GATEWAY_TOKEN_SCOPE = f"{GATEWAY_TOKEN_AUDIENCE}/invoke"


class GatewayAuthConfig(BaseModel):
    """How the agent authenticates to the Gateway. The shopper's JWT is never forwarded.

    on_behalf_of: AgentCore Identity exchanges the shopper's token (RFC 8693, delegation)
      for a Gateway token whose `sub` is the shopper and `act.sub` the agent.
    m2m: a client-credentials token for the agent's client. It doesn't name the shopper,
      so targets rely on the x-aria-actor-id header alone.

    Managed Cognito creates the client and credential provider. A BYO IdP or an external
    Gateway names its own AgentCore Identity provider and scopes here.
    """

    flow: Literal["on_behalf_of", "m2m"] = "on_behalf_of"
    credential_provider: str = Field(default="", alias="credentialProvider")
    # BYO IdP only: the agent's own client. Required with a managed Gateway in both flows: the m2m
    # authorizer and the interceptor accept only tokens issued to it. Never a shopper client.
    client_id: str = Field(default="", alias="clientId")
    # BYO IdP, on_behalf_of: the audience the Gateway token is minted for and the authorizer accepts.
    # Never one of auth.audiences, which the shopper's own tokens carry.
    audience: str = ""
    scopes: list[str] = Field(default_factory=list)


class GatewayConfig(BaseModel):
    # managed: provision the Gateway + targets here. external: point at an
    # existing Gateway. disabled: no Gateway — the agent runs with no MCP tools.
    mode: Literal["managed", "external", "disabled"] = "managed"
    endpoint: str = ""
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    authorization_verified: bool = Field(default=False, alias="authorizationVerified")
    targets: list[ToolTargetConfig] = Field(default_factory=list)
    # Custom request headers to propagate end to end (client -> Runtime -> agent
    # -> Gateway -> target). Non-credential context only, e.g. x-tenant-id,
    # x-locale. Never credentials: the shopper's token never leaves the agent
    # (docs/design-docs/identity-and-tokens.md). Never an x-aria-* name, in any
    # casing: that namespace is the agent's own.
    forward_headers: list[str] = Field(default_factory=list, alias="forwardHeaders")
    # A sample Lambda target that needs the shopper's identity (packages/mcp-servers/
    # saved-items-lambda). It exercises the whole Gateway path: agent token, actor header, interceptor, target.
    saved_items_sample: bool = Field(default=False, alias="savedItemsSample")
    agent_auth: GatewayAuthConfig = Field(default_factory=GatewayAuthConfig, alias="agentAuth")

    @model_validator(mode="after")
    def validate_forward_headers(self):
        for name in self.forward_headers:
            if name.lower() in ("authorization", "auth"):
                raise ValueError("Do not propagate Authorization via forwardHeaders. The shopper's token never leaves "
                                 "the agent, and each Gateway target authenticates with its own credentials; see "
                                 "docs/design-docs/identity-and-tokens.md")
            if name.lower() == CORRELATION_HEADER:
                raise ValueError(f"gateway.forwardHeaders can't name {name}: the interceptor sets it from the calling "
                                 "trace, so targets can find the agent's trace")
            if name.lower().startswith(AGENT_HEADER_PREFIX):
                raise ValueError(f"gateway.forwardHeaders can't name {name}: x-aria-* headers are the agent's own, "
                                 "x-aria-actor-id among them, and forwarding one would let the shopper set it for "
                                 "local tools")
        return self


class OperationsConfig(BaseModel):
    alarm_topic_arn: str = Field(default="", alias="alarmTopicArn")
    trace_log_group: str = Field(default="", alias="traceLogGroup")
    capture_content: bool = Field(default=False, alias="captureContent")
    transaction_search_ready: bool = Field(default=False, alias="transactionSearchReady")


class Config(BaseModel):
    auth: AuthConfig = Field(default_factory=AuthConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    operations: OperationsConfig = Field(default_factory=OperationsConfig)
    app: AppConfig
    build_environment: BuildEnvironmentConfig = Field(alias="buildEnvironment")
    runtime: RuntimeConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    evaluations: EvaluationsConfig = Field(default_factory=EvaluationsConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    guardrail: GuardrailConfig = Field(default_factory=GuardrailConfig)
    vpc: Optional[VpcConfig] = None

    @model_validator(mode="after")
    def validate_deployment(self):
        if len({t.name for t in self.gateway.targets}) != len(self.gateway.targets):
            raise ValueError("Gateway target names must be unique")
        propagated = {ACTOR_HEADER, CORRELATION_HEADER, *(name.lower() for name in self.gateway.forward_headers)}
        for target in self.gateway.targets:
            if target.kind == "openapi" and target.schema_file and (declared := sorted(target.openapi_header_parameters() & propagated)):
                # The Gateway can't propagate a header the document declares, and a declared
                # parameter is a tool argument the model fills in: the shopper's identity must not be one.
                raise ValueError(f"{target.schema_file} declares {declared} as parameters. Remove them: the Gateway "
                                 "propagates these headers, and a declared parameter becomes a tool argument the model sets")
        if self.api.max_tasks < self.api.min_tasks:
            raise ValueError("maxTasks must be >= minTasks")
        if self.gateway.saved_items_sample and self.gateway.mode != "managed":
            raise ValueError("gateway.savedItemsSample needs a managed Gateway")
        agent_auth = self.gateway.agent_auth
        if self.gateway_tools and (self.auth.provider == "oidc" or self.gateway.mode == "external"):
            if not agent_auth.credential_provider or not agent_auth.scopes:
                raise ValueError("Gateway tools with a BYO IdP or external Gateway need gateway.agentAuth.credentialProvider and scopes")
            if self.gateway.mode == "managed" and not agent_auth.client_id:
                # Both flows: the interceptor refuses every tool call whose token wasn't issued to this client.
                raise ValueError("Gateway tools with a BYO IdP need gateway.agentAuth.clientId, the agent's own client; "
                                 "the Gateway interceptor accepts only tokens issued to it")
            if self.gateway.mode == "managed" and agent_auth.flow == "on_behalf_of" and not agent_auth.audience:
                raise ValueError("gateway.agentAuth.flow=on_behalf_of with a BYO IdP needs the audience")
        if self.auth.provider == "oidc" and self.gateway.mode != "disabled":
            if agent_auth.flow == "on_behalf_of" and agent_auth.audience and agent_auth.audience in self.auth.audiences:
                raise ValueError("gateway.agentAuth.audience must not be one of auth.audiences: the shopper's own tokens "
                                 "carry those audiences and would pass the Gateway's audience check")
            if agent_auth.flow == "m2m" and agent_auth.client_id and agent_auth.client_id in {self.auth.client_id, *self.auth.allowed_clients}:
                raise ValueError("gateway.agentAuth.clientId must not be a shopper client (auth.clientId or auth.allowedClients): "
                                 "the Gateway would accept the shopper's own tokens")
        if self.policy.enabled and self.policy.mode == "ENFORCE" and (missing := [t.name for t in self.gateway.targets if not t.policy_file]):
            raise ValueError(f"policy.mode=ENFORCE needs a policyFile for every customer target, missing for {missing}: "
                             "Cedar denies by default, so those targets would lose every tool")
        if self.gateway.mode == "external":
            if not self.gateway.endpoint.startswith("https://") or not self.gateway.allowed_tools:
                raise ValueError("External gateway requires HTTPS endpoint and allowedTools")
            # The agent sends its minted token only to an AgentCore Gateway host and ignores any other
            # endpoint, which would leave it without Gateway tools (packages/agent/src/core/config.py).
            host = self.gateway.endpoint.removeprefix("https://").split("/", 1)[0].lower()
            if not _GATEWAY_HOST.fullmatch(host):
                raise ValueError("gateway.endpoint must be an AgentCore Gateway URL, "
                                 "https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp")
            if self.gateway.targets:
                raise ValueError("External gateway cannot provision targets")
        if self.gateway.mode == "disabled":
            # No Gateway means no MCP surface: the Cedar policy engine lives on
            # the Gateway and there is nowhere to attach targets. Local tools
            # such as product search still run in the agent.
            if self.policy.enabled:
                raise ValueError("Cedar Policy runs on the Gateway; set policy.enabled=false when gateway is disabled")
            if self.gateway.targets:
                raise ValueError("Disabled gateway cannot host tool targets")
        if self.tools.product_search.provider == "http" and not self.tools.product_search.endpoint.startswith("https://"):
            raise ValueError("HTTP search requires an HTTPS endpoint")
        if self.memory.long_term and not self.memory.strategies.any_enabled():
            raise ValueError("memory.longTerm requires at least one strategy; set longTerm=false for short-term only")
        if self.evaluations.enabled and not (self.operations.transaction_search_ready and self.operations.trace_log_group and self.operations.capture_content):
            raise ValueError("Evaluations require verified Transaction Search, traceLogGroup, and explicit captureContent consent")
        if self.is_prod:
            if self.vpc and not self.vpc.vpc_id and self.vpc.nat_gateways < 3:
                raise ValueError("Prod managed VPC requires one NAT gateway per AZ")
            if not self.app.account or not self.app.region:
                raise ValueError("Prod requires explicit app.account and app.region")
            if not self.api.certificate_arn or not self.api.allow_ip_list or self.api.min_tasks < 2:
                raise ValueError("Prod requires certificateArn, allowIpList and minTasks >= 2")
            if self.api.broad_ingress and not self.api.allow_public_ingress:
                raise ValueError(f"Prod allowIpList entries {self.api.broad_ingress} are broader than /8 (IPv6: /32) and open the "
                                 "load balancer to the internet; set api.allowPublicIngress = true if that is intended")
            if any(origin == "*" or not origin.startswith("https://") for origin in self.api.cors_allow_origins):
                raise ValueError("Prod requires explicit HTTPS CORS origins")
            if any(not url.startswith("https://") for url in self.auth.callback_urls + self.auth.logout_urls):
                raise ValueError("Prod requires HTTPS OIDC callbacks and logout URLs")
            if self.gateway.mode == "managed" and (not self.policy.enabled or self.policy.mode != "ENFORCE"):
                raise ValueError("Prod managed gateway requires Cedar ENFORCE")
            if self.gateway.mode == "external" and not self.gateway.authorization_verified:
                raise ValueError("Prod external gateway requires verified authorization")
            if self.gateway.saved_items_sample:
                raise ValueError("Prod can't deploy gateway.savedItemsSample, a demo target that serves fixture data")
            if self.tools.product_search.provider == "fixture":
                raise ValueError("Prod can't deploy tools.productSearch.provider = fixture, the committed sample "
                                 "catalog; use http or opensearch for your own catalog, or disabled")
            if not self.operations.alarm_topic_arn:
                raise ValueError("Prod requires alarmTopicArn")
            if not self.guardrail.enabled:
                raise ValueError("Prod requires the guardrail enabled")
            if self.guardrail.mode != "ENFORCE":
                raise ValueError("Prod requires guardrail.mode = ENFORCE: AUDIT blocks nothing, so try AUDIT on Dev")
        return self

    @property
    def gateway_ssm_prefix(self) -> str:
        return f"/{self.ns.lower()}/mcp/endpoints/gateway"

    @property
    def gateway_tools(self) -> list[str]:
        """Tools behind the Gateway, named "<target>___<tool>"."""
        return [name for name in self.allowed_tools if "___" in name]

    @property
    def gateway_token(self) -> dict[str, str]:
        """What the agent mints its Gateway token with: AgentCore Identity flow, provider, scopes, audience."""
        agent_auth = self.gateway.agent_auth
        managed = self.auth.provider == "cognito" and self.gateway.mode == "managed"
        on_behalf_of = agent_auth.flow == "on_behalf_of"
        return {
            "flow": "ON_BEHALF_OF_TOKEN_EXCHANGE" if on_behalf_of else "M2M",
            "provider": agent_auth.credential_provider or (f"{self.ns.lower()}-gateway" if managed else ""),
            "scopes": " ".join(agent_auth.scopes or ([GATEWAY_TOKEN_SCOPE] if managed else [])),
            "audience": (agent_auth.audience or (GATEWAY_TOKEN_AUDIENCE if managed else "")) if on_behalf_of else "",
        }

    @property
    def local_tools(self) -> list[str]:
        """Strands tools that run in the agent (no Gateway hop)."""
        return ["search_products"] if self.tools.product_search.provider != "disabled" else []

    @property
    def allowed_tools(self) -> list[str]:
        """Local tools plus Gateway tools, the latter named "<target>___<tool>"."""
        if self.gateway.mode == "disabled":
            return self.local_tools
        if self.gateway.mode == "external":
            return self.local_tools + self.gateway.allowed_tools
        sample = ["saved-items___get_saved_items"] if self.gateway.saved_items_sample else []
        return self.local_tools + sample + [f"{target.name}___{name}" for target in self.gateway.targets for name in target.allowed_tools]

    @property
    def ns(self) -> str:
        """Namespace prefix for resource names, e.g. "ARIADev"."""
        return f"{self.app.ns}{self.app.stage}"

    @property
    def is_prod(self) -> bool:
        return self.app.stage.lower() == "prod"


@lru_cache(1)
def load_config(path: Optional[Path] = None) -> Config:
    config_path = path or Path(os.environ.get("ARIA_CONFIG", str(_CONFIG_PATH)))
    if not config_path.exists():
        raise FileNotFoundError(
            f"config not found at {config_path}. Copy config/dev.toml to {config_path.name} and edit it."
        )
    # The directory resolves relative paths in the config, such as tools.productSearch.catalogFile.
    return Config.model_validate(tomllib.loads(config_path.read_text(encoding="utf-8")),
                                 context={"config_dir": config_path.resolve().parent})
