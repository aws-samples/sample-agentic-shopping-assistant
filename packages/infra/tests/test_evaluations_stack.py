"""AgentCore Evaluations stack assertions.

The failure mode this guards hardest: a service name that does not match the
deployed Runtime means the evaluation scores nothing, silently.
"""

import aws_cdk as cdk
import pytest
from pathlib import Path
from aws_cdk.assertions import Match, Template

from components.evaluators import CustomEvaluatorSpec, custom_evaluators
from config.loader import Config, load_config
from stacks.evaluations_stack import EvaluationsStack

ACCOUNT = "123456789012"
REGION = "us-east-1"
RUNTIME_NAME = "ariadev_agent"
RESULTS_LOG_GROUP = "/ariadev/evaluations/results"
TRACE_LOG_GROUP = "/aws/bedrock-agentcore/runtimes/ariadev_agent-AbC123-DEFAULT"


def make_config(**eval_overrides) -> Config:
    config = load_config(Path(__file__).parents[1] / "config/dev.toml").model_copy(deep=True)
    data = config.model_dump(by_alias=True)
    data["operations"].update(transactionSearchReady=True, traceLogGroup=TRACE_LOG_GROUP, captureContent=True)
    data["evaluations"] = {**data["evaluations"], **eval_overrides}
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
    stack = EvaluationsStack(
        app,
        "Evaluations",
        config=config,
        runtime_name=RUNTIME_NAME,
        env=cdk.Environment(account=ACCOUNT, region=REGION),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def template():
    return synth(make_config(enabled=True))


class TestDataSource:
    def test_service_name_matches_runtime_and_endpoint(self, template):
        # Must be "<runtime-name>.<endpoint>"; a mismatch scores zero sessions.
        template.has_resource_properties(
            "AWS::BedrockAgentCore::OnlineEvaluationConfig",
            {
                "DataSourceConfig": {
                    "CloudWatchLogs": {
                        "LogGroupNames": [TRACE_LOG_GROUP],
                        "ServiceNames": [f"{RUNTIME_NAME}.DEFAULT"],
                    }
                }
            },
        )

    def test_custom_endpoint_name_is_honoured(self):
        app = cdk.App(context={"ns": "ARIADev", "stage": "Dev", "aws:cdk:bundling-stacks": []})
        stack = EvaluationsStack(
            app,
            "Evaluations",
            config=make_config(enabled=True),
            runtime_name=RUNTIME_NAME,
            runtime_endpoint_name="canary",
            env=cdk.Environment(account=ACCOUNT, region=REGION),
        )
        Template.from_stack(stack).has_resource_properties(
            "AWS::BedrockAgentCore::OnlineEvaluationConfig",
            {
                "DataSourceConfig": {
                    "CloudWatchLogs": Match.object_like({"ServiceNames": [f"{RUNTIME_NAME}.canary"]})
                }
            },
        )


class TestEvaluators:
    def test_builtins_and_custom_are_both_attached(self, template):
        configs = template.find_resources("AWS::BedrockAgentCore::OnlineEvaluationConfig")
        evaluators = list(configs.values())[0]["Properties"]["Evaluators"]
        ids = [e["EvaluatorId"] for e in evaluators]
        builtins = [i for i in ids if isinstance(i, str)]
        refs = [i for i in ids if not isinstance(i, str)]

        assert "Builtin.ToolParameterAccuracy" in builtins
        assert refs, "the example custom evaluator should be attached by reference"

    def test_example_custom_evaluator_deployed(self, template):
        template.resource_count_is("AWS::BedrockAgentCore::Evaluator", len(custom_evaluators()))

    def test_custom_evaluator_uses_deterministic_judge(self, template):
        # Grading must be repeatable across runs.
        template.has_resource_properties(
            "AWS::BedrockAgentCore::Evaluator",
            {
                "EvaluatorConfig": Match.object_like(
                    {
                        "LlmAsAJudge": Match.object_like(
                            {
                                "ModelConfig": {
                                    "BedrockEvaluatorModelConfig": Match.object_like(
                                        {"InferenceConfig": Match.object_like({"Temperature": 0})}
                                    )
                                }
                            }
                        )
                    }
                )
            },
        )

    def test_custom_evaluator_declares_a_rating_scale(self, template):
        resources = template.find_resources("AWS::BedrockAgentCore::Evaluator")
        scale = list(resources.values())[0]["Properties"]["EvaluatorConfig"]["LlmAsAJudge"]["RatingScale"]
        assert len(scale["Numerical"]) >= 2

    def test_can_run_builtins_only(self):
        template = synth(make_config(enabled=True, includeExampleCustomEvaluator=False))
        template.resource_count_is("AWS::BedrockAgentCore::Evaluator", 0)
        template.resource_count_is("AWS::BedrockAgentCore::OnlineEvaluationConfig", 1)

    def test_rejects_empty_evaluator_list(self):
        with pytest.raises(ValueError, match="no evaluators are configured"):
            synth(make_config(enabled=True, builtinEvaluators=[], includeExampleCustomEvaluator=False))

    def test_rejects_more_than_ten_evaluators(self):
        too_many = [f"Builtin.Fake{i}" for i in range(11)]
        with pytest.raises(ValueError, match="at most 10 evaluators"):
            synth(make_config(enabled=True, builtinEvaluators=too_many, includeExampleCustomEvaluator=False))


class TestCustomEvaluatorSpecs:
    def test_specs_have_valid_levels(self):
        for spec in custom_evaluators():
            assert spec.level in ("TRACE", "SESSION", "TOOL_CALL")

    def test_names_are_api_safe(self):
        # Hyphens are rejected by the API.
        for spec in custom_evaluators():
            assert "-" not in spec.name

    def test_instructions_reference_only_placeholders_valid_for_level(self):
        allowed = {
            "TRACE": {"context", "assistant_turn"},
            "SESSION": {"context", "available_tools"},
            "TOOL_CALL": {"context", "available_tools", "tool_turn"},
        }
        import re

        for spec in custom_evaluators():
            # CreateEvaluator takes single-brace placeholders and rejects {{name}}.
            assert "{{" not in spec.instructions, f"{spec.name} uses double-brace placeholders"
            used = set(re.findall(r"\{(\w+)\}", spec.instructions))
            assert used, f"{spec.name} must reference at least one placeholder"
            unexpected = used - allowed[spec.level]
            assert not unexpected, f"{spec.name} uses {unexpected} which {spec.level} does not provide"

    def test_spec_is_extensible(self):
        # The documented extension contract: construct a spec without touching the stack.
        spec = CustomEvaluatorSpec(
            construct_id="X",
            name="my_metric",
            description="d",
            level="TRACE",
            instructions="{context} {assistant_turn}",
            scale=[(1, "bad", "b"), (5, "good", "g")],
        )
        assert spec.level == "TRACE"


class TestExecutionRole:
    def test_trusts_only_agentcore_evaluation_resources(self, template):
        template.has_resource_properties(
            "AWS::IAM::Role",
            {
                "AssumeRolePolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {"Principal": {"Service": "bedrock-agentcore.amazonaws.com"}}
                                )
                            ]
                        )
                    }
                )
            },
        )

    def test_can_query_traces_and_invoke_judge(self, template):
        body = str(template.to_json())
        assert "logs:StartQuery" in body
        assert "logs:GetQueryResults" in body
        assert "bedrock:InvokeModel" in body

    def test_result_writes_scoped_to_the_results_log_group(self, template):
        statements = [s for p in template.find_resources("AWS::IAM::Policy").values()
                      for s in p["Properties"]["PolicyDocument"]["Statement"]]
        write = next(s for s in statements if "logs:PutLogEvents" in s["Action"])
        assert all(RESULTS_LOG_GROUP in str(resource) for resource in write["Resource"])
        # Nothing in the template may fall under the prefix the service reserves for its own default group.
        assert "/aws/bedrock-agentcore/evaluations/" not in str(template.to_json())

    def test_cannot_start_queries_against_arbitrary_log_groups(self, template):
        statements = [s for p in template.find_resources("AWS::IAM::Policy").values()
                      for s in p["Properties"]["PolicyDocument"]["Statement"]]
        query = next(s for s in statements if "logs:StartQuery" in s["Action"])
        assert query["Resource"] == {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, f":logs:{REGION}:{ACCOUNT}:log-group:{TRACE_LOG_GROUP}:*"]]}


class TestResults:
    """Judge explanations restate conversations, so results must not be kept forever."""

    def test_results_log_group_has_retention_and_survives_stack_deletion(self, template):
        template.has_resource(
            "AWS::Logs::LogGroup",
            {
                "Properties": {"LogGroupName": RESULTS_LOG_GROUP, "RetentionInDays": 30},
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
            },
        )

    def test_config_writes_results_to_the_stack_log_group(self, template):
        template.has_resource_properties(
            "AWS::BedrockAgentCore::OnlineEvaluationConfig",
            {
                "OutputConfig": {
                    "CloudWatchConfig": {
                        "ResultDestination": "DEDICATED_LOG_GROUP",
                        "LogGroupName": {"Ref": Match.string_like_regexp("ResultsLogGroup")},
                    }
                }
            },
        )

    def test_outputs_results_group_and_custom_evaluator_ids(self, template):
        template.has_output("ResultsLogGroupName", {"Value": {"Ref": Match.string_like_regexp("ResultsLogGroup")}})
        # CDK collapses a one-element Fn::Join to the bare GetAtt, so check the reference itself.
        value = str(template.find_outputs("CustomEvaluatorIds")["CustomEvaluatorIds"]["Value"])
        assert "CatalogueGroundingEvaluator" in value and "EvaluatorId" in value

    def test_no_custom_evaluator_output_for_builtins_only(self):
        builtins_only = synth(make_config(enabled=True, includeExampleCustomEvaluator=False))
        assert builtins_only.find_outputs("CustomEvaluatorIds") == {}
