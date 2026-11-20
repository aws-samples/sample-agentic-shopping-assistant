"""Definition-driven catalog search: the loader's catalogFile, the Runtime's settings and grants, and
the OpenSearch stack's domain, Nori plugin, catalog bucket and indexing task."""

import functools
import json
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from aria_catalog.definition import EXAMPLE_DIR, definition_from_env, load_definition
from app import build_app
from config.loader import Config, load_config
from stacks.agent_runtime_stack import MAX_ENVIRONMENT_VALUE, MAX_ENVIRONMENT_VARIABLES, checked_environment

CONFIG = Path(__file__).parents[1] / "config"
EXAMPLE_TOML = (EXAMPLE_DIR / "catalog.toml").read_text(encoding="utf-8")
EMBED_RESOURCES = [
    {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, ":bedrock:ap-northeast-2:123456789012:inference-profile/global.cohere.embed-v4:0"]]},
    {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, ":bedrock:*::foundation-model/cohere.embed-v4:0"]]},
]
DOMAIN_ARN = {"Fn::GetAtt": ["OpensearchClusterOpensearchDomain544CD6CE", "Arn"]}


def raw_config(stage: str = "dev", **search) -> dict:
    raw = load_config(CONFIG / f"{stage}.toml").model_dump(by_alias=True)
    raw["tools"]["productSearch"].update(search)
    return raw


@functools.cache
def standard_definition_file() -> str:
    """The example definition with the standard analyzer, as a catalogFile."""
    import tempfile

    path = Path(tempfile.mkdtemp()) / "catalog.toml"
    path.write_text(EXAMPLE_TOML.replace('analyzer = "nori"', 'analyzer = "standard"'), encoding="utf-8")
    return str(path)


@functools.cache
def synthesize(stage: str = "dev", provider: str = "opensearch", catalog_file: str = "") -> dict[str, dict]:
    """Templates by stack role (the name without the namespace)."""
    raw = raw_config(stage, provider=provider, endpoint="https://catalog.example/search" if provider == "http" else "",
                     catalogFile=catalog_file)
    config = Config.model_validate(raw)
    app = build_app(config, offline=True)
    app.synth()
    return {s.stack_name.removeprefix(config.ns): Template.from_stack(s).to_json()
            for s in app.node.children if isinstance(s, cdk.Stack)}


def resources(template: dict, type_: str) -> dict[str, dict]:
    return {k: v for k, v in template["Resources"].items() if v["Type"] == type_}


def runtime_env(templates: dict) -> dict:
    (runtime,) = resources(templates["AgentRuntime"], "AWS::BedrockAgentCore::Runtime").values()
    return runtime["Properties"]["EnvironmentVariables"]


def role_statements(template: dict, role_prefix: str) -> list[dict]:
    return [st for policy in resources(template, "AWS::IAM::Policy").values()
            if any(role["Ref"].startswith(role_prefix) for role in policy["Properties"]["Roles"])
            for st in policy["Properties"]["PolicyDocument"]["Statement"]]


# --- The loader ---


def test_catalog_file_is_relative_to_the_config_file(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "catalog.toml").write_text(EXAMPLE_TOML.replace('name = "NORDLY"', 'name = "Shop"'), encoding="utf-8")
    config_file = tmp_path / "stage.toml"
    config_file.write_text((CONFIG / "dev.toml").read_text(encoding="utf-8").replace(
        'provider = "fixture"', 'provider = "opensearch"\ncatalogFile = "shop/catalog.toml"'), encoding="utf-8")
    settings = load_config(config_file).tools.product_search
    assert settings.catalog_file == str((tmp_path / "shop" / "catalog.toml").resolve())
    assert settings.definition.name == "Shop"
    # The absolute path survives a dump and reload, as the profile tests and gen_docs do.
    reloaded = Config.model_validate(load_config(config_file).model_dump(by_alias=True))
    assert reloaded.tools.product_search.definition == settings.definition


def test_an_empty_catalog_file_is_the_packaged_example():
    settings = Config.model_validate(raw_config(provider="opensearch")).tools.product_search
    assert settings.catalog_file == "" and settings.definition == load_definition()


def test_an_invalid_definition_fails_at_load_naming_the_file(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(EXAMPLE_TOML.replace('analyzer = "nori"', 'analyzer = "kuromoji"'), encoding="utf-8")
    with pytest.raises(ValueError, match=f"catalogFile {bad} is not a valid catalog definition: catalog.analyzer"):
        Config.model_validate(raw_config(provider="opensearch", catalogFile=str(bad)))


@pytest.mark.parametrize("name", ["missing.toml", "catalog.json"])
def test_the_catalog_file_must_be_an_existing_toml_file(tmp_path, name):
    (tmp_path / "catalog.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an existing .toml catalog definition"):
        Config.model_validate(raw_config(provider="http", endpoint="https://catalog.example/search",
                                         catalogFile=str(tmp_path / name)))


def test_the_fixture_takes_no_catalog_file():
    with pytest.raises(ValueError, match="catalogFile must be empty with provider = fixture"):
        Config.model_validate(raw_config(provider="fixture", catalogFile=standard_definition_file()))


@pytest.mark.parametrize("alias", ["Products", "_products", "prod*", "a/b"])
def test_the_alias_must_be_an_index_name(alias):
    with pytest.raises(ValueError, match="openSearchIndex"):
        Config.model_validate(raw_config(provider="opensearch", openSearchIndex=alias))


# --- The Runtime ---


def definition_values(env: dict) -> dict:
    return {name: value for name, value in env.items() if name.startswith("CATALOG_DEFINITION")}


@pytest.mark.parametrize("provider", ["fixture", "opensearch", "http"])
def test_the_runtime_gets_the_definition_as_compact_json(provider):
    env = runtime_env(synthesize("dev", provider))
    values = definition_values(env)
    # The example is longer than one value the resource takes, so it comes in two.
    assert list(values) == ["CATALOG_DEFINITION", "CATALOG_DEFINITION_2"]
    assert all(len(value) <= MAX_ENVIRONMENT_VALUE for value in values.values())
    assert definition_from_env(values) == load_definition()
    joined = "".join(values.values())
    assert joined == json.dumps(json.loads(joined), separators=(",", ":"), ensure_ascii=False)
    assert "SEARCH_FIELD_MAP" not in env
    assert len(env) <= MAX_ENVIRONMENT_VARIABLES


def test_a_custom_definition_reaches_the_runtime():
    env = runtime_env(synthesize("dev", "opensearch", standard_definition_file()))
    assert definition_from_env(definition_values(env)).analyzer == "standard"


def test_environment_values_over_agentcores_limit_fail_synth():
    # The AWS::BedrockAgentCore::Runtime resource schema takes at most 2,048 characters a value.
    assert MAX_ENVIRONMENT_VALUE == 2048
    checked_environment({"CATALOG_DEFINITION": "x" * MAX_ENVIRONMENT_VALUE})
    with pytest.raises(ValueError, match="CATALOG_DEFINITION_2 is 2049 bytes.*at most 2048 characters.*Shorten the catalog"):
        checked_environment({"CATALOG_DEFINITION_2": "x" * (MAX_ENVIRONMENT_VALUE + 1)})
    # Korean text is 3 bytes a character in UTF-8, and the check counts bytes, the stricter measure.
    with pytest.raises(ValueError, match="CATALOG_DEFINITION is 2049 bytes"):
        checked_environment({"CATALOG_DEFINITION": "x" * 2046 + "가"})
    with pytest.raises(ValueError, match="at most 50"):
        checked_environment({f"VARIABLE_{n}": "" for n in range(MAX_ENVIRONMENT_VARIABLES + 1)})
    # A token resolves at deploy; it isn't measured.
    checked_environment({"MEMORY_ID": cdk.Token.as_string(cdk.Fn.ref("x" * 6000))})


def test_a_definition_too_big_for_the_runtime_fails_synth(tmp_path):
    # Two filters of 100 values of 98 characters: about 20 KB of JSON, past 8 values of 2,048 bytes.
    def values(prefix: str) -> str:
        return ", ".join(f'"{prefix * 95}{n:03d}"' for n in range(100))
    big = tmp_path / "big.toml"
    text = EXAMPLE_TOML.replace('values = ["Summer", "Fall", "Winter", "Spring"]', f"values = [{values('s')}]")
    text = text.replace('values = ["Men", "Women", "Unisex", "Boys", "Girls"]', f"values = [{values('g')}]")
    assert text.count("sss") and text.count("ggg")
    big.write_text(text, encoding="utf-8")
    config = Config.model_validate(raw_config(provider="http", endpoint="https://catalog.example/search", catalogFile=str(big)))
    with pytest.raises(ValueError, match="more than the 8 environment values of 2048 bytes"):
        build_app(config, offline=True)


def test_opensearch_search_may_invoke_only_the_embedding_model_and_search():
    runtime = synthesize("dev", "opensearch")["AgentRuntime"]
    statements = role_statements(runtime, "RuntimeRole")
    embedding = [st for st in statements if "cohere" in json.dumps(st["Resource"])]
    assert embedding == [{"Action": "bedrock:InvokeModel", "Effect": "Allow", "Resource": EMBED_RESOURCES}]
    es = [st for st in statements if any(a.startswith("es:") for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"]))]
    assert len(es) == 1 and es[0]["Action"] == ["es:ESHttpPost", "es:ESHttpGet"]
    assert all(json.dumps(r).endswith(('/products/_search"]]}', '/products/_search/*"]]}')) for r in es[0]["Resource"])


@pytest.mark.parametrize("provider", ["fixture", "http"])
def test_other_providers_get_no_embedding_grant(provider):
    statements = role_statements(synthesize("dev", provider)["AgentRuntime"], "RuntimeRole")
    assert not [st for st in statements if "cohere" in json.dumps(st["Resource"])]


# --- The OpenSearch stack ---


def test_the_domain_runs_opensearch_3_5():
    (domain,) = resources(synthesize()["Opensearch"], "AWS::OpenSearchService::Domain").values()
    assert domain["Properties"]["EngineVersion"] == "OpenSearch_3.5"


def custom_calls(template: dict) -> dict[str, dict]:
    """Each Custom::AWS resource's Create, Update and Delete calls, with tokens rendered as text."""
    def text(value):
        if isinstance(value, dict) and "Fn::Join" in value:
            return "".join(part if isinstance(part, str) else json.dumps(part).replace('"', "'") for part in value["Fn::Join"][1])
        return value
    return {lid: {phase: json.loads(text(r["Properties"][phase])) for phase in ("Create", "Update", "Delete")
                  if phase in r["Properties"]} | {"_resource": r}
            for lid, r in resources(template, "Custom::AWS").items()}


def test_nori_is_looked_up_then_associated():
    calls = custom_calls(synthesize()["Opensearch"])
    package = calls["NoriPackage6D20EEF4"]
    assert package["Create"] == package["Update"]
    assert package["Create"]["action"] == "describePackages" and package["Create"]["service"] == "OpenSearch"
    assert package["Create"]["parameters"]["Filters"] == [
        {"Name": "PackageName", "Value": ["analysis-nori"]},
        {"Name": "EngineVersion", "Value": ["OpenSearch_3.5"]},
        {"Name": "PackageType", "Value": ["ZIP-PLUGIN"]},
    ]
    assert package["Create"]["outputPaths"] == ["PackageDetailsList.0.PackageID"]
    association = calls["NoriAssociationF6F9CC02"]
    looked_up = "{'Fn::GetAtt': ['NoriPackage6D20EEF4', 'PackageDetailsList.0.PackageID']}"
    target = {"PackageID": looked_up, "DomainName": "{'Ref': 'OpensearchClusterOpensearchDomain544CD6CE'}"}
    assert association["Create"]["action"] == association["Update"]["action"] == "associatePackage"
    assert association["Create"]["parameters"] == association["Update"]["parameters"] == target
    # Nothing dissociates it: the service refuses while an index still uses the plugin.
    assert "Delete" not in association
    # No package ID is hard-coded: it differs by Region (us-west-2's and ap-northeast-2's today).
    body = json.dumps(synthesize()["Opensearch"])
    assert "G26022645" not in body and "G259293935" not in body
    # After the domain and its own policy updates.
    assert "OpensearchClusterOpensearchDomain544CD6CE" in association["_resource"]["DependsOn"]


def test_nori_grants_are_least_privilege():
    statements = {lid: policy["Properties"]["PolicyDocument"]["Statement"]
                  for lid, policy in resources(synthesize()["Opensearch"], "AWS::IAM::Policy").items() if lid.startswith("Nori")}
    assert statements == {
        "NoriPackageCustomResourcePolicyEFA13D84": [{"Action": "es:DescribePackages", "Effect": "Allow", "Resource": "*"}],
        "NoriAssociationCustomResourcePolicyF75330BC": [
            {"Action": "es:AssociatePackage", "Effect": "Allow", "Resource": DOMAIN_ARN}],
    }


@pytest.mark.parametrize("stage", ["dev", "prod"])
def test_every_stage_keeps_the_plugin(stage):
    association = resources(synthesize(stage)["Opensearch"], "Custom::AWS")["NoriAssociationF6F9CC02"]
    assert association["DeletionPolicy"] == association["UpdateReplacePolicy"] == "Retain"


def test_a_standard_analyzer_needs_no_nori():
    template = synthesize("dev", "opensearch", standard_definition_file())["Opensearch"]
    assert "analysis-nori" not in json.dumps(template) and not resources(template, "Custom::AWS")
    assert "NoriPackageId" not in template["Outputs"]


@pytest.mark.parametrize("provider,alarmed", [("opensearch", True), ("http", False), ("fixture", False)])
def test_a_keyword_fallback_alarms_only_where_search_embeds(provider, alarmed):
    # The fallback still answers, so only this alarm reports a lost embedding model.
    alarms = {a["Properties"]["AlarmName"].split("-", 1)[1]: a["Properties"]
              for a in resources(synthesize("dev", provider)["Operations"], "AWS::CloudWatch::Alarm").values()}
    assert "SearchFailures" in alarms
    assert ("SearchEmbeddingFailures" in alarms) is alarmed
    if alarmed:
        alarm = alarms["SearchEmbeddingFailures"]
        assert (alarm["MetricName"], alarm["Statistic"], alarm["Threshold"], alarm["EvaluationPeriods"]) == (
            "SearchEmbeddingFailures", "Sum", 3, 1)
        (embedding_filter,) = [f["Properties"] for f in resources(synthesize("dev", provider)["Operations"], "AWS::Logs::MetricFilter").values()
                               if f["Properties"]["MetricTransformations"][0]["MetricName"] == "SearchEmbeddingFailures"]
        assert embedding_filter["FilterPattern"] == '{ $.event = "product_search_embedding_failed" }'


@pytest.mark.parametrize("stage,retained", [("dev", False), ("prod", True)])
def test_the_catalog_bucket_is_private_tls_only_and_versioned(stage, retained):
    template = synthesize(stage)["Opensearch"]
    (bucket,) = resources(template, "AWS::S3::Bucket").values()
    properties = bucket["Properties"]
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True}
    assert properties["BucketEncryption"]["ServerSideEncryptionConfiguration"] == [
        {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert bucket["DeletionPolicy"] == ("Retain" if retained else "Delete")
    assert bool(resources(template, "Custom::S3AutoDeleteObjects")) is not retained
    (policy,) = resources(template, "AWS::S3::BucketPolicy").values()
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    assert {"Action": "s3:*", "Condition": {"Bool": {"aws:SecureTransport": "false"}}, "Effect": "Deny",
            "Principal": {"AWS": "*"}} in [{k: v for k, v in st.items() if k != "Resource"} for st in statements]
    # The only Allow is Dev's auto-delete helper, by its role.
    assert all(st["Principal"] != {"AWS": "*"} for st in statements if st["Effect"] == "Allow")
    assert "CatalogBucketName" in template["Outputs"]


def test_the_task_role_has_exactly_its_three_statements():
    template = synthesize()["Opensearch"]
    assert role_statements(template, "IndexerTaskRole") == [
        {"Action": "s3:GetObject", "Effect": "Allow",
         "Resource": {"Fn::Join": ["", [{"Fn::GetAtt": ["CatalogBucket5819BC2E", "Arn"]}, "/catalog/*"]]}},
        {"Action": "bedrock:InvokeModel", "Effect": "Allow", "Resource": EMBED_RESOURCES},
        {"Action": ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPut", "es:ESHttpPost"], "Effect": "Allow", "Resource": [
            {"Fn::Join": ["", [DOMAIN_ARN, path]]}
            for path in ("/products", "/products-*", "/_aliases", "/_search/pipeline/products-hybrid")]},
    ]
    (role,) = [r for lid, r in resources(template, "AWS::IAM::Role").items() if lid.startswith("IndexerTaskRole")]
    (trust,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
    assert trust["Condition"]["StringEquals"] == {"aws:SourceAccount": "123456789012"}


def test_the_task_reaches_the_domain_on_443_from_its_own_group_only():
    template = synthesize()["Opensearch"]
    ingress = [r["Properties"] for r in resources(template, "AWS::EC2::SecurityGroupIngress").values()]
    assert ingress == [{
        "Description": "The catalog indexing task may load the catalog domain",
        "FromPort": 443, "ToPort": 443, "IpProtocol": "tcp",
        "GroupId": {"Fn::GetAtt": ["OpensearchClusterOpensearchSecurityGroup8445C822", "GroupId"]},
        "SourceSecurityGroupId": {"Fn::GetAtt": ["IndexerSecurityGroup5826F55F", "GroupId"]},
    }]
    for group in resources(template, "AWS::EC2::SecurityGroup").values():
        assert "SecurityGroupIngress" not in group["Properties"]


def test_the_task_definition_runs_the_indexer_with_the_runtimes_definition():
    templates = synthesize()
    (task,) = resources(templates["Opensearch"], "AWS::ECS::TaskDefinition").values()
    assert task["Properties"]["RuntimePlatform"] == {"CpuArchitecture": "ARM64", "OperatingSystemFamily": "LINUX"}
    assert task["Properties"]["RequiresCompatibilities"] == ["FARGATE"]
    (container,) = task["Properties"]["ContainerDefinitions"]
    assert container["Name"] == "indexer" and container["ReadonlyRootFilesystem"] is True
    assert container["LogConfiguration"]["Options"]["awslogs-stream-prefix"] == "indexer"
    env = {e["Name"]: e["Value"] for e in container["Environment"]}
    assert definition_values(env) == definition_values(runtime_env(templates))
    assert env["OPENSEARCH_PRODUCT_INDEX"] == "products"


def test_the_outputs_the_script_reads():
    outputs = synthesize()["Opensearch"]["Outputs"]
    for name in ("CatalogBucketName", "IndexerCluster", "IndexerTaskDefinitionArn", "IndexerSubnets",
                 "IndexerSecurityGroup", "IndexerLogGroup", "ProductAlias", "IndexReplicas", "DomainName", "NoriPackageId"):
        assert name in outputs, name
    assert outputs["IndexerCluster"]["Value"] == "ariadev"
    assert outputs["IndexReplicas"]["Value"] == "0" and synthesize("prod")["Opensearch"]["Outputs"]["IndexReplicas"]["Value"] == "1"
    # The Runtime's private subnets with egress: the task gets no public IP.
    assert "egressSubnet" in json.dumps(outputs["IndexerSubnets"])


@pytest.mark.parametrize("stage", ["dev", "prod"])
def test_no_public_endpoints(stage):
    for role, template in synthesize(stage).items():
        assert not resources(template, "AWS::Lambda::Url"), role
        for permission in resources(template, "AWS::Lambda::Permission").values():
            assert permission["Properties"]["Principal"] != "*", role
        rules = [r["Properties"] for r in resources(template, "AWS::EC2::SecurityGroupIngress").values()]
        rules += [rule for group in resources(template, "AWS::EC2::SecurityGroup").values()
                  for rule in group["Properties"].get("SecurityGroupIngress", [])]
        for rule in rules:
            assert rule.get("CidrIp") != "0.0.0.0/0" and rule.get("CidrIpv6") != "::/0", (role, rule)
