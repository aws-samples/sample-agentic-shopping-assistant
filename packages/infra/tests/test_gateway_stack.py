"""Gateway stack synthesis assertions.

Bundling is skipped via the bundling-stacks context, so the Lambda assets are not
built here. Product search is a local tool in the agent; its catalog grants are
tested in test_runtime_search.py.
"""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from config.loader import load_config
from pathlib import Path
from stacks.auth_stack import AuthStack
from stacks.gateway_stack import GatewayStack

ACCOUNT = "123456789012"
REGION = "us-east-1"


def _template():
    config = load_config(Path(__file__).parents[1] / "config/dev.toml").model_copy(deep=True)
    app = cdk.App(
        context={
            "ns": config.ns,
            "stage": config.app.stage,
            "isProd": config.is_prod,
            "aws:cdk:bundling-stacks": [],
        }
    )
    env = cdk.Environment(account=ACCOUNT, region=REGION)

    auth = AuthStack(app, "Auth", env=env)
    gateway = GatewayStack(
        app,
        "Gateway",
        config=config,
        user_pool=auth.user_pool,
        user_pool_client=auth.user_pool_client,
        discovery_url=auth.discovery_url,
        env=env,
    )
    return Template.from_stack(gateway)


@pytest.fixture(scope="module")
def template():
    return _template()


class TestGateway:
    def test_permission_ids_do_not_depend_on_token_allocation(self, template):
        first = template.find_resources("AWS::Lambda::Permission")
        second = _template().find_resources("AWS::Lambda::Permission")
        assert len(first) == 2
        assert first == second

    def test_creates_gateway_and_one_target(self, template):
        template.resource_count_is("AWS::BedrockAgentCore::Gateway", 1)
        template.resource_count_is("AWS::BedrockAgentCore::GatewayTarget", 1)

    def test_uses_custom_jwt_and_mcp(self, template):
        template.has_resource_properties(
            "AWS::BedrockAgentCore::Gateway",
            {"AuthorizerType": "CUSTOM_JWT", "ProtocolType": "MCP"},
        )

    def test_interceptor_receives_request_headers(self, template):
        # Without PassRequestHeaders the Gateway strips request headers, and the interceptor
        # can't read the actor ID header or the token it checks it against.
        template.has_resource_properties(
            "AWS::BedrockAgentCore::Gateway",
            {
                "InterceptorConfigurations": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "InterceptionPoints": ["REQUEST"],
                                "InputConfiguration": {"PassRequestHeaders": True},
                            }
                        )
                    ]
                )
            },
        )

    def test_publishes_gateway_url_to_ssm(self, template):
        # The agent container resolves this at startup.
        template.has_resource_properties(
            "AWS::SSM::Parameter", {"Name": "/ariadev/mcp/endpoints/gateway/unified"}
        )


class TestSavedItemsSample:
    """The sample target that exercises the Gateway path: the shopper's saved items, scoped by actor ID."""

    def test_runs_outside_the_vpc_on_arm64(self, template):
        functions = template.find_resources("AWS::Lambda::Function", {"Properties": {"FunctionName": "ariadev-saved-items"}})
        (function,) = functions.values()
        assert function["Properties"]["Architectures"] == ["arm64"]
        assert "VpcConfig" not in function["Properties"]

    def test_advertises_get_saved_items(self, template):
        template.has_resource_properties(
            "AWS::BedrockAgentCore::GatewayTarget",
            {
                "Name": "saved-items",
                "TargetConfiguration": Match.object_like({
                    "Mcp": Match.object_like({
                        "Lambda": Match.object_like({
                            "ToolSchema": {"InlinePayload": [Match.object_like({"Name": "get_saved_items"})]}
                        })
                    })
                }),
            },
        )


class TestInterceptorLambda:
    def test_exists_with_short_timeout(self, template):
        # Runs on every Gateway request, so it must stay fast.
        template.has_resource_properties(
            "AWS::Lambda::Function",
            Match.object_like(
                {
                    "FunctionName": Match.string_like_regexp(".*gateway-interceptor"),
                    "Timeout": 10,
                }
            ),
        )
