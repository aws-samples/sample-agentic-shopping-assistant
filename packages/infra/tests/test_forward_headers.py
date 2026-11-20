"""Custom header propagation wiring: config -> Runtime allowlist + interceptor.

A custom header only reaches a tool if every hop is configured, so assert the
two the operator can get wrong: the Runtime must allowlist the header (or it is
stripped before the agent sees it) and the interceptor must be told to forward
it.
"""

from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from app import build_app
from config.loader import Config, load_config

_DEV = Path(__file__).parents[1] / "config/dev.toml"


def _config(forward_headers) -> Config:
    data = load_config(_DEV).model_dump(by_alias=True)
    data["gateway"]["forwardHeaders"] = forward_headers
    return Config.model_validate(data)


def _synth(config: Config) -> dict:
    app = build_app(config, offline=True)
    app.synth()
    by_type: dict[str, list[dict]] = {}
    for stack in app.node.children:
        if isinstance(stack, cdk.Stack):
            for resource in Template.from_stack(stack).to_json()["Resources"].values():
                by_type.setdefault(resource["Type"], []).append(resource["Properties"])
    return by_type


def test_runtime_allowlists_custom_headers_alongside_authorization():
    runtime = _synth(_config(["x-tenant-id", "x-locale"]))["AWS::BedrockAgentCore::Runtime"][0]
    allowlist = runtime["RequestHeaderConfiguration"]["RequestHeaderAllowlist"]
    assert allowlist == ["Authorization", "x-tenant-id", "x-locale"]
    assert runtime["EnvironmentVariables"]["FORWARD_HEADERS"] == "x-tenant-id,x-locale"


def test_interceptor_is_told_which_headers_to_forward():
    resources = _synth(_config(["x-tenant-id", "x-locale"]))
    interceptor_envs = [
        fn["Environment"]["Variables"]
        for fn in resources["AWS::Lambda::Function"]
        if "FORWARD_HEADERS" in fn.get("Environment", {}).get("Variables", {})
    ]
    assert interceptor_envs, "no Lambda received FORWARD_HEADERS"
    assert all(env["FORWARD_HEADERS"] == "x-tenant-id,x-locale" for env in interceptor_envs)


def test_empty_by_default_leaves_only_authorization():
    runtime = _synth(_config([]))["AWS::BedrockAgentCore::Runtime"][0]
    assert runtime["RequestHeaderConfiguration"]["RequestHeaderAllowlist"] == ["Authorization"]
    assert runtime["EnvironmentVariables"]["FORWARD_HEADERS"] == ""


def test_rejects_authorization_in_forward_headers():
    with pytest.raises(ValueError, match="Do not propagate Authorization"):
        _config(["x-tenant-id", "Authorization"])


@pytest.mark.parametrize("name", ["x-correlation-id", "X-Correlation-Id"])
def test_rejects_the_correlation_header_the_interceptor_sets(name):
    with pytest.raises(ValueError, match=f"gateway.forwardHeaders can't name {name}: the interceptor sets it"):
        _config(["x-tenant-id", name])


@pytest.mark.parametrize("name", ["x-aria-actor-id", "X-Aria-Actor-Id", "x-aria-tenant", "X-ARIA-LOCALE"])
def test_rejects_the_agents_own_headers_in_any_casing(name):
    # A forwarded header reaches local tools' invocation_state with the value the shopper sent.
    with pytest.raises(ValueError, match=f"gateway.forwardHeaders can't name {name}:"):
        _config(["x-tenant-id", name])
