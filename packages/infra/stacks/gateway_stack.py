"""AgentCore Gateway: one MCP endpoint for tools that need their own auth.

Frequent tools that need no per-user authorization, such as product search, run
in the agent instead (see docs/design-docs/tool-placement.md). The Gateway accepts only the
agent's own token, minted by AgentCore Identity; the interceptor checks the
shopper's actor ID header and never forwards credentials.
"""

import shutil
from pathlib import Path

import aws_cdk as cdk
import jsii
from aws_cdk import (
    aws_cognito as cognito,
    aws_bedrockagentcore as agentcore,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ssm as ssm,
)
from cdk_nag import NagSuppressions
from constructs import Construct

from components.policy import build_policy, load_policies, PolicySpec
from config.loader import ACTOR_HEADER, CORRELATION_HEADER, Config
from components.identity import Identity

_PACKAGES = Path(__file__).resolve().parent.parent.parent
_INTERCEPTOR_DIR = _PACKAGES / "mcp-servers" / "gateway-interceptor"
_SAVED_ITEMS_DIR = _PACKAGES / "mcp-servers" / "saved-items-lambda" / "src"
_CATALOG_DIR = _PACKAGES / "catalog" / "src" / "aria_catalog"

# Gateway exposes tools as "<target>___<tool>"; Cedar action IDs use this form.
SAVED_ITEMS_TARGET = "saved-items"
SAVED_ITEMS_ACTION = f"{SAVED_ITEMS_TARGET}___get_saved_items"

# The line the interceptor logs for each call it refuses (DENIAL_LOG in its handler; a test
# pins the two together), and the metric that counts them for the Operations stack's alarm.
INTERCEPTOR_DENIAL_LOG = "interceptor: target invocation denied"
INTERCEPTOR_DENIALS_METRIC = "InterceptorDenials"


def metric_namespace(ns: str) -> str:
    """The namespace of the stage's own log metrics, shared with the Operations stack."""
    return f"ARIA/{ns}"


@jsii.implements(cdk.ILocalBundling)
class _CopySavedItemsSources:
    """Bundle the sample's handler with the catalog package it reads, without Docker."""

    def try_bundle(self, output_dir: str, *, image, **_options) -> bool:
        shutil.copytree(_SAVED_ITEMS_DIR, output_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(_CATALOG_DIR, Path(output_dir) / "aria_catalog", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return True


class GatewayStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        user_pool: cognito.IUserPool | None = None,
        user_pool_client: cognito.IUserPoolClient | None = None,
        agent_client: cognito.IUserPoolClient | None = None,
        discovery_url: str = "",
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)
        identity = Identity.resolve(config, discovery_url, user_pool_client.user_pool_client_id if user_pool_client else "")
        # The agent's own client: the M2M authorizer accepts only its tokens, and the interceptor
        # checks every tool call's token names it. Managed Cognito: the Auth stack's agent client.
        agent_client_id = config.gateway.agent_auth.client_id or (agent_client.user_pool_client_id if agent_client else "")
        authorizer = self._authorizer(config, identity, agent_client_id)

        ns = self.node.try_get_context("ns")
        # AgentCore appends a generated suffix to the name for the Gateway's ID and ARN.
        self.gateway_name = f"{ns.lower()}-tools"

        gateway_role = self._create_gateway_role()

        interceptor_fn = self._create_interceptor_lambda(gateway_role, config, agent_client_id)
        self.interceptor_function = interceptor_fn
        self.saved_items_function: lambda_.Function | None = None
        # Fixed names for alarms in other stacks, so no construct reference crosses stacks.
        self.lambda_function_names = {"Interceptor": f"{ns.lower()}-gateway-interceptor"}

        # Created before the Gateway so it can be referenced in the Gateway's
        # policyEngineConfiguration; the policies themselves are added after,
        # since Cedar validates them against the Gateway's tool schema.
        self.policy_engine = None
        if config.policy.enabled:
            self.policy_engine = agentcore.CfnPolicyEngine(
                self,
                "PolicyEngine",
                name=f"{ns}ToolAuthz".replace("-", "_"),
                description=f"Cedar authorization for {ns} gateway tools",
            )
            engine_arn = self.policy_engine.attr_policy_engine_arn
            gateway_role.add_to_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:GetPolicyEngine", "bedrock-agentcore:ListPolicies", "bedrock-agentcore:GetPolicy"],
                resources=[engine_arn],
            ))
            gateway_role.add_to_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"],
                # Referencing Gateway.GetAtt here would create a dependency
                # cycle. Its unique configured name bounds the generated ID.
                resources=[engine_arn, f"arn:{self.partition}:bedrock-agentcore:{self.region}:{self.account}:gateway/{self.gateway_name}-*"],
            ))

        self.gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name=self.gateway_name,
            protocol_type="MCP",
            authorizer_type="CUSTOM_JWT",
            role_arn=gateway_role.role_arn,
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(**authorizer)
            ),
            protocol_configuration=agentcore.CfnGateway.GatewayProtocolConfigurationProperty(
                mcp=agentcore.CfnGateway.MCPGatewayConfigurationProperty(search_type="SEMANTIC")
            ),
            interceptor_configurations=[
                agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["REQUEST"],
                    interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=interceptor_fn.function_arn
                        )
                    ),
                    input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(
                        # Without this the Gateway strips request headers, and the interceptor
                        # can't read the actor ID header or the token it checks it against.
                        pass_request_headers=True
                    ),
                )
            ],
            policy_engine_configuration=(
                agentcore.CfnGateway.GatewayPolicyEngineConfigurationProperty(
                    arn=self.policy_engine.attr_policy_engine_arn,
                    mode=config.policy.mode,
                )
                if self.policy_engine is not None
                else None
            ),
        )

        if config.gateway.saved_items_sample:
            target = self._create_saved_items_target(config, gateway_role)
            if self.policy_engine is not None:
                self._create_policies(config, target, SAVED_ITEMS_ACTION)
        for target_config in config.gateway.targets:
            self._create_customer_target(config, target_config, gateway_role)
        self.gateway.node.add_dependency(gateway_role)

        # Written here, read by the runtime container at startup — lets the
        # Gateway be redeployed without redeploying the agent.
        ssm.StringParameter(
            self,
            "GatewayUrlParameter",
            parameter_name=f"{config.gateway_ssm_prefix}/unified",
            string_value=self.gateway.attr_gateway_url,
            description="AgentCore Gateway MCP endpoint",
        )

        self._suppress_nag()

        cdk.CfnOutput(self, "GatewayUrl", value=self.gateway.attr_gateway_url)
        cdk.CfnOutput(self, "GatewayId", value=self.gateway.attr_gateway_identifier)

    @staticmethod
    def _authorizer(config: Config, identity: Identity, agent_client_id: str) -> dict:
        """Accept only the agent's own token, never a shopper's.

        An on-behalf-of token is bound to the tools resource server (its `aud`); a
        shopper's access token has no `aud`, so it is rejected. A client-credentials
        token has no `aud` either, so for M2M the agent's client ID is checked instead.
        """
        token = config.gateway_token
        discovery_url = identity.authorizer()["discovery_url"]
        scopes = token["scopes"].split()
        if token["flow"] == "M2M":
            if not agent_client_id:
                raise ValueError("The M2M Gateway authorizer needs the agent's client ID")
            return dict(discovery_url=discovery_url, allowed_clients=[agent_client_id], allowed_scopes=scopes)
        return dict(discovery_url=discovery_url, allowed_audience=[token["audience"]], allowed_scopes=scopes)

    # ── IAM ─────────────────────────────────────────────────────

    def _create_gateway_role(self) -> iam.Role:
        # Only this stage's Gateway may assume the role, not every Gateway in the account.
        return iam.Role(
            self,
            "GatewayExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": f"arn:{self.partition}:bedrock-agentcore:{self.region}:{self.account}:gateway/{self.gateway_name}-*"},
                },
            ),
        )

    # ── Lambda targets ──────────────────────────────────────────

    def _create_saved_items_target(self, config: Config, gateway_role: iam.Role) -> agentcore.CfnGatewayTarget:
        """The sample Lambda target: the shopper's saved items, scoped by actor ID."""
        ns = self.node.try_get_context("ns")
        function = lambda_.Function(
            self,
            "SavedItemsFunction",
            function_name=f"{ns.lower()}-saved-items",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            tracing=lambda_.Tracing.ACTIVE,
            reserved_concurrent_executions=10,
            handler="handler.lambda_handler",
            # The asset hash covers only the handler and the catalog it reads, so unrelated
            # package changes never redeploy the function.
            code=lambda_.Code.from_asset(
                str(_PACKAGES),
                ignore_mode=cdk.IgnoreMode.GIT,
                exclude=[
                    "*",
                    "!mcp-servers/", "!mcp-servers/saved-items-lambda/", "!mcp-servers/saved-items-lambda/src/",
                    "!mcp-servers/saved-items-lambda/src/**",
                    "!catalog/", "!catalog/src/", "!catalog/src/aria_catalog/", "!catalog/src/aria_catalog/**",
                    "__pycache__/", "*.pyc",
                ],
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    command=["bash", "-c", "cp -r /asset-input/mcp-servers/saved-items-lambda/src/. /asset-output/ && "
                                           "cp -r /asset-input/catalog/src/aria_catalog /asset-output/"],
                    local=_CopySavedItemsSources(),
                    platform="linux/arm64",
                ),
            ),
            timeout=cdk.Duration.seconds(10),
            memory_size=256,
            environment={"LOG_LEVEL": "INFO"},
            log_group=logs.LogGroup(
                self,
                "SavedItemsLogGroup",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=cdk.RemovalPolicy.RETAIN if config.is_prod else cdk.RemovalPolicy.DESTROY,
            ),
        )
        # A stable construct ID avoids hashing an unresolved principal token,
        # which otherwise changes permission logical IDs between synth runs.
        function.add_permission("InvokeFromGatewayRole", principal=iam.ArnPrincipal(gateway_role.role_arn))
        self.saved_items_function = function
        self.lambda_function_names["SavedItems"] = f"{ns.lower()}-saved-items"
        tool = agentcore.CfnGatewayTarget.ToolDefinitionProperty(
            name="get_saved_items",
            description="The shopper's saved items (wishlist). Use it when the shopper asks about items they saved.",
            input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                type="object",
                properties={
                    "limit": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                        type="integer", description="How many saved items to return (default 5, max 20)."
                    ),
                },
                required=[],
            ),
        )
        return self._create_target(SAVED_ITEMS_TARGET, function, [tool], config)

    def _create_interceptor_lambda(self, gateway_role: iam.Role, config: Config, agent_client_id: str) -> lambda_.Function:
        ns = self.node.try_get_context("ns")
        log_group = logs.LogGroup(
            self,
            "InterceptorLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN if config.is_prod else cdk.RemovalPolicy.DESTROY,
        )

        function = lambda_.Function(
            self,
            "GatewayInterceptorFunction",
            function_name=f"{ns.lower()}-gateway-interceptor",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            tracing=lambda_.Tracing.ACTIVE,
            reserved_concurrent_executions=20,
            handler="handler.lambda_handler",
            # Stdlib only — no bundling step needed.
            code=lambda_.Code.from_asset(
                str(_INTERCEPTOR_DIR / "src"), exclude=["__pycache__", "*.pyc"]
            ),
            # Runs on every Gateway request, so keep it small and fast.
            timeout=cdk.Duration.seconds(10),
            memory_size=256,
            environment={
                "LOG_LEVEL": "INFO",
                "FORWARD_HEADERS": ",".join(config.gateway.forward_headers),
                # The on-behalf-of check derives the actor ID exactly as the agent does.
                "OIDC_TENANT_CLAIM": config.auth.tenant_claim,
                # on_behalf_of or m2m: which token the agent presents, so the interceptor knows what
                # to check. It denies every tool call if either of these is empty.
                "GATEWAY_AUTH_FLOW": config.gateway.agent_auth.flow,
                "AGENT_CLIENT_ID": agent_client_id,
            },
            log_group=log_group,
        )
        function.add_permission(
            "InvokeFromGatewayRole", principal=iam.ArnPrincipal(gateway_role.role_arn)
        )
        # Counted here, next to the log group, so the Operations stack alarms on the metric by
        # name and needs no export from this stack.
        logs.MetricFilter(
            self,
            "InterceptorDenials",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.literal(f'"{INTERCEPTOR_DENIAL_LOG}"'),
            metric_namespace=metric_namespace(config.ns),
            metric_name=INTERCEPTOR_DENIALS_METRIC,
            metric_value="1",
            default_value=0,
        )
        return function

    # ── Gateway targets ─────────────────────────────────────────

    def _request_headers(self, config: Config) -> agentcore.CfnGatewayTarget.MetadataConfigurationProperty:
        """Headers the Gateway may pass to a target: the actor ID, the trace's ID and the forwarded context."""
        headers = [ACTOR_HEADER, CORRELATION_HEADER, *config.gateway.forward_headers]
        if len(headers) > 10:
            raise ValueError("A Gateway target allows at most 10 request headers, x-aria-actor-id and "
                             "x-correlation-id included, so gateway.forwardHeaders can name at most 8")
        return agentcore.CfnGatewayTarget.MetadataConfigurationProperty(allowed_request_headers=headers)

    def _create_target(
        self,
        name: str,
        function: lambda_.IFunction,
        tools: list,
        config: Config,
    ) -> agentcore.CfnGatewayTarget:
        target = agentcore.CfnGatewayTarget(
            self,
            f"{name}-target",
            gateway_identifier=self.gateway.attr_gateway_identifier,
            name=name,
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=function.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(inline_payload=tools),
                    )
                )
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE"
                )
            ],
            metadata_configuration=self._request_headers(config),
        )
        target.add_resource_dependency(self.gateway)
        return target

    def _outbound_credential(self, spec, role: iam.Role, *, bearer_header: bool = False):
        """The OAuth or API-key credential the Gateway presents to a customer target."""
        T = agentcore.CfnGatewayTarget
        self._grant_outbound_credential(spec, role)
        if spec.credential_type == "OAUTH":
            return T.CredentialProviderProperty(oauth_credential_provider=T.OAuthCredentialProviderProperty(
                provider_arn=spec.provider_arn, scopes=spec.scopes, grant_type=spec.grant_type))
        if bearer_header:
            # MCP servers take the key as a bearer token.
            key = T.ApiKeyCredentialProviderProperty(provider_arn=spec.provider_arn, credential_location="HEADER",
                                                     credential_parameter_name="Authorization", credential_prefix="Bearer ")
        else:
            key = T.ApiKeyCredentialProviderProperty(provider_arn=spec.provider_arn, credential_location=spec.api_key_location,
                                                     credential_parameter_name=spec.api_key_name)
        return T.CredentialProviderProperty(api_key_credential_provider=key)

    def _grant_outbound_credential(self, spec, role: iam.Role) -> None:
        """What AgentCore needs to fetch a target's OAuth token or API key for the Gateway.

        The Gateway first gets a workload token for its own workload identity, then asks the
        provider for the credential, whose secret AgentCore keeps in Secrets Manager. All three
        are scoped: this Gateway's identity, this one provider, and that provider's secret.
        """
        oauth = spec.credential_type == "OAUTH"
        _, partition, _, region, account, _ = spec.provider_arn.split(":", 5)
        name = spec.provider_arn.rsplit("/", 1)[-1]
        vault = spec.provider_arn.rsplit("/", 2)[0]
        directory = f"arn:{self.partition}:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default"
        identity = f"{directory}/workload-identity/{self.gateway_name}-*"
        # AgentCore authorizes the credential call against the Gateway's workload identity as well
        # as the provider (verified on Dev: granting the provider alone is denied).
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:GetResourceOauth2Token" if oauth else "bedrock-agentcore:GetResourceApiKey"],
            resources=[spec.provider_arn, vault, directory, identity]))
        # AgentCore names the secret <kind>/<provider>-<suffix>; Secrets Manager adds its own suffix.
        role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:{partition}:secretsmanager:{region}:{account}:secret:bedrock-agentcore-identity!default/{'oauth2' if oauth else 'apikey'}/{name}-*"]))
        if not getattr(self, "_workload_token_granted", False):
            role.add_to_policy(iam.PolicyStatement(actions=["bedrock-agentcore:GetWorkloadAccessToken"], resources=[directory, identity]))
            self._workload_token_granted = True

    def _create_customer_target(self, config, spec, role):
        T = agentcore.CfnGatewayTarget
        provider = None
        if spec.kind == "lambda":
            def schema(value):
                if set(value) - {"type", "description", "required", "properties", "items"}:
                    raise ValueError("Unsupported inline Gateway schema keyword; enforce additional constraints in the target")
                return T.SchemaDefinitionProperty(
                    type=value["type"], description=value.get("description"), required=value.get("required"),
                    properties={k: schema(v) for k, v in value.get("properties", {}).items()} or None,
                    items=schema(value["items"]) if "items" in value else None,
                )
            tools = [T.ToolDefinitionProperty(name=t["name"], description=t.get("description", "Customer tool"), input_schema=schema(t["inputSchema"])) for t in spec.tool_schema]
            role.add_to_policy(iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[spec.lambda_arn]))
            target_config = T.McpTargetConfigurationProperty(lambda_=T.McpLambdaTargetConfigurationProperty(lambda_arn=spec.lambda_arn, tool_schema=T.ToolSchemaProperty(inline_payload=tools)))
        elif spec.kind == "mcp":
            target_config = T.McpTargetConfigurationProperty(mcp_server=T.McpServerTargetConfigurationProperty(endpoint=spec.endpoint))
            provider = self._outbound_credential(spec, role, bearer_header=True)
        elif spec.kind == "openapi":
            if spec.schema_file:
                document = T.ApiSchemaConfigurationProperty(inline_payload=spec.openapi_document())
            else:
                document = T.ApiSchemaConfigurationProperty(s3=T.S3ConfigurationProperty(uri=spec.schema_s3_uri))
                bucket, _, key = spec.schema_s3_uri.removeprefix("s3://").partition("/")
                role.add_to_policy(iam.PolicyStatement(actions=["s3:GetObject"], resources=[f"arn:{self.partition}:s3:::{bucket}/{key}"]))
            target_config = T.McpTargetConfigurationProperty(open_api_schema=document)
            provider = self._outbound_credential(spec, role)
        else:  # apigateway
            target_config = T.McpTargetConfigurationProperty(api_gateway=T.ApiGatewayTargetConfigurationProperty(
                rest_api_id=spec.rest_api_id,
                stage=spec.stage,
                api_gateway_tool_configuration=T.ApiGatewayToolConfigurationProperty(
                    tool_filters=[T.ApiGatewayToolFilterProperty(filter_path=op.path, methods=[op.method]) for op in spec.operations],
                    # Fixed names keep tool names, allowedTools and Cedar actions in step.
                    tool_overrides=[T.ApiGatewayToolOverrideProperty(name=op.name, path=op.path, method=op.method, description=op.description or None)
                                    for op in spec.operations],
                ),
            ))
            if spec.credential_type == "GATEWAY_IAM_ROLE":
                role.add_to_policy(iam.PolicyStatement(
                    actions=["execute-api:Invoke"],
                    resources=[f"arn:{self.partition}:execute-api:{self.region}:{self.account}:{spec.rest_api_id}/{spec.stage}/*"],
                ))
            else:
                provider = self._outbound_credential(spec, role)
        target = T(
            self, f"{spec.name}-target", name=spec.name, gateway_identifier=self.gateway.attr_gateway_identifier,
            target_configuration=T.TargetConfigurationProperty(mcp=target_config),
            credential_provider_configurations=[T.CredentialProviderConfigurationProperty(credential_provider_type=spec.credential_type, credential_provider=provider)],
            metadata_configuration=self._request_headers(config),
        )
        target.add_resource_dependency(self.gateway)
        if self.policy_engine is not None and spec.policy_file:
            source = spec.policy_document()
            for name in spec.allowed_tools:
                action = f"{spec.name}___{name}"
                policy_name = action.replace("-", "_")
                statement = source.replace("{{GATEWAY_ARN}}", self.gateway.attr_gateway_arn).replace("{{TOOL}}", action)
                policy = build_policy(self, PolicySpec(f"Policy{action}", f"allow_{policy_name}", statement, "Customer-supplied tool authorization"), self.policy_engine.attr_policy_engine_id, "ACTIVE")
                policy.add_resource_dependency(target)

    # ── Policy ──────────────────────────────────────────────────

    def _create_policies(self, config: Config, target, action: str) -> None:
        """Deploy the sample's components/policies/sample/*.cedar files into the engine for its one tool action."""
        assert self.policy_engine is not None

        specs = load_policies(
            gateway_arn=self.gateway.attr_gateway_arn,
            tool_name=action,
        )
        if not specs:
            raise ValueError(
                "policy.enabled is true but components/policies/sample/ contains no .cedar files. "
                "Cedar is default-deny, so deploying an empty engine in ENFORCE mode "
                "would block every tool call."
            )

        self.policies = []
        for spec in specs:
            policy = build_policy(
                self,
                spec,
                policy_engine_id=self.policy_engine.attr_policy_engine_id,
                # Shadow the complete intended policy set at engine level.
                # Per-policy LOG_ONLY excludes grants from the active set.
                enforcement_mode="ACTIVE",
            )
            # Cedar validates against the Gateway's tool schema, so the target
            # must exist before the policy is created.
            policy.add_resource_dependency(target)
            policy.add_resource_dependency(self.policy_engine)
            if self.policies:
                # Strict validation evaluates the existing policy set. Create
                # the permit before a forbid, which alone is deny-all.
                policy.add_resource_dependency(self.policies[-1])
            self.policies.append(policy)

        cdk.CfnOutput(self, "PolicyEngineId", value=self.policy_engine.attr_policy_engine_id)
        cdk.CfnOutput(self, "PolicyMode", value=config.policy.mode)
        cdk.CfnOutput(self, "PolicyCount", value=str(len(specs)))

    def _suppress_nag(self) -> None:
        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Lambda roles need generated log streams and X-Ray. Cedar authorization is scoped to one engine and the configured Gateway name prefix to avoid a dependency cycle. Outbound credentials name the Gateway's workload identity and each provider's secret by prefix, because AgentCore generates their suffixes.",
                },
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "The interceptor and saved-items functions use the AWS-managed AWSLambdaBasicExecutionRole, which only writes their logs; neither runs in a VPC",
                    "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": "The interceptor and saved-items functions pin PYTHON_3_13, the version their packages test on (.python-version), rather than the newest runtime",
                },
            ],
        )
