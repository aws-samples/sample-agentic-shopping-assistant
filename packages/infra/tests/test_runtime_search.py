"""The agent's local product search: what the Runtime is given for each catalog provider."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template

from app import build_app
from config.loader import Config, load_config


def runtime_template(provider: str, **search) -> dict:
    raw = load_config(Path(__file__).parents[1] / "config/dev.toml").model_dump(by_alias=True)
    raw["tools"]["productSearch"].update(provider=provider, **search)
    config = Config.model_validate(raw)
    app = build_app(config, offline=True)
    app.synth()
    stack = next(s for s in app.node.children if isinstance(s, cdk.Stack) and s.stack_name.endswith("AgentRuntime"))
    return Template.from_stack(stack).to_json()


def runtime_env(template: dict) -> dict:
    runtime = next(r for r in template["Resources"].values() if r["Type"] == "AWS::BedrockAgentCore::Runtime")
    return runtime["Properties"]["EnvironmentVariables"]


def statements(template: dict) -> list[dict]:
    return [st for r in template["Resources"].values() if r["Type"] == "AWS::IAM::Policy"
            for st in r["Properties"]["PolicyDocument"]["Statement"]]


def test_fixture_search_needs_no_grants():
    template = runtime_template("fixture")
    env = runtime_env(template)
    assert env["SEARCH_PROVIDER"] == "fixture"
    assert "search_products" in env["ALLOWED_TOOLS"].split(",")
    assert "es:ESHttp" not in str(template)


class TestOpenSearch:
    template = None

    @classmethod
    def setup_class(cls):
        cls.template = runtime_template("opensearch")

    def test_runtime_joins_the_vpc(self):
        runtime = next(r for r in self.template["Resources"].values() if r["Type"] == "AWS::BedrockAgentCore::Runtime")
        assert runtime["Properties"]["NetworkConfiguration"]["NetworkMode"] == "VPC"

    def test_can_post_to_the_index_search_path(self):
        # grant_read() only allows ESHttpGet/ESHttpHead, but an OpenSearch
        # `_search` is a POST. Without ESHttpPost every search fails with a 403 at runtime.
        search = next(st for st in statements(self.template) if "es:ESHttpPost" in st["Action"])
        assert all("/_search" in str(resource) for resource in search["Resource"])

    def test_has_no_write_permissions(self):
        # A read-only tool must not be able to index or delete documents.
        body = str(self.template)
        assert "es:ESHttpPut" not in body and "es:ESHttpDelete" not in body

    def test_only_the_runtime_may_reach_the_domain(self):
        ingress = [r["Properties"] for r in self.template["Resources"].values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"]
        assert any(i["FromPort"] == 443 and "RuntimeSecurityGroup" in str(i["SourceSecurityGroupId"]) for i in ingress)

    def test_receives_the_index_and_host(self):
        env = runtime_env(self.template)
        assert env["OPENSEARCH_PRODUCT_INDEX"] == "products"
        assert env["OPENSEARCH_HOST"]


def test_http_search_can_read_only_its_credential():
    arn = "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:catalog-key-AbCdEf"
    template = runtime_template("http", endpoint="https://catalog.example/search", secretArn=arn)
    env = runtime_env(template)
    assert env["SEARCH_ENDPOINT"] == "https://catalog.example/search"
    reads = [st for st in statements(template) if "secretsmanager:GetSecretValue" in st["Action"]]
    catalog = [st for st in reads if "catalog-key" in str(st["Resource"])]
    assert catalog and all(arn in str(st["Resource"]) for st in catalog)
    # The only other secret the Runtime reads is its Gateway credential provider's.
    assert all("bedrock-agentcore-identity!default/oauth2/ariadev-gateway" in str(st["Resource"]) for st in reads if st not in catalog)
