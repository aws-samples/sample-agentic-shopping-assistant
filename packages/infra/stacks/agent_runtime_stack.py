"""AgentCore Runtime + Memory for the shopping assistant.

  * CfnRuntime hosts the agent container (VM-per-session).
  * CfnMemory persists conversation events; with memory.longTerm it also extracts preferences, facts and summaries.
  * The agent's local product search reads the selected catalog directly.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_bedrockagentcore as agentcore,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_opensearchservice as oss,
    aws_secretsmanager as secretsmanager,
)
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from cdk_nag import NagSuppressions
from constructs import Construct

from aria_catalog.definition import ENVIRONMENT_NAME, definition_environment
from components.guardrail import Guardrail
from components.images import build_excludes
from components.model_permissions import model_resources
from config.loader import Config
from components.identity import Identity

_AGENT_DIR = Path(__file__).resolve().parent.parent.parent / "agent"

# AgentCore Runtime's limits on environmentVariables, the stricter of the CreateAgentRuntime API
# reference (names of 1 to 100 characters) and the AWS::BedrockAgentCore::Runtime resource schema
# (at most 50 variables, values of at most 2,048 characters; the API allows 5,000).
MAX_ENVIRONMENT_VARIABLES = 50
MAX_ENVIRONMENT_NAME = 100
MAX_ENVIRONMENT_VALUE = 2048

# Namespaces must match packages/agent/src/core/memory.py.
PREFERENCES_NAMESPACE = "/users/{actorId}/preferences"
FACTS_NAMESPACE = "/users/{actorId}/facts"
SUMMARIES_NAMESPACE = "/summaries/{actorId}/{sessionId}"


def checked_environment(environment: dict[str, str]) -> dict[str, str]:
    """The Runtime's environment, after checking it against AgentCore's limits, so synth fails
    instead of the deployment. Values that are tokens resolve at deploy and are left unchecked."""
    if len(environment) > MAX_ENVIRONMENT_VARIABLES:
        raise ValueError(f"The Runtime has {len(environment)} environment variables; AgentCore Runtime takes at most "
                         f"{MAX_ENVIRONMENT_VARIABLES}")
    for name, value in environment.items():
        if not 1 <= len(name) <= MAX_ENVIRONMENT_NAME:
            raise ValueError(f"Environment variable name {name[:40]!r} is longer than AgentCore Runtime's "
                             f"{MAX_ENVIRONMENT_NAME} characters")
        if cdk.Token.is_unresolved(value):
            continue
        # The limit counts characters. UTF-8 bytes are never fewer, so a value that passes here passes there.
        size = len(value.encode("utf-8"))
        if size > MAX_ENVIRONMENT_VALUE:
            hint = (" Shorten the catalog definition: fewer or shorter filter values and descriptions."
                    if name.startswith(ENVIRONMENT_NAME) else "")
            raise ValueError(f"{name} is {size} bytes; AgentCore Runtime takes at most {MAX_ENVIRONMENT_VALUE} "
                             f"characters per environment variable.{hint}")
    return environment


class AgentRuntimeStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        vpc: ec2.IVpc,
        user_pool: cognito.IUserPool | None = None,
        user_pool_client: cognito.IUserPoolClient | None = None,
        discovery_url: str = "",
        open_search_domain: oss.IDomain | None = None,
        open_search_security_group: ec2.ISecurityGroup | None = None,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)
        identity = Identity.resolve(config, discovery_url, user_pool_client.user_pool_client_id if user_pool_client else "")

        ns: str = self.node.try_get_context("ns")
        # Also the OTel service.name prefix that AgentCore Evaluations matches on.
        self.runtime_name = f"{ns.lower()}_agent".replace("-", "_")

        guardrail = Guardrail(self, "Guardrail", mode=config.guardrail.mode) if config.guardrail.enabled else None
        # Short-term memory is always on; config.memory.longTerm adds the extraction strategies.
        self.memory = self._create_memory(config, ns)
        self.memory.apply_removal_policy(cdk.RemovalPolicy.RETAIN if config.is_prod else cdk.RemovalPolicy.DESTROY)
        runtime_role = self._create_runtime_role(config, guardrail)

        image = DockerImageAsset(
            self,
            "AgentImage",
            directory=str(_AGENT_DIR.parent),
            file="agent/Dockerfile",
            # Built from packages/ for the shared contracts and catalog, without the other packages.
            exclude=build_excludes("agent"),
            asset_name="aria-agent",
            # AgentCore Runtime requires arm64 regardless of the ECS setting.
            platform=Platform.LINUX_ARM64,
        )
        image.repository.grant_pull(runtime_role)

        self.runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name=self.runtime_name,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri
                )
            ),
            network_configuration=self._network_configuration(vpc),
            role_arn=runtime_role.role_arn,
            environment_variables=checked_environment({
                **identity.environment(),
                "GATEWAY_ENABLED": str(config.gateway.mode != "disabled").lower(),
                "MCP_SSM_PREFIX": config.gateway_ssm_prefix,
                "GATEWAY_URL": config.gateway.endpoint,
                "MODEL_ID": config.runtime.model_id,
                "TRACE_CAPTURE_CONTENT": str(config.operations.capture_content).lower(),
                "MODEL_CACHE_ENABLED": "false",
                "ALLOWED_TOOLS": ",".join(config.allowed_tools),
                "MAX_TOOL_CALLS": str(config.runtime.max_tool_calls),
                "MAX_SESSION_TURNS": str(config.runtime.max_session_turns),
                "FORWARD_HEADERS": ",".join(config.gateway.forward_headers),
                "REQUEST_TIMEOUT_SECONDS": str(config.api.request_timeout),
                "MODEL_MAX_TOKENS": str(config.runtime.max_tokens),
                "MODEL_TEMPERATURE": str(config.runtime.temperature),
                "AGENTCORE_MEMORY_ID": self.memory_id,
                "AGENTCORE_MEMORY_STRATEGIES": ",".join(config.memory.active_strategies),
                "AGENTCORE_MEMORY_TOP_K": str(config.memory.top_k),
                "AGENTCORE_MEMORY_RELEVANCE_SCORE": str(config.memory.relevance_score),
                "GUARDRAIL_ID": guardrail.guardrail_id if guardrail is not None else "",
                "GUARDRAIL_VERSION": guardrail.guardrail_version if guardrail is not None else "",
                "GUARDRAIL_STREAM_PROCESSING": config.guardrail.stream_processing,
                # The agent screens tool results with ApplyGuardrail (granted below with the guardrail).
                "SCREEN_TOOL_RESULTS": str(config.guardrail.screens_tool_results).lower(),
                # The local search tool's catalog; aria_catalog reads these at call time.
                "SEARCH_PROVIDER": config.tools.product_search.provider,
                "SEARCH_ENDPOINT": config.tools.product_search.endpoint,
                "SEARCH_SECRET_ARN": config.tools.product_search.secret_arn,
                "SEARCH_RESULT_PATH": config.tools.product_search.result_path,
                # The catalog definition the tool's schema, query and cards come from (aria_catalog),
                # in as many values as it needs.
                **definition_environment(config.tools.product_search.definition_json
                                         if config.tools.product_search.provider != "disabled" else ""),
                "OPENSEARCH_HOST": open_search_domain.domain_endpoint if open_search_domain else "",
                "OPENSEARCH_PRODUCT_INDEX": config.tools.product_search.open_search_index,
                # The agent's own Gateway token (core/downstream.py); the shopper's JWT is never forwarded.
                "GATEWAY_AUTH_FLOW": config.gateway_token["flow"],
                "GATEWAY_CREDENTIAL_PROVIDER": config.gateway_token["provider"],
                "GATEWAY_TOKEN_SCOPES": config.gateway_token["scopes"],
                "GATEWAY_TOKEN_AUDIENCE": config.gateway_token["audience"],
                "LOG_LEVEL": "INFO",
            }),
            authorizer_configuration=agentcore.CfnRuntime.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(
                    **identity.authorizer(),
                )
            ),
            # The agent reads the shopper's token only to derive the actor ID and
            # check session ownership; it never forwards it. Custom propagated
            # headers must be allowlisted here too, or the Runtime drops them
            # before the agent sees them.
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=["Authorization", *config.gateway.forward_headers]
            ),
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=config.runtime.idle_session_timeout,
                max_lifetime=config.runtime.max_lifetime,
            ),
        )
        self._grant_product_search(config, runtime_role, open_search_domain, open_search_security_group)
        # The role ARN alone does not order its separate AWS::IAM::Policy.
        self.runtime.node.add_dependency(runtime_role)
        self.runtime.node.add_dependency(self.memory)

        # AgentCore creates the endpoint's log group itself as soon as the Runtime
        # exists, so a CloudFormation-owned LogGroup with the same name fails with
        # AlreadyExists. Adopt the service's group instead: LogRetention applies the
        # 30-day retention (creating the group if it is still missing) and keeps it
        # when the stack is deleted.
        log_group_name = f"/aws/bedrock-agentcore/runtimes/{self.runtime.attr_agent_runtime_id}-DEFAULT"
        log_group_arn = self.format_arn(service="logs", resource="log-group", resource_name=log_group_name,
                                        arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME)
        self.log_retention_role = iam.Role(self, "RuntimeLogRetentionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        self.log_retention_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy"],
            resources=[log_group_arn, f"{log_group_arn}:*"]))
        # The handler also creates its own Lambda log group, sets a 1-day retention on
        # it and writes to it. CloudFormation generates (and may truncate) the function
        # name, so match CDK LogRetention handler groups rather than every log group.
        handler_logs = self.format_arn(service="logs", resource="log-group", resource_name="/aws/lambda/*LogRetention*",
                                       arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME)
        self.log_retention_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:PutRetentionPolicy", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[handler_logs, f"{handler_logs}:*"]))
        # Immutable reference: LogRetention adds a Resource "*" retention grant to any
        # mutable role it is given; this keeps its handler scoped to these log groups.
        self.log_retention = logs.LogRetention(self, "RuntimeLogRetention",
            log_group_name=log_group_name, retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            role=iam.Role.from_role_arn(self, "RuntimeLogRetentionRoleRef", self.log_retention_role.role_arn, mutable=False))
        self.log_retention.node.add_dependency(self.log_retention_role)
        self.log_group = logs.LogGroup.from_log_group_name(self, "RuntimeLogGroup", log_group_name)
        self._suppress_nag(runtime_role)

        cdk.CfnOutput(self, "RuntimeArn", value=self.runtime.attr_agent_runtime_arn)
        cdk.CfnOutput(self, "RuntimeId", value=self.runtime.attr_agent_runtime_id)
        cdk.CfnOutput(self, "MemoryId", value=self.memory_id)

    @property
    def runtime_arn(self) -> str:
        return self.runtime.attr_agent_runtime_arn

    @property
    def memory_id(self) -> str:
        return self.memory.attr_memory_id

    # ── Memory ──────────────────────────────────────────────────

    def _create_memory(self, config: Config, ns: str) -> agentcore.CfnMemory:
        # Each key maps to one long-term strategy; selection comes from
        # config.memory.active_strategies. An empty list = short-term only
        # (conversation event store, no extraction/retrieval).
        strategy_builders = {
            # Cross-session preferences, retrieved automatically per turn.
            "user_preference": agentcore.CfnMemory.MemoryStrategyProperty(
                user_preference_memory_strategy=agentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                    name="user_preferences",
                    namespaces=[PREFERENCES_NAMESPACE],
                    description="Shopping preferences: sizes, colours, styles, brands",
                )
            ),
            "semantic": agentcore.CfnMemory.MemoryStrategyProperty(
                semantic_memory_strategy=agentcore.CfnMemory.SemanticMemoryStrategyProperty(
                    name="user_facts",
                    namespaces=[FACTS_NAMESPACE],
                    description="Facts about the shopper and their past interests",
                )
            ),
            "summarization": agentcore.CfnMemory.MemoryStrategyProperty(
                summary_memory_strategy=agentcore.CfnMemory.SummaryMemoryStrategyProperty(
                    name="conversation_summaries",
                    namespaces=[SUMMARIES_NAMESPACE],
                    description="Per-session conversation summaries",
                )
            ),
        }
        strategies = [strategy_builders[key] for key in config.memory.active_strategies]
        return agentcore.CfnMemory(
            self,
            "Memory",
            name=f"{ns.lower()}_memory".replace("-", "_"),
            description="NORDLY shopping assistant conversation memory",
            event_expiry_duration=config.memory.event_expiry_days,
            # None (not []) when short-term only: CfnMemory rejects an empty list.
            memory_strategies=strategies or None,
        )

    # ── Networking ──────────────────────────────────────────────

    def _network_configuration(self, vpc: ec2.IVpc) -> agentcore.CfnRuntime.NetworkConfigurationProperty:
        """The Runtime always runs in the VPC, with its own security group.

        It reaches AWS services through the VPC's endpoints where it has them, and everything else
        (the Gateway, AgentCore Identity and Memory, Secrets Manager, public catalogs) through its NAT
        gateway. AgentCore supports VPC mode only in some AZ IDs per region; scripts/preflight.py
        checks the subnets.
        """
        self.runtime_security_group = ec2.SecurityGroup(
            self,
            "RuntimeSecurityGroup",
            vpc=vpc,
            description="AgentCore Runtime ENIs",
            allow_all_outbound=True,
        )
        return agentcore.CfnRuntime.NetworkConfigurationProperty(
            network_mode="VPC",
            network_mode_config=agentcore.CfnRuntime.VpcConfigProperty(
                # Must be private subnets: public subnets give no internet route.
                subnets=vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS).subnet_ids,
                security_groups=[self.runtime_security_group.security_group_id],
            ),
        )

    def _grant_product_search(self, config: Config, role: iam.Role, domain, domain_security_group) -> None:
        """Let the local search tool read the selected catalog, and nothing more."""
        settings = config.tools.product_search
        if settings.provider == "opensearch":
            if domain is None or domain_security_group is None:
                raise ValueError("OpenSearch search needs the domain and its security group")
            ec2.CfnSecurityGroupIngress(
                self, "SearchDomainIngress", group_id=domain_security_group.security_group_id,
                source_security_group_id=self.runtime_security_group.security_group_id,
                ip_protocol="tcp", from_port=443, to_port=443,
                description="Only the agent Runtime may query the catalog domain",
            )
            # An OpenSearch `_search` is a POST, so grant that explicitly, scoped to the
            # search paths: a read-only tool must not be able to index or delete. The hybrid
            # query names its search pipeline in the query string (?search_pipeline=), which
            # IAM doesn't see, so the same paths cover it.
            role.add_to_policy(iam.PolicyStatement(
                actions=["es:ESHttpPost", "es:ESHttpGet"],
                resources=[
                    f"{domain.domain_arn}/{settings.open_search_index}/_search",
                    f"{domain.domain_arn}/{settings.open_search_index}/_search/*",
                ],
            ))
            # The query embedding, with the definition's model only. A cross-Region profile also
            # needs the foundation model in the Regions it routes to.
            role.add_to_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=model_resources(settings.definition.embedding_model, self.region, self.account, self.partition),
            ))
        if settings.provider == "http" and settings.secret_arn:
            secretsmanager.Secret.from_secret_complete_arn(self, "SearchCredential", settings.secret_arn).grant_read(role)

    # ── IAM ─────────────────────────────────────────────────────

    def _create_runtime_role(self, config, guardrail) -> iam.Role:
        role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/{self.runtime_name}-*"},
                },
            ),
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=model_resources(config.runtime.model_id, self.region, self.account, self.partition),
            )
        )
        if guardrail is not None:
            role.add_to_policy(iam.PolicyStatement(actions=["bedrock:ApplyGuardrail"], resources=guardrail.apply_resources))
        provider = config.gateway_token["provider"]
        if config.gateway_tools and provider:
            # Mint the agent's Gateway token from this Runtime's workload identity. The
            # Runtime-managed workload identity's name is generated, hence the suffix wildcard.
            prefix = f"arn:{self.partition}:bedrock-agentcore:{self.region}:{self.account}"
            role.add_to_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:GetResourceOauth2Token"],
                resources=[
                    f"{prefix}:workload-identity-directory/default",
                    f"{prefix}:workload-identity-directory/default/workload-identity/*",
                    f"{prefix}:token-vault/default",
                    f"{prefix}:token-vault/default/oauth2credentialprovider/{provider}",
                ],
            ))
            # AgentCore names the secret oauth2/<provider>-<suffix>; the hyphen keeps a provider whose
            # name merely starts with this one (e.g. <provider>2) out of reach.
            role.add_to_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[f"arn:{self.partition}:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity!default/oauth2/{provider}-*"],
            ))
        role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        if config.gateway.mode == "managed":
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[f"arn:{self.partition}:ssm:{self.region}:{self.account}:parameter{config.gateway_ssm_prefix}/unified"],
                )
            )
        # What AgentCoreMemorySessionManager calls (bedrock-agentcore 1.20): the conversation's
        # events, with DeleteEvent for a guardrail redaction or a legacy-event migration, and
        # record retrieval for long-term memory. Listing sessions and deleting records is the API's job.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                ],
                resources=[self.memory.attr_memory_arn],
            )
        )

        # Trace delivery puts a resource policy on the Runtime's own log groups. AgentCore's
        # documented execution role scopes this to the agent's groups, not the account (*).
        role.add_to_policy(iam.PolicyStatement(
            actions=["logs:PutResourcePolicy"],
            resources=[f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/{self.runtime_name}-*"],
        ))
        # CloudWatch Logs — all three statements are required.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/{self.runtime_name}-*"
                ],
            )
        )
        # Without DescribeLogGroups on log-group:* no [runtime-logs] streams are
        # created and container stdout is silently dropped.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:DescribeLogGroups"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:"
                    f"log-group:/aws/bedrock-agentcore/runtimes/{self.runtime_name}-*:log-stream:*"
                ],
            )
        )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources=["*"],
            )
        )
        return role

    def _suppress_nag(self, role: iam.Role) -> None:
        NagSuppressions.add_resource_suppressions(
            role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Exact configured model, the catalog's embedding model and the selected Guardrail profile may route across regions. ECR auth and X-Ray require *. Metric writes are namespace constrained. Logs cover the Runtime's own generated log groups and streams (including their resource policy) and log-group discovery. Memory, the source Guardrail and SSM are exact resources. The Gateway token grant names the one credential provider and its secret's generated suffix; the Runtime-managed workload identity has a generated name."
                    ),
                }
            ],
            True,
        )
        NagSuppressions.add_resource_suppressions(
            self.log_retention_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Retention/creation grants name only the Runtime log group (the :* suffix is its ARN form) and the "
                        "CDK LogRetention handler's own /aws/lambda/*LogRetention* group, whose generated name is unknown at synth"
                    ),
                }
            ],
            True,
        )
