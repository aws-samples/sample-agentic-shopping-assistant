"""Deploy-time gating parameters: Gateway on/off, Memory strategy, Guardrail mode, Runtime limits.

Each toggle threads TOML -> pydantic -> stack, so these assert both the
cross-field validation invariants and the resources that synthesize.
"""

from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from app import build_app
from config.loader import Config, load_config

_DEV = Path(__file__).parents[1] / "config/dev.toml"
_PROD = Path(__file__).parents[1] / "config/prod.toml"


def raw(profile: Path = _DEV) -> dict:
    return load_config(profile).model_dump(by_alias=True)


def synth(config: Config) -> dict:
    """Return {resource_type: [properties, ...]} across all synthesized stacks."""
    app = build_app(config, offline=True)
    app.synth()
    by_type: dict[str, list[dict]] = {}
    for stack in app.node.children:
        if not isinstance(stack, cdk.Stack):
            continue
        for resource in Template.from_stack(stack).to_json()["Resources"].values():
            by_type.setdefault(resource["Type"], []).append(resource["Properties"])
    return by_type


def runtime_env(resources: dict) -> dict:
    return resources["AWS::BedrockAgentCore::Runtime"][0]["EnvironmentVariables"]


# ── Gateway on/off ──────────────────────────────────────────────

class TestGatewayDisabled:
    def _config(self, search: str = "fixture") -> Config:
        data = raw()
        data["gateway"].update(mode="disabled", savedItemsSample=False)
        data["tools"]["productSearch"]["provider"] = search
        return Config.model_validate(data)

    def test_no_gateway_or_policy_engine(self):
        resources = synth(self._config())
        assert "AWS::BedrockAgentCore::Gateway" not in resources
        assert "AWS::BedrockAgentCore::PolicyEngine" not in resources

    def test_search_still_runs_in_the_agent(self):
        # Product search is a local tool, so it needs no Gateway.
        env = runtime_env(synth(self._config()))
        assert env["GATEWAY_ENABLED"] == "false"
        assert env["ALLOWED_TOOLS"] == "search_products"
        assert env["SEARCH_PROVIDER"] == "fixture"

    def test_no_tools_when_search_is_disabled_too(self):
        assert runtime_env(synth(self._config(search="disabled")))["ALLOWED_TOOLS"] == ""

    def test_rejects_policy_enabled(self):
        data = raw()
        data["gateway"].update(mode="disabled", savedItemsSample=False)
        data["policy"]["enabled"] = True
        with pytest.raises(ValueError, match="Cedar Policy runs on the Gateway"):
            Config.model_validate(data)

    def test_rejects_the_sample_target_without_a_managed_gateway(self):
        data = raw()
        data["gateway"]["mode"] = "disabled"  # savedItemsSample stays true in the Dev template
        with pytest.raises(ValueError, match="savedItemsSample needs a managed Gateway"):
            Config.model_validate(data)


# ── Memory strategy ─────────────────────────────────────────────

class TestMemoryStrategies:
    def test_short_term_only_has_no_strategies(self):
        data = raw()
        data["memory"].update(longTerm=False)
        memory = synth(Config.model_validate(data))["AWS::BedrockAgentCore::Memory"][0]
        assert "MemoryStrategies" not in memory

    def test_selected_subset_only(self):
        data = raw()
        data["memory"].update(longTerm=True)
        data["memory"]["strategies"] = {"semantic": True, "summarization": False, "userPreference": False}
        memory = synth(Config.model_validate(data))["AWS::BedrockAgentCore::Memory"][0]
        assert [next(iter(s)) for s in memory["MemoryStrategies"]] == ["SemanticMemoryStrategy"]

    def test_short_term_memory_is_always_deployed(self):
        # The committed templates leave long-term off; the conversation event store is still there.
        for profile in (_DEV, _PROD):
            memory = synth(load_config(profile))["AWS::BedrockAgentCore::Memory"]
            assert len(memory) == 1 and "MemoryStrategies" not in memory[0]

    def test_long_term_is_off_unless_turned_on(self):
        data = raw()
        data["memory"].pop("longTerm", None)
        assert Config.model_validate(data).memory.active_strategies == []

    def test_long_term_requires_a_strategy(self):
        data = raw()
        data["memory"].update(longTerm=True)
        data["memory"]["strategies"] = {"semantic": False, "summarization": False, "userPreference": False}
        with pytest.raises(ValueError, match="at least one strategy"):
            Config.model_validate(data)

    @pytest.mark.parametrize("days,valid", [(6, False), (7, True), (365, True), (366, False)])
    def test_event_expiry_stays_within_what_memory_accepts(self, days, valid):
        data = raw()
        data["memory"]["eventExpiryDays"] = days
        if valid:
            assert Config.model_validate(data).memory.event_expiry_days == days
        else:
            with pytest.raises(ValueError, match="eventExpiryDays"):
                Config.model_validate(data)


# ── Guardrail mode ──────────────────────────────────────────────

class TestGuardrailMode:
    def test_audit_mode_does_not_block(self):
        data = raw()
        data["guardrail"] = {"enabled": True, "mode": "AUDIT"}
        guardrail = synth(Config.model_validate(data))["AWS::Bedrock::Guardrail"][0]
        actions = {f["InputAction"] for f in guardrail["ContentPolicyConfig"]["FiltersConfig"]}
        assert actions == {"NONE"}
        sensitive = guardrail["SensitiveInformationPolicyConfig"]
        assert {r["InputAction"] for r in sensitive["PiiEntitiesConfig"] + sensitive["RegexesConfig"]} == {"NONE"}

    def test_disabled_removes_guardrail_in_dev(self):
        data = raw()
        data["guardrail"] = {"enabled": False, "mode": "ENFORCE"}
        resources = synth(Config.model_validate(data))
        assert "AWS::Bedrock::Guardrail" not in resources
        assert runtime_env(resources)["GUARDRAIL_ID"] == ""
        # Nothing to screen tool results with, even though screenToolResults defaults to true.
        assert runtime_env(resources)["SCREEN_TOOL_RESULTS"] == "false"

    def test_screens_tool_results_by_default(self, defaults):
        env = runtime_env(defaults)
        assert env["SCREEN_TOOL_RESULTS"] == "true" and env["GUARDRAIL_ID"]

    def test_screening_can_be_turned_off(self, tuned):
        env = runtime_env(tuned)
        assert env["SCREEN_TOOL_RESULTS"] == "false" and env["GUARDRAIL_ID"]

    def test_runtime_may_apply_the_guardrail_to_tool_results(self, defaults):
        statements = [st for policy in defaults["AWS::IAM::Policy"] for st in policy["PolicyDocument"]["Statement"]]
        assert any(st["Action"] == "bedrock:ApplyGuardrail" for st in statements)

    @pytest.mark.parametrize("profile,expected", [(_DEV, "async"), (_PROD, "sync")])
    def test_stream_processing_reaches_the_runtime(self, profile, expected):
        # Dev streams replies while the guardrail checks them; Prod checks before streaming.
        assert runtime_env(synth(load_config(profile)))["GUARDRAIL_STREAM_PROCESSING"] == expected

    def test_stream_processing_defaults_to_sync(self):
        data = raw()
        data["guardrail"] = {"enabled": True, "mode": "ENFORCE"}
        assert Config.model_validate(data).guardrail.stream_processing == "sync"

    def test_stream_processing_rejects_unknown_modes(self):
        data = raw()
        data["guardrail"] = {**data["guardrail"], "streamProcessing": "ASYNCHRONOUS"}
        with pytest.raises(ValueError):
            Config.model_validate(data)

    def test_prod_rejects_disabled_guardrail(self):
        data = raw(_PROD)
        data["guardrail"] = {"enabled": False, "mode": "ENFORCE"}
        with pytest.raises(ValueError, match="guardrail enabled"):
            Config.model_validate(data)

    def test_prod_requires_enforce(self):
        # AUDIT blocks nothing.
        data = raw(_PROD)
        data["guardrail"]["mode"] = "AUDIT"
        with pytest.raises(ValueError, match="ENFORCE"):
            Config.model_validate(data)

    def test_prod_may_stream_async(self):
        # Streaming is the stage's choice: async answers sooner and can show a reply briefly before the guardrail cuts it.
        data = raw(_PROD)
        data["guardrail"]["streamProcessing"] = "async"
        assert runtime_env(synth(Config.model_validate(data)))["GUARDRAIL_STREAM_PROCESSING"] == "async"


# ── Runtime limits and the WAF rate limit ───────────────────────

@pytest.fixture(scope="module")
def defaults() -> dict:
    return synth(load_config(_DEV))


@pytest.fixture(scope="module")
def tuned() -> dict:
    data = raw()
    data["runtime"].update(maxToolCalls=3, maxSessionTurns=40)
    data["guardrail"]["screenToolResults"] = False
    data["api"].update(wafRateLimit=500, requestTimeout=90)
    return synth(Config.model_validate(data))


def rate_limit(resources: dict) -> int:
    (acl,) = resources["AWS::WAFv2::WebACL"]
    return next(r["Statement"]["RateBasedStatement"]["Limit"] for r in acl["Rules"] if "RateBasedStatement" in r["Statement"])


class TestRuntimeLimits:
    def test_defaults_reach_the_runtime(self, defaults):
        env = runtime_env(defaults)
        assert (env["MAX_TOOL_CALLS"], env["MAX_SESSION_TURNS"], env["REQUEST_TIMEOUT_SECONDS"]) == ("8", "100", "120")

    def test_configured_limits_reach_the_runtime(self, tuned):
        env = runtime_env(tuned)
        assert (env["MAX_TOOL_CALLS"], env["MAX_SESSION_TURNS"], env["REQUEST_TIMEOUT_SECONDS"]) == ("3", "40", "90")

    @pytest.mark.parametrize("section,key,value", [
        ("runtime", "maxToolCalls", 0), ("runtime", "maxToolCalls", 21),
        ("runtime", "maxSessionTurns", 0), ("runtime", "maxSessionTurns", 501),
        # The load balancer's idle timeout is 150 s and the browser gives up at 130 s.
        ("api", "requestTimeout", 121),
    ])
    def test_rejects_limits_out_of_range(self, section, key, value):
        data = raw()
        data[section][key] = value
        with pytest.raises(ValueError, match=key):
            Config.model_validate(data)


class TestWafRateLimit:
    def test_defaults_to_100_requests_per_ip(self, defaults):
        assert rate_limit(defaults) == 100

    def test_comes_from_config_and_the_alarm_names_it(self, tuned):
        assert rate_limit(tuned) == 500
        (alarm,) = [a for a in tuned["AWS::CloudWatch::Alarm"] if a["AlarmName"].endswith("-WafRateLimitBlocks")]
        assert "(500)" in alarm["AlarmDescription"]

    def test_rejects_less_than_wafs_minimum(self):
        data = raw()
        data["api"]["wafRateLimit"] = 9
        with pytest.raises(ValueError, match="wafRateLimit"):
            Config.model_validate(data)


@pytest.mark.parametrize("endpoint", [
    "https://abc123.execute-api.us-west-2.amazonaws.com/mcp",
    "https://gateway.example.com/mcp",
    "https://gw.gateway.bedrock-agentcore.us-west-2.amazonaws.com.attacker.example/mcp",
])
def test_an_external_gateway_must_be_an_agentcore_gateway(endpoint):
    # The agent sends its minted token only to an AgentCore Gateway host and ignores any other.
    config = raw()
    config["gateway"] = {"mode": "external", "endpoint": endpoint, "allowedTools": ["catalog___search"],
                         "agentAuth": {"credentialProvider": "shop-gateway", "scopes": ["shop/tools"]}}
    with pytest.raises(ValueError, match="AgentCore Gateway URL"):
        Config.model_validate(config)
