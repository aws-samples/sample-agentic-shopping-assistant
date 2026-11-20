import functools
from pathlib import Path
import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Template, Match
from app import build_app
from config.loader import Config, load_config


# "template" synthesizes the committed file unchanged: Prod's placeholder HTTP catalog, since Prod rejects the fixture.
PROFILES = [("dev", "fixture"), ("prod", "template"), ("prod", "opensearch"), ("dev", "http"), ("dev", "mcp"), ("prod", "external"), ("dev", "memory-evaluations")]


@functools.cache
def synthesize(stage: str, provider: str) -> tuple[Config, list[cdk.Stack], dict[str, dict]]:
    """A profile's config, stacks and templates, synthesized once for every test that reads them."""
    raw = load_config(Path(__file__).parents[1] / f"config/{stage}.toml").model_dump(by_alias=True)
    if provider in ("http", "mcp", "external"):
        raw["auth"].update(provider="oidc", issuer="https://identity.example", clientId="shop", audiences=["aria-api"], scopes=["aria/chat"])
        # A BYO IdP names the AgentCore Identity provider the agent mints its Gateway token from.
        # A managed Gateway also names the agent's own client, the only one its interceptor accepts.
        raw["gateway"]["agentAuth"] = dict(flow="on_behalf_of", credentialProvider="shop-gateway", clientId="shop-agent",
                                           audience="shop-tools", scopes=["shop-tools/invoke"])
    if provider == "memory-evaluations":
        raw["memory"]["longTerm"] = True
        raw["evaluations"]["enabled"] = True
        raw["operations"].update(transactionSearchReady=True, captureContent=True, traceLogGroup="/aws/bedrock-agentcore/runtimes/example-DEFAULT")
    elif provider in ("fixture", "opensearch", "http"):
        raw["tools"]["productSearch"].update(provider=provider, endpoint="https://catalog.example/search" if provider == "http" else "")
    elif provider == "mcp":
        raw["tools"]["productSearch"]["provider"] = "disabled"
        raw["gateway"]["targets"] = [dict(name="catalog", kind="mcp", endpoint="https://catalog.example/mcp", allowedTools=["search"], credentialType="OAUTH", providerArn="arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:token-vault/default/oauth2credentialprovider/catalog", scopes=["catalog:read"])]
    elif provider == "external":
        # Search runs in the agent, so local search works alongside an external Gateway.
        raw["gateway"].update(mode="external", endpoint="https://gw-1.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp", allowedTools=["catalog___search"], authorizationVerified=True)
    config = Config.model_validate(raw)
    app = build_app(config, offline=True)
    app.synth()
    stacks = [s for s in app.node.children if isinstance(s, cdk.Stack)]
    return config, stacks, {s.stack_name: Template.from_stack(s).to_json() for s in stacks}


@pytest.mark.parametrize("stage,provider", PROFILES)
def test_profiles_synthesize_with_nag(stage, provider):
    config, stacks, templates = synthesize(stage, provider)
    for stack in stacks:
        errors = Annotations.from_stack(stack).find_error("*", Match.any_value())
        assert not errors, errors
    resources = [v for t in templates.values() for v in t["Resources"].values()]
    types = [r["Type"] for r in resources]
    assert ("AWS::Cognito::UserPool" in types) == (config.auth.provider == "cognito")
    assert ("AWS::OpenSearchService::Domain" in types) == (provider == "opensearch")
    assert ("AWS::BedrockAgentCore::Gateway" in types) == (provider != "external")
    # Short-term memory is in every profile; the memory-evaluations profile adds long-term strategies.
    assert "AWS::BedrockAgentCore::Memory" in types
    assert ("AWS::BedrockAgentCore::OnlineEvaluationConfig" in types) == (provider == "memory-evaluations")
    assert "AWS::CloudWatch::Dashboard" in types
    if config.policy.enabled and config.gateway.mode == "managed":
        gateway_template = templates[f"{config.ns}Gateway"]["Resources"]
        gateway_role = gateway_template["Gateway"]["Properties"]["RoleArn"]["Fn::GetAtt"][0]
        statements = [
            statement
            for item in gateway_template.values()
            if item["Type"] == "AWS::IAM::Policy" and {"Ref": gateway_role} in item["Properties"].get("Roles", [])
            for statement in item["Properties"]["PolicyDocument"]["Statement"]
        ]
        required_actions = {
            "bedrock-agentcore:GetPolicyEngine", "bedrock-agentcore:ListPolicies",
            "bedrock-agentcore:GetPolicy", "bedrock-agentcore:AuthorizeAction",
            "bedrock-agentcore:PartiallyAuthorizeActions",
        }
        granted = set()
        for statement in statements:
            actions = statement["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            if required_actions.intersection(actions):
                granted.update(actions)
                grants = statement["Resource"]
                grants = grants if isinstance(grants, list) else [grants]
                assert "*" not in grants
                assert {"Fn::GetAtt": ["PolicyEngine", "PolicyEngineArn"]} in grants
        assert required_actions.issubset(granted)
    for stack_name, body in templates.items():
        for logical_id, resource in body["Resources"].items():
            if resource["Type"] == "AWS::IAM::Policy":
                for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
                    grants = statement.get("Resource", [])
                    grants = grants if isinstance(grants, list) else [grants]
                    if "*" in grants:
                        actions = statement["Action"]
                        actions = actions if isinstance(actions, list) else [actions]
                        permitted = {
                            "ecr:GetAuthorizationToken", "cloudwatch:PutMetricData",
                            "xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets",
                            "logs:DescribeLogGroups", "logs:GetQueryResults",
                        }
                        if stack_name.endswith("Opensearch"):
                            # CDK's log-delivery helper for the domain's logs: account-level resource-policy APIs.
                            # The Nori lookup: a read with no resource-level permissions.
                            permitted |= {"logs:PutResourcePolicy", "logs:DeleteResourcePolicy", "es:DescribePackages"}
                        assert set(actions).issubset(permitted), (logical_id, actions)
            if resource["Type"] not in ("AWS::BedrockAgentCore::Runtime", "AWS::BedrockAgentCore::Gateway", "AWS::BedrockAgentCore::OnlineEvaluationConfig"):
                continue
            role_key = "EvaluationExecutionRoleArn" if resource["Type"].endswith("OnlineEvaluationConfig") else "RoleArn"
            role = resource["Properties"][role_key]["Fn::GetAtt"][0]
            policies = [name for name, item in body["Resources"].items()
                        if item["Type"] == "AWS::IAM::Policy" and {"Ref": role} in item["Properties"].get("Roles", [])]
            assert set(policies).issubset(resource.get("DependsOn", [])), (logical_id, policies)
    if stage == "prod":
        listeners = [r["Properties"] for r in resources if r["Type"] == "AWS::ElasticLoadBalancingV2::Listener"]
        assert all(l["Protocol"] == "HTTPS" for l in listeners)
        assert all(s.termination_protection for s in stacks)
        # Prod keeps every log group, flow logs and the Gateway's functions included, when a stack goes.
        assert all(r.get("DeletionPolicy") == "Retain" for r in resources if r["Type"] == "AWS::Logs::LogGroup")
        for r in resources:
            if r["Type"] == "AWS::OpenSearchService::Domain":
                assert r["Properties"]["ClusterConfig"]["InstanceCount"] == 3
                assert r["Properties"]["ClusterConfig"]["DedicatedMasterCount"] == 3
                assert r["DeletionPolicy"] == "Retain"


@pytest.mark.parametrize("stage,provider", PROFILES)
def test_the_runtime_always_runs_in_the_vpc(stage, provider):
    config, _, templates = synthesize(stage, provider)
    resources = templates[f"{config.ns}AgentRuntime"]["Resources"]
    (runtime,) = [r for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime"]
    network = runtime["Properties"]["NetworkConfiguration"]
    assert network["NetworkMode"] == "VPC"
    # Its own security group, under the logical ID a stage that already ran VPC mode has, so it isn't replaced.
    assert network["NetworkModeConfig"]["SecurityGroups"] == [{"Fn::GetAtt": ["RuntimeSecurityGroup228FFEFC", "GroupId"]}]
    # The VPC's private subnets with egress, one per AZ: public subnets give no internet route.
    subnets = network["NetworkModeConfig"]["Subnets"]
    assert len(subnets) == 3 and all("egressSubnet" in str(subnet) for subnet in subnets)


@pytest.mark.parametrize("section", [("runtime",), ("tools", "productSearch"), ("api",)])
def test_an_unknown_key_fails_with_its_path(section):
    # A misspelled or invented key fails the plan instead of being ignored.
    raw = load_config(Path(__file__).parents[1] / "config/dev.toml").model_dump(by_alias=True)
    table = raw
    for key in section:
        table = table[key]
    table["notAKey"] = True
    path = r"\.".join((*section, "notAKey"))
    with pytest.raises(ValueError, match=rf"{path}\n  Extra inputs are not permitted"):
        Config.model_validate(raw)


def prod_raw() -> dict:
    return load_config(Path(__file__).parents[1] / "config/prod.toml").model_dump(by_alias=True)


def test_prod_rejects_missing_security_settings():
    raw = prod_raw()
    raw["policy"]["mode"] = "LOG_ONLY"
    with pytest.raises(ValueError, match="ENFORCE"):
        Config.model_validate(raw)


@pytest.mark.parametrize("allow,public,valid", [
    (["0.0.0.0/0"], False, False),
    (["10.0.0.0/7"], False, False),
    (["203.0.113.0/24", "2001:db8::/31"], False, False),
    (["10.0.0.0/8", "2001:db8::/32"], False, True),
    # Opening the load balancer to the internet takes an explicit second key.
    (["0.0.0.0/0"], True, True),
])
def test_prod_ingress_is_narrow_unless_deliberately_public(allow, public, valid):
    raw = prod_raw()
    raw["api"].update(allowIpList=allow, allowPublicIngress=public)
    if valid:
        assert Config.model_validate(raw).api.allow_public_ingress is public
    else:
        with pytest.raises(ValueError, match="allowPublicIngress"):
            Config.model_validate(raw)


def test_prod_rejects_the_saved_items_sample():
    raw = prod_raw()
    raw["gateway"]["savedItemsSample"] = True
    with pytest.raises(ValueError, match="savedItemsSample"):
        Config.model_validate(raw)


def test_prod_rejects_the_fixture_catalog():
    raw = prod_raw()
    raw["tools"]["productSearch"]["provider"] = "fixture"
    with pytest.raises(ValueError, match="Prod can't deploy tools.productSearch.provider = fixture"):
        Config.model_validate(raw)


@pytest.mark.parametrize("provider", ["http", "opensearch", "disabled"])
def test_prod_accepts_a_real_catalog_or_none(provider):
    raw = prod_raw()
    raw["tools"]["productSearch"]["provider"] = provider
    assert Config.model_validate(raw).tools.product_search.provider == provider
