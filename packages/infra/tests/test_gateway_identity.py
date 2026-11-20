"""The Gateway accepts only the agent's own token, never the shopper's JWT."""

from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from app import build_app
from config.loader import Config, load_config

DEV = Path(__file__).parents[1] / "config/dev.toml"


def raw(**gateway) -> dict:
    data = load_config(DEV).model_dump(by_alias=True)
    data["gateway"].update(gateway)
    return data


def templates(data: dict) -> dict[str, dict]:
    app = build_app(Config.model_validate(data), offline=True)
    app.synth()
    return {s.stack_name: Template.from_stack(s).to_json() for s in app.node.children if isinstance(s, cdk.Stack)}


def resource(template: dict, kind: str) -> dict:
    return next(r["Properties"] for r in template["Resources"].values() if r["Type"] == kind)


@pytest.fixture(scope="module")
def on_behalf_of():
    return templates(raw())


@pytest.fixture(scope="module")
def m2m():
    return templates(raw(agentAuth={"flow": "m2m"}))


def byo_idp(**agent_auth) -> dict:
    """The Dev template with a BYO IdP: the shopper's tokens carry aud "aria-api" from client "shop"."""
    data = raw(agentAuth={"flow": "on_behalf_of", "credentialProvider": "shop-gateway", "clientId": "shop-agent",
                          "audience": "shop-tools", "scopes": ["shop-tools/invoke"], **agent_auth})
    data["auth"].update(provider="oidc", issuer="https://identity.example", clientId="shop", audiences=["aria-api"])
    return data


def interceptor_env(stacks: dict) -> dict:
    gateway = next(body for name, body in stacks.items() if name.endswith("Gateway"))
    functions = [r["Properties"] for r in gateway["Resources"].values() if r["Type"] == "AWS::Lambda::Function"]
    return next(f for f in functions if f.get("FunctionName", "").endswith("-gateway-interceptor"))["Environment"]["Variables"]


class TestGatewayAuthorizer:
    def test_on_behalf_of_accepts_only_tokens_bound_to_the_tools_audience(self, on_behalf_of):
        jwt = resource(on_behalf_of["ARIADevGateway"], "AWS::BedrockAgentCore::Gateway")["AuthorizerConfiguration"]["CustomJWTAuthorizer"]
        # A shopper's access token has no aud, so it can't get through.
        assert jwt["AllowedAudience"] == ["aria-tools"]
        assert jwt["AllowedScopes"] == ["aria-tools/invoke"]
        assert "AllowedClients" not in jwt

    def test_m2m_accepts_only_the_agent_client(self, m2m):
        jwt = resource(m2m["ARIADevGateway"], "AWS::BedrockAgentCore::Gateway")["AuthorizerConfiguration"]["CustomJWTAuthorizer"]
        assert "AgentClient" in str(jwt["AllowedClients"])
        assert "WebClient" not in str(jwt)
        assert "AllowedAudience" not in jwt


class TestRuntime:
    def test_is_told_how_to_mint_its_gateway_token(self, on_behalf_of):
        env = resource(on_behalf_of["ARIADevAgentRuntime"], "AWS::BedrockAgentCore::Runtime")["EnvironmentVariables"]
        assert env["GATEWAY_AUTH_FLOW"] == "ON_BEHALF_OF_TOKEN_EXCHANGE"
        assert env["GATEWAY_CREDENTIAL_PROVIDER"] == "ariadev-gateway"
        assert env["GATEWAY_TOKEN_SCOPES"] == "aria-tools/invoke"
        assert env["GATEWAY_TOKEN_AUDIENCE"] == "aria-tools"

    def test_m2m_sends_no_audience(self, m2m):
        env = resource(m2m["ARIADevAgentRuntime"], "AWS::BedrockAgentCore::Runtime")["EnvironmentVariables"]
        assert (env["GATEWAY_AUTH_FLOW"], env["GATEWAY_TOKEN_AUDIENCE"]) == ("M2M", "")

    def test_may_mint_only_from_its_own_credential_provider(self, on_behalf_of):
        statements = [st for r in on_behalf_of["ARIADevAgentRuntime"]["Resources"].values() if r["Type"] == "AWS::IAM::Policy"
                      for st in r["Properties"]["PolicyDocument"]["Statement"]]
        mint = next(st for st in statements if st["Action"] == "bedrock-agentcore:GetResourceOauth2Token")
        assert any("oauth2credentialprovider/ariadev-gateway" in str(r) for r in mint["Resource"])
        assert not any(str(r).endswith("oauth2credentialprovider/*") for r in mint["Resource"])
        # The provider's secret by its generated suffix, never a provider whose name merely starts the same.
        (secret,) = [st for st in statements if st["Action"] == "secretsmanager:GetSecretValue"]
        assert str(secret["Resource"]).endswith("bedrock-agentcore-identity!default/oauth2/ariadev-gateway-*']]}")

    def test_no_identity_grants_without_gateway_tools(self):
        data = raw(savedItemsSample=False)
        runtime = templates(data)["ARIADevAgentRuntime"]
        assert "GetResourceOauth2Token" not in str(runtime)


def test_interceptor_gets_the_tenant_claim(on_behalf_of):
    variables = interceptor_env(on_behalf_of)
    assert "OIDC_TENANT_CLAIM" in variables


class TestInterceptor:
    """It denies every tool call when GATEWAY_AUTH_FLOW or AGENT_CLIENT_ID is missing."""

    def test_on_behalf_of_names_the_managed_agent_client(self, on_behalf_of):
        env = interceptor_env(on_behalf_of)
        assert env["GATEWAY_AUTH_FLOW"] == "on_behalf_of"
        assert "AgentClient" in str(env["AGENT_CLIENT_ID"]) and "WebClient" not in str(env["AGENT_CLIENT_ID"])

    def test_m2m_expects_the_client_the_authorizer_accepts(self, m2m):
        env = interceptor_env(m2m)
        jwt = resource(m2m["ARIADevGateway"], "AWS::BedrockAgentCore::Gateway")["AuthorizerConfiguration"]["CustomJWTAuthorizer"]
        assert env["GATEWAY_AUTH_FLOW"] == "m2m"
        assert jwt["AllowedClients"] == [env["AGENT_CLIENT_ID"]]

    def test_byo_idp_names_the_configured_agent_client(self):
        env = interceptor_env(templates(byo_idp()))
        assert (env["GATEWAY_AUTH_FLOW"], env["AGENT_CLIENT_ID"]) == ("on_behalf_of", "shop-agent")


def test_only_this_stages_gateway_may_assume_its_role(on_behalf_of):
    roles = [r["Properties"] for r in on_behalf_of["ARIADevGateway"]["Resources"].values() if r["Type"] == "AWS::IAM::Role"
             and "bedrock-agentcore.amazonaws.com" in str(r["Properties"]["AssumeRolePolicyDocument"])]
    (role,) = roles
    (statement,) = role["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Condition"]["StringEquals"] == {"aws:SourceAccount": "123456789012"}
    assert str(statement["Condition"]["ArnLike"]["aws:SourceArn"]).endswith(":gateway/ariadev-tools-*']]}")


class TestConfig:
    def test_byo_idp_must_name_its_credential_provider(self):
        data = raw()
        data["auth"].update(provider="oidc", issuer="https://identity.example", clientId="shop", audiences=["aria-api"])
        with pytest.raises(ValueError, match="agentAuth.credentialProvider"):
            Config.model_validate(data)

    def test_byo_idp_m2m_needs_the_agent_client(self):
        data = raw(agentAuth={"flow": "m2m", "credentialProvider": "shop-gateway", "scopes": ["shop/tools"]})
        data["auth"].update(provider="oidc", issuer="https://identity.example", clientId="shop", audiences=["aria-api"])
        with pytest.raises(ValueError, match="clientId"):
            Config.model_validate(data)

    def test_byo_idp_on_behalf_of_needs_the_agent_client_too(self):
        # The interceptor checks every token was issued to the agent's client, in both flows.
        with pytest.raises(ValueError, match="agentAuth.clientId"):
            Config.model_validate(byo_idp(clientId=""))

    def test_rejects_a_gateway_audience_the_shoppers_tokens_carry(self):
        with pytest.raises(ValueError, match="must not be one of auth.audiences"):
            Config.model_validate(byo_idp(audience="aria-api"))

    def test_rejects_an_m2m_agent_client_that_is_a_shopper_client(self):
        with pytest.raises(ValueError, match="must not be a shopper client"):
            Config.model_validate(byo_idp(flow="m2m", clientId="shop", audience=""))

    def test_accepts_a_distinct_audience_and_client(self):
        assert Config.model_validate(byo_idp()).gateway.agent_auth.client_id == "shop-agent"
