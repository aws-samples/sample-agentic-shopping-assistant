"""Synthesis assertions for the AgentCore stacks.

Docker bundling is skipped via the bundling-stacks context so these run without
a container runtime; the Gateway stack is covered by test_gateway_stack.py,
test_gateway_identity.py and test_customer_targets.py.
"""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from config.loader import AuthConfig, load_config
from pathlib import Path
from stacks.agent_runtime_stack import (
    FACTS_NAMESPACE,
    PREFERENCES_NAMESPACE,
    SUMMARIES_NAMESPACE,
    AgentRuntimeStack,
)
from stacks.api_service_stack import ApiServiceStack
from stacks.auth_stack import AuthStack
from stacks.common_app_stack import CommonAppStack
from stacks.vpc_stack import VpcStack

ACCOUNT = "123456789012"
REGION = "us-west-2"
RUNTIME_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/r-1"
MEMORY_ID = "mem-abcdefghij"


@pytest.fixture(scope="module")
def synth():
    config = load_config(Path(__file__).parents[1] / "config/dev.toml").model_copy(deep=True)
    config.memory.long_term = True
    app = cdk.App(
        context={
            "ns": config.ns,
            "stage": config.app.stage,
            "isProd": config.is_prod,
            "aws:cdk:bundling-stacks": [],
        }
    )
    env = cdk.Environment(account=ACCOUNT, region=REGION)

    vpc = VpcStack(app, "Vpc", vpc_id=None, env=env)
    auth = AuthStack(app, "Auth", env=env)
    runtime = AgentRuntimeStack(
        app,
        "Runtime",
        config=config,
        vpc=vpc.vpc,
        user_pool=auth.user_pool,
        user_pool_client=auth.user_pool_client,
        discovery_url=auth.discovery_url,
        env=env,
    )
    common = CommonAppStack(
        app, "Common", vpc=vpc.vpc, allow_ip_list=["127.0.0.1/32"], server_port=8000, env=env
    )
    api = ApiServiceStack(
        app,
        "Api",
        config=config,
        cluster=common.cluster,
        load_balancer=common.load_balancer,
        load_balancer_security_group=common.load_balancer_security_group,
        agent_runtime_arn=RUNTIME_ARN,
        memory_id=MEMORY_ID,
        discovery_url=auth.discovery_url,
        user_pool=auth.user_pool,
        user_pool_client=auth.user_pool_client,
        env=env,
    )
    return {
        "runtime": Template.from_stack(runtime),
        "auth": Template.from_stack(auth),
        "api": Template.from_stack(api),
        "vpc": Template.from_stack(vpc),
        "config": config,
    }


class TestRuntime:
    def test_creates_one_runtime_and_one_memory(self, synth):
        synth["runtime"].resource_count_is("AWS::BedrockAgentCore::Runtime", 1)
        synth["runtime"].resource_count_is("AWS::BedrockAgentCore::Memory", 1)

    def test_authorization_header_is_allowlisted(self, synth):
        # The agent reads the shopper's JWT for the actor ID and session ownership; without this the Runtime
        # strips it and every turn fails with owner_mismatch.
        synth["runtime"].has_resource_properties(
            "AWS::BedrockAgentCore::Runtime",
            {"RequestHeaderConfiguration": {"RequestHeaderAllowlist": ["Authorization"]}},
        )

    def test_uses_custom_jwt_authorizer(self, synth):
        synth["runtime"].has_resource_properties(
            "AWS::BedrockAgentCore::Runtime",
            {"AuthorizerConfiguration": {"CustomJWTAuthorizer": Match.any_value()}},
        )

    def test_memory_namespaces_match_agent_code(self, synth):
        # These strings are formatted at runtime by AgentCoreMemorySessionManager;
        # a mismatch silently yields zero retrieved memories.
        synth["runtime"].has_resource_properties(
            "AWS::BedrockAgentCore::Memory",
            {
                "MemoryStrategies": [
                    {"UserPreferenceMemoryStrategy": {"Namespaces": [PREFERENCES_NAMESPACE]}},
                    {"SemanticMemoryStrategy": {"Namespaces": [FACTS_NAMESPACE]}},
                    {"SummaryMemoryStrategy": {"Namespaces": [SUMMARIES_NAMESPACE]}},
                ]
            },
        )

    def test_runtime_depends_on_memory(self, synth):
        runtimes = synth["runtime"].find_resources("AWS::BedrockAgentCore::Runtime")
        assert "Memory" in list(runtimes.values())[0]["DependsOn"]

    def test_gateway_url_comes_from_ssm_prefix(self, synth):
        synth["runtime"].has_resource_properties(
            "AWS::BedrockAgentCore::Runtime",
            {"EnvironmentVariables": Match.object_like({"MCP_SSM_PREFIX": synth["config"].gateway_ssm_prefix})},
        )

    def test_memory_and_its_strategies_are_passed_to_the_container(self, synth):
        env = synth["runtime"].find_resources("AWS::BedrockAgentCore::Runtime")
        (props,) = [r["Properties"]["EnvironmentVariables"] for r in env.values()]
        assert "AGENTCORE_MEMORY_ID" in props
        assert props["AGENTCORE_MEMORY_STRATEGIES"] == "user_preference,semantic,summarization"

    def test_runs_in_the_vpc_with_its_own_security_group(self, synth):
        synth["runtime"].has_resource_properties(
            "AWS::BedrockAgentCore::Runtime", {"NetworkConfiguration": {"NetworkMode": "VPC"}}
        )
        synth["runtime"].has_resource_properties(
            "AWS::EC2::SecurityGroup", {"GroupDescription": "AgentCore Runtime ENIs"}
        )

    def test_lifecycle_configured(self, synth):
        config = synth["config"]
        synth["runtime"].has_resource_properties(
            "AWS::BedrockAgentCore::Runtime",
            {
                "LifecycleConfiguration": {
                    "IdleRuntimeSessionTimeout": config.runtime.idle_session_timeout,
                    "MaxLifetime": config.runtime.max_lifetime,
                }
            },
        )

    def test_guardrail_created_and_wired(self, synth):
        synth["runtime"].resource_count_is("AWS::Bedrock::Guardrail", 1)
        synth["runtime"].resource_count_is("AWS::Bedrock::GuardrailVersion", 1)

    def test_role_can_describe_log_groups_account_wide(self, synth):
        # Missing this permission silently drops all container stdout.
        synth["runtime"].has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {
                                        "Action": "logs:DescribeLogGroups",
                                        "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:*",
                                    }
                                )
                            ]
                        )
                    }
                )
            },
        )

    def test_role_trusts_only_agentcore_in_this_account(self, synth):
        synth["runtime"].has_resource_properties(
            "AWS::IAM::Role",
            {
                "AssumeRolePolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {
                                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                                        "Condition": {
                                            "StringEquals": {"aws:SourceAccount": ACCOUNT},
                                            "ArnLike": Match.any_value(),
                                        },
                                    }
                                )
                            ]
                        )
                    }
                )
            },
        )

    def _statements(self, synth) -> list[dict]:
        return [st for policy in synth["runtime"].find_resources("AWS::IAM::Policy").values()
                for st in policy["Properties"]["PolicyDocument"]["Statement"]]

    def test_memory_grants_only_what_the_session_manager_calls(self, synth):
        # AgentCoreMemorySessionManager (bedrock-agentcore 1.20) writes, reads, redacts and retrieves;
        # listing sessions and deleting records belong to the API's task role.
        (memory,) = [st for st in self._statements(synth) if "bedrock-agentcore:CreateEvent" in st["Action"]]
        assert sorted(memory["Action"]) == sorted([
            "bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents",
            "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:RetrieveMemoryRecords"])

    def test_log_resource_policy_is_limited_to_the_runtimes_log_groups(self, synth):
        (statement,) = [st for st in self._statements(synth) if st["Action"] == "logs:PutResourcePolicy"]
        assert statement["Resource"] != "*"
        assert str(statement["Resource"]).endswith(f":logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/runtimes/ariadev_agent-*']]}}")


class TestAuth:
    def test_creates_user_pool_and_two_clients(self, synth):
        synth["auth"].resource_count_is("AWS::Cognito::UserPool", 1)
        # The shopper's web client, and the agent's own client for its Gateway token.
        synth["auth"].resource_count_is("AWS::Cognito::UserPoolClient", 2)

    def test_web_client_has_no_secret(self, synth):
        # Public SPA client: a secret cannot be kept confidential in a browser.
        synth["auth"].has_resource_properties(
            "AWS::Cognito::UserPoolClient", {"ClientName": "ariadev-web", "GenerateSecret": False}
        )

    def test_web_tokens_carry_the_tools_scope_for_exchange(self, synth):
        # Token exchange only narrows scopes, so the shopper's token must already carry it.
        web = synth["auth"].find_resources("AWS::Cognito::UserPoolClient", {"Properties": {"ClientName": "ariadev-web"}})
        (client,) = web.values()
        # Scopes are rendered as "<resource server ref>/<scope>".
        assert any("ToolScopes" in str(scope) and "/invoke" in str(scope) for scope in client["Properties"]["AllowedOAuthScopes"])

    def test_agent_client_is_confidential_and_may_delegate(self, synth):
        agent = synth["auth"].find_resources("AWS::Cognito::UserPoolClient", {"Properties": {"ClientName": "ariadev-agent"}})
        (client,) = agent.values()
        props = client["Properties"]
        assert props["GenerateSecret"] is True
        assert props["AllowedOAuthFlows"] == ["client_credentials"]
        (scope,) = props["AllowedOAuthScopes"]
        assert "ToolScopes" in str(scope) and "/invoke" in str(scope)
        assert "ALLOW_TOKEN_EXCHANGE_DELEGATION" in props["ExplicitAuthFlows"]

    def test_domain_uses_managed_login(self, synth):
        # Cognito refuses token exchange on a classic hosted-UI domain.
        synth["auth"].has_resource_properties("AWS::Cognito::UserPoolDomain", {"ManagedLoginVersion": 2})
        synth["auth"].resource_count_is("AWS::Cognito::ManagedLoginBranding", 1)

    def test_credential_provider_exchanges_on_behalf_of_the_shopper(self, synth):
        synth["auth"].has_resource_properties(
            "AWS::BedrockAgentCore::OAuth2CredentialProvider",
            {
                "Name": "ariadev-gateway",
                "CredentialProviderVendor": "CustomOauth2",
                "Oauth2ProviderConfigInput": {"CustomOauth2ProviderConfig": Match.object_like({
                    "OnBehalfOfTokenExchangeConfig": {
                        "GrantType": "TOKEN_EXCHANGE",
                        "TokenExchangeGrantTypeConfig": {"ActorTokenContent": "M2M", "ActorTokenScopes": ["aria-tools/invoke"]},
                    },
                })},
            },
        )

    def test_client_secret_is_not_written_into_the_template(self, synth):
        provider = next(iter(synth["auth"].find_resources("AWS::BedrockAgentCore::OAuth2CredentialProvider").values()))
        secret = provider["Properties"]["Oauth2ProviderConfigInput"]["CustomOauth2ProviderConfig"]["ClientSecret"]
        assert isinstance(secret, dict) and "Fn::GetAtt" in secret

    def test_web_client_uses_authorization_code(self, synth):
        synth["auth"].has_resource_properties(
            "AWS::Cognito::UserPoolClient",
            {"ClientName": "ariadev-web", "AllowedOAuthFlows": ["code"], "AllowedOAuthFlowsUserPoolClient": True},
        )

    def test_self_signup_disabled(self, synth):
        synth["auth"].has_resource_properties(
            "AWS::Cognito::UserPool", {"AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}}
        )

    def test_account_recovery_uses_only_the_verified_email(self, synth):
        synth["auth"].has_resource_properties(
            "AWS::Cognito::UserPool",
            {"AccountRecoverySetting": {"RecoveryMechanisms": [{"Name": "verified_email", "Priority": 1}]}},
        )

    def test_login_domain_prefix_defaults_to_namespace_and_account_id(self, synth):
        # A pool has one prefix domain, so changing this default fails the update of every stage deployed with it.
        synth["auth"].has_resource_properties("AWS::Cognito::UserPoolDomain", {"Domain": f"ariadev-{ACCOUNT}"})

    def test_login_domain_prefix_can_leave_out_the_account_id(self):
        app = cdk.App(context={"ns": "ARIADev", "stage": "Dev", "isProd": False})
        auth = AuthStack(app, "Auth", settings=AuthConfig(domainPrefix="nordly-shop"), env=cdk.Environment(account=ACCOUNT, region=REGION))
        Template.from_stack(auth).has_resource_properties("AWS::Cognito::UserPoolDomain", {"Domain": "nordly-shop"})

    @staticmethod
    def _pool(mfa: str | None = None, prod: bool = False) -> dict:
        app = cdk.App(context={"ns": "ARIAProd" if prod else "ARIADev", "stage": "Prod" if prod else "Dev", "isProd": prod})
        settings = AuthConfig(**({"mfa": mfa} if mfa else {}))
        auth = AuthStack(app, "Auth", settings=settings, env=cdk.Environment(account=ACCOUNT, region=REGION))
        (pool,) = Template.from_stack(auth).find_resources("AWS::Cognito::UserPool").values()
        return pool

    @pytest.mark.parametrize("prod", [False, True])
    def test_mfa_is_optional_by_default_in_every_stage(self, prod):
        # Managed login asks no shopper to enrol; one who has registered an authenticator is asked for a code.
        pool = self._pool(prod=prod)
        assert pool["Properties"]["MfaConfiguration"] == "OPTIONAL"
        assert pool["Properties"]["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
        assert "AwsSolutions-COG2" in str(pool["Metadata"])

    @pytest.mark.parametrize("mfa,configuration", [("required", "ON"), ("off", "OFF")])
    def test_mfa_follows_the_setting(self, mfa, configuration):
        pool = self._pool(mfa, prod=True)
        assert pool["Properties"]["MfaConfiguration"] == configuration
        assert pool["Properties"].get("EnabledMfas") == (["SOFTWARE_TOKEN_MFA"] if mfa == "required" else None)
        # Only a pool that requires MFA passes cdk-nag's COG2 without a suppression.
        assert ("AwsSolutions-COG2" in str(pool.get("Metadata", {}))) is (mfa != "required")

    def test_mfa_is_managed_cognito_only(self):
        with pytest.raises(ValueError, match="auth.mfa applies to managed Cognito only"):
            AuthConfig(provider="oidc", issuer="https://idp.example.com", clientId="web", audiences=["aria"], mfa="required")

    @pytest.mark.parametrize("prefix", ["Nordly", "-nordly", "nordly-", "my_shop", "shop-aws-login", "amazon-shop", "cognito"])
    def test_rejects_a_prefix_cognito_would_refuse(self, prefix):
        with pytest.raises(ValueError, match="domainPrefix"):
            AuthConfig(domainPrefix=prefix)


class TestApiService:
    def test_runtime_uses_bearer_without_iam_invocation_permission(self, synth):
        assert "bedrock-agentcore:InvokeAgentRuntime" not in str(synth["api"].to_json())

    def test_receives_runtime_arn_and_memory_id(self, synth):
        synth["api"].has_resource_properties(
            "AWS::ECS::TaskDefinition",
            {
                "ContainerDefinitions": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Environment": Match.array_with(
                                    [
                                        {"Name": "AGENT_RUNTIME_ARN", "Value": RUNTIME_ARN},
                                        {"Name": "AGENTCORE_MEMORY_ID", "Value": MEMORY_ID},
                                    ]
                                )
                            }
                        )
                    ]
                )
            },
        )

    def test_memory_access_scoped_to_one_memory(self, synth):
        body = str(synth["api"].to_json())
        assert f"memory/{MEMORY_ID}" in body
        assert "memory/*" not in body

    def test_runs_in_private_subnets(self, synth):
        synth["api"].has_resource_properties(
            "AWS::ECS::Service",
            {
                "NetworkConfiguration": Match.object_like(
                    {"AwsvpcConfiguration": Match.object_like({"AssignPublicIp": "DISABLED"})}
                )
            },
        )

    def test_health_check_path(self, synth):
        synth["api"].has_resource_properties(
            "AWS::ElasticLoadBalancingV2::TargetGroup", {"HealthCheckPath": "/ping"}
        )

    def test_no_bedrock_invoke_permission(self, synth):
        # The API runs no agent itself (the AgentCore Runtime does), so it must not be able to call models.
        assert "bedrock:InvokeModel" not in str(synth["api"].to_json())


class TestVpc:
    def test_has_endpoints_required_by_agentcore_vpc_mode(self, synth):
        template = synth["vpc"]
        services = [
            props["Properties"].get("ServiceName")
            for props in template.find_resources("AWS::EC2::VPCEndpoint").values()
        ]
        rendered = str(services)
        for required in ("ecr.api", "ecr.dkr", "logs"):
            assert required in rendered, f"missing VPC endpoint for {required}"

    def test_has_s3_gateway_endpoint(self, synth):
        # Free, and required for ECR layer pulls in VPC mode.
        assert "s3" in str(
            [
                props["Properties"].get("ServiceName")
                for props in synth["vpc"].find_resources("AWS::EC2::VPCEndpoint").values()
            ]
        )


class TestCommonApp:
    @staticmethod
    def _security_group(**options) -> dict:
        app = cdk.App(context={"ns": "ARIADev", "stage": "Dev", "isProd": False})
        env = cdk.Environment(account=ACCOUNT, region=REGION)
        vpc = VpcStack(app, "Vpc", vpc_id=None, env=env)
        common = CommonAppStack(app, "Common", vpc=vpc.vpc, server_port=443, env=env, **options)
        groups = Template.from_stack(common).find_resources("AWS::EC2::SecurityGroup")
        return next(group for group in groups.values() if "SecurityGroupIngress" in group["Properties"])

    def test_ipv6_allowlist_entries_become_ipv6_rules(self):
        group = self._security_group(allow_ip_list=["203.0.113.0/24", "2001:db8::/32"])
        rules = group["Properties"]["SecurityGroupIngress"]
        assert any(r.get("CidrIp") == "203.0.113.0/24" for r in rules)
        assert any(r.get("CidrIpv6") == "2001:db8::/32" for r in rules)

    def test_public_ingress_is_called_what_it_is(self):
        group = self._security_group(allow_ip_list=["0.0.0.0/0"], public_ingress=True)
        (reason,) = [rule["reason"] for rule in group["Metadata"]["cdk_nag"]["rules_to_suppress"] if rule["id"] == "AwsSolutions-EC23"]
        assert "internet-wide" in reason and "allowPublicIngress" in reason
