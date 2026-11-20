"""AgentCore Evaluations: continuous scoring of live agent traffic.

Reads the Runtime's OpenTelemetry traces from CloudWatch and scores sampled
sessions with LLM-as-a-judge evaluators. Results go to a stack-owned log group
with 30-day retention: without an explicit destination the service creates one
that never expires, and judge explanations restate parts of the conversation.

Prerequisites (verify before enabling):
  * Transaction Search delivery to CloudWatch Logs must be ACTIVE:
        aws xray get-trace-segment-destination
  * The agent must emit OTel traces — the Runtime's ADOT sidecar does this.

Customer extension points:
  * `[evaluations]` in .toml — sampling, and which built-in evaluators to run
  * components/evaluators.py — add domain-specific custom evaluators
"""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_bedrockagentcore as agentcore
from cdk_nag import NagSuppressions
from constructs import Construct

from components.evaluators import build_evaluator, custom_evaluators
from config.loader import Config
from components.model_permissions import model_resources


class EvaluationsStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        runtime_name: str,
        runtime_endpoint_name: str = "DEFAULT",
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        ns: str = self.node.try_get_context("ns")
        settings = config.evaluations
        self.settings = settings
        self.spans_log_group = config.operations.trace_log_group
        if not self.spans_log_group:
            raise ValueError("operations.traceLogGroup is required")

        # OTel service.name for a Runtime-hosted agent is
        # "<agent-runtime-name>.<endpoint-name>". If this does not match, the
        # evaluation silently scores nothing.
        service_name = f"{runtime_name}.{runtime_endpoint_name}"

        # The /aws/bedrock-agentcore/evaluations/ prefix is reserved for the
        # service's own default group, so the stack names its group by namespace.
        self.results_log_group_name = f"/{ns.lower()}/evaluations/results"
        self.results_log_group = logs.LogGroup(
            self,
            "ResultsLogGroup",
            log_group_name=self.results_log_group_name,
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        execution_role = self._create_execution_role()

        evaluator_ids = list(settings.builtin_evaluators)

        self.custom_evaluators: list[agentcore.CfnEvaluator] = []
        if settings.include_example_custom_evaluator:
            for spec in custom_evaluators():
                evaluator = build_evaluator(self, spec, settings.judge_model_id)
                self.custom_evaluators.append(evaluator)
                evaluator_ids.append(evaluator.attr_evaluator_id)

        if not evaluator_ids:
            raise ValueError(
                "evaluations.enabled is true but no evaluators are configured; "
                "add builtinEvaluators in .toml or a custom evaluator in components/evaluators.py"
            )
        if len(evaluator_ids) > 10:
            raise ValueError(
                f"AgentCore Evaluations allows at most 10 evaluators per config, got {len(evaluator_ids)}"
            )

        self.config_resource = agentcore.CfnOnlineEvaluationConfig(
            self,
            "OnlineEvaluationConfig",
            # Underscores only — hyphens are rejected.
            online_evaluation_config_name=f"{ns.lower()}_online_eval",
            description=f"Continuous evaluation of the {ns} shopping assistant",
            data_source_config=agentcore.CfnOnlineEvaluationConfig.DataSourceConfigProperty(
                cloud_watch_logs=agentcore.CfnOnlineEvaluationConfig.CloudWatchLogsInputConfigProperty(
                    log_group_names=[self.spans_log_group],
                    service_names=[service_name],
                )
            ),
            rule=agentcore.CfnOnlineEvaluationConfig.RuleProperty(
                sampling_config=agentcore.CfnOnlineEvaluationConfig.SamplingConfigProperty(
                    sampling_percentage=settings.sampling_percentage
                ),
                session_config=agentcore.CfnOnlineEvaluationConfig.SessionConfigProperty(
                    session_timeout_minutes=settings.session_timeout_minutes
                ),
            ),
            evaluators=[
                agentcore.CfnOnlineEvaluationConfig.EvaluatorReferenceProperty(evaluator_id=eid)
                for eid in evaluator_ids
            ],
            evaluation_execution_role_arn=execution_role.role_arn,
            execution_status="ENABLED",
        )
        # aws-cdk-lib's L1 has no output_config argument yet; the CloudFormation
        # resource supports OutputConfig, so set it directly.
        self.config_resource.add_property_override(
            "OutputConfig",
            {
                "CloudWatchConfig": {
                    "ResultDestination": "DEDICATED_LOG_GROUP",
                    "LogGroupName": self.results_log_group.log_group_name,
                }
            },
        )
        for evaluator in self.custom_evaluators:
            self.config_resource.add_resource_dependency(evaluator)
        self.config_resource.node.add_dependency(execution_role)

        self._suppress_nag()

        cdk.CfnOutput(self, "OnlineEvaluationConfigId", value=self.config_resource.ref)
        cdk.CfnOutput(self, "EvaluatedServiceName", value=service_name)
        cdk.CfnOutput(self, "SamplingPercentage", value=str(settings.sampling_percentage))
        cdk.CfnOutput(self, "Evaluators", value=", ".join(settings.builtin_evaluators))
        cdk.CfnOutput(self, "ResultsLogGroupName", value=self.results_log_group.log_group_name)
        if self.custom_evaluators:
            # Pass these to `python -m evals.run --evaluator` to use them in dataset runs.
            cdk.CfnOutput(
                self,
                "CustomEvaluatorIds",
                value=cdk.Fn.join(",", [evaluator.attr_evaluator_id for evaluator in self.custom_evaluators]),
            )

    def _create_execution_role(self) -> iam.Role:
        role = iam.Role(
            self,
            "EvaluationExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {
                        "aws:SourceAccount": self.account,
                        "aws:ResourceAccount": self.account,
                    },
                    "ArnLike": {
                        "aws:SourceArn": [
                            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:evaluator/*",
                            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:online-evaluation-config/*",
                        ]
                    },
                },
            ),
        )

        # Account-wide discovery and query-result lookup do not accept resource ARNs.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:DescribeLogGroups", "logs:GetQueryResults"],
                resources=["*"],
            )
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["logs:StartQuery"],
            resources=[f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:{self.spans_log_group}:*"],
        ))
        # Write evaluation results to the stack's results log group only. The literal
        # name keeps the grant readable in the synthesized template.
        results_group = self.results_log_group_name
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
                resources=[
                    f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:{results_group}",
                    f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:{results_group}:*",
                ],
            )
        )
        # Index the spans log group so traces are queryable.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:DescribeIndexPolicies", "logs:PutIndexPolicy"],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.spans_log_group}",
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.spans_log_group}:*",
                ],
            )
        )
        # Invoke the judge model.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=model_resources(self.settings.judge_model_id, self.region, self.account, self.partition),
            )
        )
        return role

    def _suppress_nag(self) -> None:
        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "DescribeLogGroups/GetQueryResults require Resource *; StartQuery is scoped "
                        "to the configured source log group. Only the configured judge model can be "
                        "invoked across regions; result writes are scoped to the stack's results log group"
                    ),
                }
            ],
        )
