"""AgentCore Policy assertions.

The dangerous failure is a policy set that denies everything in ENFORCE mode,
so the tests pin default-deny safety: a permit must exist, rollout defaults to
LOG_ONLY, and an empty policy directory is rejected outright. The sample's
policies apply to the sample only; a customer target gets its own policyFile.
"""

import re
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

import config.loader
from components.policy import PolicySpec, load_policies
from config.loader import Config, load_config
from stacks.auth_stack import AuthStack
from stacks.gateway_stack import SAVED_ITEMS_ACTION, GatewayStack

ACCOUNT = "123456789012"
REGION = "us-east-1"
GATEWAY_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/gw-abc123"


INFRA = Path(__file__).parents[1]
SAMPLE_POLICIES = INFRA / "components" / "policies" / "sample"
ORDERS = dict(name="orders", kind="lambda", lambdaArn=f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders",
              toolSchema=[{"name": "get_order", "inputSchema": {"type": "object"}},
                          {"name": "list_orders", "inputSchema": {"type": "object"}}],
              allowedTools=["get_order", "list_orders"])


def make_config(*targets, **policy_overrides) -> Config:
    config = load_config(Path(__file__).parents[1] / "config/dev.toml")
    data = config.model_dump(by_alias=True)
    data["policy"] = {**data["policy"], **policy_overrides}
    data["gateway"]["targets"] = list(targets)
    return Config.model_validate(data)


def synth(config: Config) -> Template:
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
def enabled():
    return synth(make_config(enabled=True))


class TestDisabledByDefault:
    def test_no_policy_resources_when_disabled(self):
        template = synth(make_config(enabled=False))
        template.resource_count_is("AWS::BedrockAgentCore::PolicyEngine", 0)
        template.resource_count_is("AWS::BedrockAgentCore::Policy", 0)

    def test_gateway_has_no_policy_engine_when_disabled(self):
        template = synth(make_config(enabled=False))
        gateways = template.find_resources("AWS::BedrockAgentCore::Gateway")
        assert "PolicyEngineConfiguration" not in list(gateways.values())[0]["Properties"]


class TestEnabled:
    def test_creates_engine_and_policies(self, enabled):
        enabled.resource_count_is("AWS::BedrockAgentCore::PolicyEngine", 1)
        assert len(enabled.find_resources("AWS::BedrockAgentCore::Policy")) >= 2

    def test_attached_to_gateway_in_log_only_by_default(self, enabled):
        # Default rollout must not block traffic.
        enabled.has_resource_properties(
            "AWS::BedrockAgentCore::Gateway",
            {"PolicyEngineConfiguration": Match.object_like({"Mode": "LOG_ONLY"})},
        )

    def test_shadow_engine_evaluates_an_active_policy_set(self, enabled):
        for resource in enabled.find_resources("AWS::BedrockAgentCore::Policy").values():
            assert resource["Properties"]["EnforcementMode"] == "ACTIVE"

    def test_strict_validation_installs_permit_before_forbid(self, enabled):
        resources = enabled.find_resources("AWS::BedrockAgentCore::Policy")
        assert "Policy01AllowShopperTools" in resources["Policy02ForbidBulkExtraction"]["DependsOn"]

    def test_enforce_mode_propagates_to_policies(self):
        template = synth(make_config(enabled=True, mode="ENFORCE"))
        template.has_resource_properties(
            "AWS::BedrockAgentCore::Gateway",
            {"PolicyEngineConfiguration": Match.object_like({"Mode": "ENFORCE"})},
        )
        for res in template.find_resources("AWS::BedrockAgentCore::Policy").values():
            assert res["Properties"]["EnforcementMode"] == "ACTIVE"

    def test_policies_depend_on_engine_and_target(self, enabled):
        # Cedar validates against the Gateway's tool schema, so the target must
        # exist first or the policy lands in CREATE_FAILED.
        for res in enabled.find_resources("AWS::BedrockAgentCore::Policy").values():
            depends = res.get("DependsOn", [])
            assert any("PolicyEngine" in d for d in depends)
            assert any("target" in d for d in depends)

    def test_policies_are_validated(self, enabled):
        for res in enabled.find_resources("AWS::BedrockAgentCore::Policy").values():
            assert res["Properties"]["ValidationMode"] == "FAIL_ON_ANY_FINDINGS"


class TestCedarContent:
    @pytest.fixture(scope="class")
    def specs(self) -> list[PolicySpec]:
        return load_policies(GATEWAY_ARN, SAVED_ITEMS_ACTION)

    def test_placeholders_are_substituted(self, specs):
        for spec in specs:
            assert "{{" not in spec.statement, f"{spec.name} still has a placeholder"

    def test_gateway_arn_and_tool_are_injected(self, specs):
        combined = "\n".join(s.statement for s in specs)
        assert GATEWAY_ARN in combined
        assert SAVED_ITEMS_ACTION in combined

    def test_a_permit_exists(self, specs):
        # Without a permit, default-deny blocks every tool call.
        assert any(re.search(r"^\s*permit\(", s.statement, re.M) for s in specs)

    def test_a_forbid_exists(self, specs):
        assert any(re.search(r"^\s*forbid\(", s.statement, re.M) for s in specs)

    def test_names_are_api_safe(self, specs):
        # Cedar requires [A-Za-z][A-Za-z0-9_]*, max 48 chars, and is immutable.
        for spec in specs:
            assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", spec.name), spec.name
            assert len(spec.name) <= 48

    def test_statements_meet_api_length_floor(self, specs):
        # The API rejects statements under 35 characters.
        for spec in specs:
            assert len(spec.statement) >= 35

    def test_bulk_extraction_guard_checks_limit_defensively(self, specs):
        guard = next(s for s in specs if "forbid" in s.statement)
        # Cedar errors on a missing attribute, so presence must be checked first.
        assert "context has input" in guard.statement
        assert "context.input has limit" in guard.statement

    def test_ordering_is_deterministic(self, specs):
        names = [s.name for s in specs]
        assert names == sorted(names), "policies should load in filename order"

    def test_loads_only_the_sample_folder(self, specs):
        # Customer templates elsewhere in components/policies/ must never apply to the sample. The
        # names come from the filenames and are the deployed policies' names, which Cedar can't rename.
        assert len(list(SAMPLE_POLICIES.glob("*.cedar"))) == len(specs)
        assert [s.name for s in specs] == ["p01_allow_shopper_tools", "p02_forbid_bulk_extraction"]


class TestCustomerPolicyFiles:
    @pytest.mark.parametrize("policy_file,message", [
        ("examples/orders-openapi.json", "under components/policies/"),
        ("../outside.cedar", "under components/policies/"),
        ("/etc/orders.cedar", "under components/policies/"),
        ("components/policies/sample/01-allow-shopper-tools.cedar", "outside sample/"),
        ("components/policies/orders.txt", ".cedar file"),
        ("components/policies/missing.cedar", "does not exist"),
    ])
    def test_rejects_a_file_outside_the_customer_folders_or_missing(self, policy_file, message):
        with pytest.raises(ValueError, match=message):
            make_config({**ORDERS, "policyFile": policy_file})

    def test_enforce_needs_a_policy_file_for_every_target_in_every_stage(self):
        # Dev too: Cedar denies by default, so an unlisted target would lose every tool.
        with pytest.raises(ValueError, match="policyFile for every customer target"):
            make_config(ORDERS, enabled=True, mode="ENFORCE")

    def test_log_only_may_shadow_a_target_without_one(self):
        assert make_config(ORDERS, enabled=True, mode="LOG_ONLY").policy.mode == "LOG_ONLY"

    def test_deploys_the_customer_template_once_per_allowed_tool(self, monkeypatch, tmp_path):
        policies = tmp_path / "components" / "policies" / "orders"
        policies.mkdir(parents=True)
        (policies / "orders.cedar").write_text(
            'permit(principal is AgentCore::OAuthUser, action == AgentCore::Action::"{{TOOL}}", '
            'resource == AgentCore::Gateway::"{{GATEWAY_ARN}}") when { context has input };', encoding="utf-8")
        monkeypatch.setattr(config.loader, "_INFRA_DIR", tmp_path)
        template = synth(make_config({**ORDERS, "policyFile": "components/policies/orders/orders.cedar"}, enabled=True, mode="ENFORCE"))
        statements = {res["Properties"]["Name"]: str(res["Properties"]["Definition"]["Cedar"]["Statement"])
                      for res in template.find_resources("AWS::BedrockAgentCore::Policy").values()}
        customer = {name: statement for name, statement in statements.items() if name.startswith("allow_orders")}
        assert sorted(customer) == ["allow_orders___get_order", "allow_orders___list_orders"]
        assert all("{{" not in statement for statement in customer.values())
        assert 'orders___get_order' in customer["allow_orders___get_order"]
        # The sample's own policies name only the sample's tool.
        sample = {name: statement for name, statement in statements.items() if name not in customer}
        assert len(sample) == 2 and all("saved-items___get_saved_items" in statement for statement in sample.values())


class TestEmptyPolicyDirectory:
    def test_rejects_enabled_with_no_cedar_files(self, monkeypatch, tmp_path):
        # An empty engine in ENFORCE mode would deny every tool call.
        import components.policy as policy_module

        monkeypatch.setattr(policy_module, "_SAMPLE_POLICY_DIR", tmp_path)
        with pytest.raises(ValueError, match="sample/ contains no .cedar files"):
            synth(make_config(enabled=True))
