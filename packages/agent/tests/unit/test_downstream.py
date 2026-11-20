"""The agent's own Gateway token: minted from the workload token, cached, never the shopper's JWT."""

import base64
import json

import pytest

from core.downstream import DownstreamToken, token_expiry


def jwt(**claims) -> str:
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class Identity:
    def __init__(self, *tokens):
        self.tokens = list(tokens)
        self.requests = []

    def get_resource_oauth2_token(self, **request):
        self.requests.append(request)
        return {"accessToken": self.tokens.pop(0)}


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now


def source(identity, clock, flow="ON_BEHALF_OF_TOKEN_EXCHANGE", audience="aria-tools"):
    return DownstreamToken(provider="ariadev-gateway", flow=flow, scopes=("aria-tools/invoke",), region="us-west-2",
                           audience=audience, client=identity, clock=clock)


def test_mints_with_the_workload_token_and_the_configured_flow():
    identity, clock = Identity(jwt(exp=4_600)), Clock()
    tokens = source(identity, clock)
    tokens.set_workload_token("wat")
    assert tokens.bearer() == f"Bearer {jwt(exp=4_600)}"
    assert identity.requests == [{
        "workloadIdentityToken": "wat",
        "resourceCredentialProviderName": "ariadev-gateway",
        "scopes": ["aria-tools/invoke"],
        "oauth2Flow": "ON_BEHALF_OF_TOKEN_EXCHANGE",
        "audiences": ["aria-tools"],
        # Cognito rejects AgentCore's default subject token type (jwt) with HTTP 400.
        "customParameters": {"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},  # nosec B105 # an RFC 8693 token-type URN, not a credential
    }]


def test_m2m_without_an_audience_sends_none():
    identity = Identity(jwt(exp=4_600))
    tokens = source(identity, Clock(), flow="M2M", audience="")
    tokens.set_workload_token("wat")
    tokens.bearer()
    assert identity.requests[0]["oauth2Flow"] == "M2M"
    # Client credentials have no subject token to label.
    assert "customParameters" not in identity.requests[0]
    assert "audiences" not in identity.requests[0]


def test_reuses_the_token_until_shortly_before_it_expires():
    identity, clock = Identity(jwt(exp=4_600), jwt(exp=8_200)), Clock()
    tokens = source(identity, clock)
    tokens.set_workload_token("wat")
    first = tokens.bearer()
    clock.now = 4_500  # 100 s before expiry, outside the 60 s refresh margin
    assert tokens.bearer() == first and len(identity.requests) == 1
    clock.now = 4_545  # inside the margin: mint again
    assert tokens.bearer() != first and len(identity.requests) == 2


def test_refuses_without_a_workload_token():
    with pytest.raises(PermissionError, match="workload access token"):
        source(Identity(), Clock()).bearer()


def test_a_missing_access_token_fails_closed():
    class NoToken(Identity):
        def get_resource_oauth2_token(self, **request):
            return {"authorizationUrl": "https://consent.example"}

    tokens = source(NoToken(), Clock())
    tokens.set_workload_token("wat")
    with pytest.raises(PermissionError, match="no access token"):
        tokens.bearer()


def test_unsupported_flows_and_missing_settings_are_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        DownstreamToken(provider="p", flow="USER_FEDERATION", scopes=("s",), region="r")
    with pytest.raises(ValueError, match="credential provider"):
        DownstreamToken(provider="", flow="M2M", scopes=("s",), region="r")


def test_opaque_tokens_get_a_short_lifetime():
    assert token_expiry("not-a-jwt", 100.0) == 400.0
    assert token_expiry(jwt(exp=999), 0.0) == 999.0


def test_the_identity_client_has_bounded_timeouts_and_retries(monkeypatch):
    # A mint runs inside a Gateway request; botocore's defaults (60 s reads) would stall it.
    captured = {}

    def client(service, **kwargs):
        captured.update(service=service, **kwargs)
        return Identity(jwt(exp=4_600))

    monkeypatch.setattr("core.downstream.boto3.client", client)
    tokens = DownstreamToken(provider="ariadev-gateway", flow="M2M", scopes=("aria-tools/invoke",), region="us-west-2",
                             clock=Clock())
    tokens.set_workload_token("wat")
    tokens.bearer()
    config = captured["config"]
    assert (captured["service"], captured["region_name"]) == ("bedrock-agentcore", "us-west-2")
    assert (config.connect_timeout, config.read_timeout) == (3, 10)
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
