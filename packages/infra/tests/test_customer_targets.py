"""Customer APIs onboarded as Gateway targets: OpenAPI and API Gateway, beside Lambda and MCP."""

import json
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from app import build_app
from config.loader import Config, load_config

DEV = Path(__file__).parents[1] / "config/dev.toml"
PROVIDER = "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:token-vault/default/oauth2credentialprovider/orders"
KEY_PROVIDER = "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:token-vault/default/apikeycredentialprovider/stores"

OPENAPI = dict(name="orders", kind="openapi", schemaFile="examples/orders-openapi.json", allowedTools=["getOrderStatus"],
               credentialType="OAUTH", providerArn=PROVIDER, scopes=["orders/read"])
APIGATEWAY = dict(name="stores", kind="apigateway", restApiId="a1b2c3d4e5", stage="prod",
                  operations=[{"name": "find_store", "path": "/stores", "method": "GET", "description": "Nearest stores"}],
                  allowedTools=["find_store"])


def config(*targets, forward=None) -> Config:
    data = load_config(DEV).model_dump(by_alias=True)
    data["gateway"]["targets"] = list(targets)
    if forward is not None:
        data["gateway"]["forwardHeaders"] = forward
    return Config.model_validate(data)


def gateway(*targets) -> dict:
    app = build_app(config(*targets), offline=True)
    app.synth()
    stack = next(s for s in app.node.children if isinstance(s, cdk.Stack) and s.stack_name.endswith("Gateway"))
    return Template.from_stack(stack).to_json()


def target(template: dict, name: str) -> dict:
    return next(r["Properties"] for r in template["Resources"].values()
                if r["Type"] == "AWS::BedrockAgentCore::GatewayTarget" and r["Properties"]["Name"] == name)


def statements(template: dict) -> list[dict]:
    return [st for r in template["Resources"].values() if r["Type"] == "AWS::IAM::Policy" for st in r["Properties"]["PolicyDocument"]["Statement"]]


class TestOpenApi:
    def test_inline_document_with_oauth(self):
        template = gateway(OPENAPI)
        props = target(template, "orders")
        assert '"operationId": "getOrderStatus"' in props["TargetConfiguration"]["Mcp"]["OpenApiSchema"]["InlinePayload"]
        oauth = props["CredentialProviderConfigurations"][0]["CredentialProvider"]["OauthCredentialProvider"]
        assert (oauth["ProviderArn"], oauth["Scopes"], oauth["GrantType"]) == (PROVIDER, ["orders/read"], "CLIENT_CREDENTIALS")
        assert any(st["Action"] == "bedrock-agentcore:GetResourceOauth2Token" and PROVIDER in st["Resource"] for st in statements(template))

    @pytest.mark.parametrize("credentials,secret", [
        ({}, "secret:bedrock-agentcore-identity!default/oauth2/orders-*"),
        ({"credentialType": "API_KEY", "providerArn": KEY_PROVIDER, "apiKeyName": "x-api-key"},
         "secret:bedrock-agentcore-identity!default/apikey/stores-*"),
    ])
    def test_the_gateway_can_fetch_the_credential_and_nothing_more(self, credentials, secret):
        # AgentCore's outbound path: a workload token for the Gateway, then the provider's credential and secret.
        found = statements(gateway({**OPENAPI, **credentials}))
        (read,) = [st for st in found if st["Action"] == "secretsmanager:GetSecretValue"]
        assert read["Resource"] == f"arn:aws:secretsmanager:ap-northeast-2:123456789012:{secret}"
        (fetch,) = [st for st in found if st["Action"] in ("bedrock-agentcore:GetResourceOauth2Token", "bedrock-agentcore:GetResourceApiKey")]
        # The provider, its vault, and the Gateway's own workload identity: AgentCore checks all of them.
        assert fetch["Resource"][:2] == [credentials.get("providerArn", PROVIDER), credentials.get("providerArn", PROVIDER).rsplit("/", 2)[0]]
        assert str(fetch["Resource"][3]).endswith("/workload-identity/ariadev-tools-*']]}")
        (workload,) = [st for st in found if st["Action"] == "bedrock-agentcore:GetWorkloadAccessToken"]
        assert [str(r).endswith(suffix) for r, suffix in zip(workload["Resource"], (":workload-identity-directory/default']]}", "/workload-identity/ariadev-tools-*']]}"))] == [True, True]

    def test_document_from_s3_can_read_only_that_object(self):
        template = gateway({**OPENAPI, "schemaFile": "", "schemaS3Uri": "s3://specs-bucket/orders/openapi.json"})
        assert target(template, "orders")["TargetConfiguration"]["Mcp"]["OpenApiSchema"]["S3"]["Uri"] == "s3://specs-bucket/orders/openapi.json"
        reads = [st for st in statements(template) if st["Action"] == "s3:GetObject"]
        (read,) = reads  # ARNs carry the partition as a reference
        assert str(read["Resource"]).endswith(":s3:::specs-bucket/orders/openapi.json']]}")

    def test_api_key_goes_where_configured(self):
        template = gateway({**OPENAPI, "credentialType": "API_KEY", "providerArn": KEY_PROVIDER, "apiKeyName": "x-api-key"})
        key = target(template, "orders")["CredentialProviderConfigurations"][0]["CredentialProvider"]["ApiKeyCredentialProvider"]
        assert (key["CredentialLocation"], key["CredentialParameterName"]) == ("HEADER", "x-api-key")

    @pytest.mark.parametrize("change,message", [
        ({"schemaS3Uri": "s3://b/k.json"}, "exactly one"),
        ({"schemaFile": ""}, "exactly one"),
        ({"credentialType": "GATEWAY_IAM_ROLE"}, "kind=apigateway"),
        ({"allowedTools": ["deleteEverything"]}, "operationIds"),
        ({"schemaFile": "../../README.md"}, "under packages/infra"),
        ({"credentialType": "API_KEY", "providerArn": KEY_PROVIDER}, "apiKeyName"),
        # Three-legged OAuth needs a consent flow and a return URL that ARIA doesn't have.
        ({"grantType": "AUTHORIZATION_CODE"}, "grantType AUTHORIZATION_CODE needs a consent flow"),
    ])
    def test_rejects_invalid_configs(self, change, message):
        with pytest.raises(ValueError, match=message):
            config({**OPENAPI, **change})

    @pytest.mark.parametrize("name,forward", [("X-Aria-Actor-Id", None), ("x-locale", ["x-locale"])])
    def test_rejects_a_document_that_declares_a_propagated_header(self, monkeypatch, name, forward):
        # A declared header is a tool argument the model fills in, and the Gateway won't propagate it.
        document = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {"/orders": {"get": {
            "operationId": "getOrderStatus", "parameters": [{"$ref": "#/components/parameters/Who"}]}}},
            "components": {"parameters": {"Who": {"name": name, "in": "header", "schema": {"type": "string"}}}}}
        monkeypatch.setattr("config.loader.ToolTargetConfig.openapi_document", lambda self: json.dumps(document))
        with pytest.raises(ValueError, match="Remove them"):
            config(OPENAPI, forward=forward)

    def test_the_example_document_declares_no_propagated_header(self):
        assert not config(OPENAPI).gateway.targets[0].openapi_header_parameters()


class TestApiGateway:
    def test_iam_signed_calls_reach_only_that_stage(self):
        template = gateway(APIGATEWAY)
        api = target(template, "stores")["TargetConfiguration"]["Mcp"]["ApiGateway"]
        assert (api["RestApiId"], api["Stage"]) == ("a1b2c3d4e5", "prod")
        tools = api["ApiGatewayToolConfiguration"]
        assert tools["ToolFilters"] == [{"FilterPath": "/stores", "Methods": ["GET"]}]
        assert tools["ToolOverrides"][0]["Name"] == "find_store"
        invoke = next(st for st in statements(template) if st["Action"] == "execute-api:Invoke")
        assert str(invoke["Resource"]).endswith(":execute-api:ap-northeast-2:123456789012:a1b2c3d4e5/prod/*']]}")

    def test_api_key_alternative(self):
        template = gateway({**APIGATEWAY, "credentialType": "API_KEY", "providerArn": KEY_PROVIDER, "apiKeyName": "x-api-key"})
        assert "execute-api:Invoke" not in str(template)

    @pytest.mark.parametrize("change,message", [
        ({"credentialType": "OAUTH", "providerArn": PROVIDER}, "GATEWAY_IAM_ROLE or API_KEY"),
        ({"operations": []}, "restApiId, stage and operations"),
        ({"allowedTools": ["drop_tables"]}, "operation names"),
    ])
    def test_rejects_invalid_configs(self, change, message):
        with pytest.raises(ValueError, match=message):
            config({**APIGATEWAY, **change})


class TestActorHeader:
    def test_every_target_may_receive_the_actor_header_and_the_trace_id(self):
        template = gateway(OPENAPI, APIGATEWAY)
        for name in ("orders", "stores", "saved-items"):
            assert target(template, name)["MetadataConfiguration"]["AllowedRequestHeaders"][:2] == ["x-aria-actor-id", "x-correlation-id"]

    def test_forwarded_context_headers_are_allowed_too(self):
        app = build_app(config(OPENAPI, forward=["x-locale"]), offline=True)
        app.synth()
        stack = next(s for s in app.node.children if isinstance(s, cdk.Stack) and s.stack_name.endswith("Gateway"))
        assert target(Template.from_stack(stack).to_json(), "orders")["MetadataConfiguration"]["AllowedRequestHeaders"] == [
            "x-aria-actor-id", "x-correlation-id", "x-locale"]

    def test_too_many_headers_are_rejected(self):
        # Ten per target, the actor and trace headers included, leaves eight for forwardHeaders.
        app = build_app(config(OPENAPI, forward=[f"x-h{i}" for i in range(8)]), offline=True)
        app.synth()
        with pytest.raises(ValueError, match="at most 8"):
            app = build_app(config(OPENAPI, forward=[f"x-h{i}" for i in range(9)]), offline=True)
            app.synth()
