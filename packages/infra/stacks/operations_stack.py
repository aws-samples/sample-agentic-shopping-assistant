"""Stage-owned alarms and dashboards; no account-wide telemetry settings are changed."""
from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cw, aws_cloudwatch_actions as actions, aws_elasticloadbalancingv2 as elbv2, aws_logs as logs, aws_sns as sns
from constructs import Construct

from components.webapp_waf import RATE_LIMIT_RULE, web_acl_metric_name
from config.loader import Config
from stacks.gateway_stack import INTERCEPTOR_DENIALS_METRIC, metric_namespace

# Log events the alarms count, by their structlog `event` field. The agent and the API emit
# them; a renamed event silently zeroes its alarm, so tests pin these names.
API_INVOCATION_FAILED = "runtime invocation failed"
AGENT_TURN_FAILED = "agent_turn_failed"
SESSION_BUILD_FAILED = "session_build_failed"
TOOL_DENIED = "tool_denied"
SEARCH_FAILED = "product_search_failed"
SEARCH_EMBEDDING_FAILED = "product_search_embedding_failed"
# The `code` of an agent_turn_failed event that Bedrock throttled.
THROTTLED = "throttled"

_RUNBOOK = "See docs/guides/operations.md"


@dataclass(frozen=True)
class _Check:
    name: str
    metric: cw.IMetric
    threshold: float
    # Two 5-minute periods in a row, unless an outage signal needs to alarm on the first.
    periods: int = 2
    datapoints: int | None = None
    comparison: cw.ComparisonOperator = cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
    missing: cw.TreatMissingData = cw.TreatMissingData.NOT_BREACHING
    description: str = f"{_RUNBOOK}; check dependency errors and recent release before rollback"


class OperationsStack(cdk.Stack):
    @staticmethod
    def _lambda_metric(function_name: str, metric_name: str) -> cw.Metric:
        return cw.Metric(namespace="AWS/Lambda", metric_name=metric_name, dimensions_map={"FunctionName": function_name},
                         statistic="Sum", period=cdk.Duration.minutes(5))

    def _count(self, construct_id: str, log_group: logs.ILogGroup, pattern: logs.IFilterPattern, metric_name: str) -> cw.Metric:
        """How many log events matched in each period.

        Sum, not the default Average: the filter emits 1 for a match and 0 for every other event,
        so an average never exceeds 1 and an alarm on it can't reach a count threshold.
        """
        return logs.MetricFilter(self, construct_id, log_group=log_group, filter_pattern=pattern,
            metric_namespace=self._namespace, metric_name=metric_name, metric_value="1", default_value=0).metric(statistic="Sum")

    def __init__(self, scope: Construct, construct_id: str, *, config: Config, api, common, runtime, gateway=None, search=None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        topic = sns.Topic.from_topic_arn(self, "Notifications", config.operations.alarm_topic_arn) if config.operations.alarm_topic_arn else None
        self._namespace = namespace = metric_namespace(config.ns)

        def event(name: str) -> logs.IFilterPattern:
            return logs.FilterPattern.string_value("$.event", "=", name)

        api_failures = self._count("ApiFailures", api.log_group, event(API_INVOCATION_FAILED), "ApiInvocationFailures")
        # Only failures the service owns: the agent also logs agent_turn_failed, with retryable false,
        # for a caller's invalid request, another shopper's session or a spent turn budget.
        runtime_failures = self._count("RuntimeFailures", runtime.log_group,
            logs.FilterPattern.all(event(AGENT_TURN_FAILED), logs.FilterPattern.boolean_value("$.retryable", True)), "AgentFailures")
        latency = logs.MetricFilter(self, "Latency", log_group=api.log_group,
            filter_pattern=logs.FilterPattern.exists("$.duration_ms"),
            metric_namespace=namespace, metric_name="TurnDurationMs", metric_value="$.duration_ms").metric(statistic="p95")
        token_metrics = []
        for field in ("input_tokens", "output_tokens"):
            token_metrics.append(logs.MetricFilter(self, field, log_group=runtime.log_group,
                filter_pattern=logs.FilterPattern.exists(f"$.{field}"), metric_namespace=namespace,
                metric_name=field, metric_value=f"$.{field}").metric(statistic="Sum"))
        # Outage signals the turn-failure count misses: a session that can't be built fails every
        # turn in it, and throttling or tool denials can rise while other turns still complete.
        session_builds = self._count("SessionBuildEvents", runtime.log_group, event(SESSION_BUILD_FAILED), "SessionBuildFailures")
        throttles = self._count("ThrottleEvents", runtime.log_group,
            logs.FilterPattern.all(event(AGENT_TURN_FAILED), logs.FilterPattern.string_value("$.code", "=", THROTTLED)), "ModelThrottles")
        # A tool the agent isn't allowed or oversized arguments, not a spent call budget: a runaway turn
        # logs budget denials too, and those show up as tool_budget_ended_turn instead.
        tool_denials = self._count("ToolDenialEvents", runtime.log_group,
            logs.FilterPattern.all(event(TOOL_DENIED), logs.FilterPattern.string_value("$.reason", "!=", "budget")), "ToolDenials")
        # The catalog backend failing: the shopper gets an error instead of products, but the turn
        # completes, so AgentErrors misses it.
        search_failures = self._count("SearchFailureEvents", runtime.log_group, event(SEARCH_FAILED), "SearchFailures")
        search_metrics = [search_failures]

        load_balancer = common.load_balancer.metrics
        target_group = api.target_group.metrics
        elb_errors = load_balancer.http_code_elb(elbv2.HttpCodeElb.ELB_5XX_COUNT, statistic="Sum")
        target_errors = load_balancer.http_code_target(elbv2.HttpCodeTarget.TARGET_5XX_COUNT, statistic="Sum")
        # A gauge, read each minute: the fewest healthy API tasks in that minute.
        healthy_hosts = target_group.healthy_host_count(statistic="Minimum", period=cdk.Duration.minutes(1))
        # By the web ACL's fixed metric name, not a construct reference into CommonApp. WAF reports
        # the metric only when it blocks, so missing data is the quiet case.
        rate_limited = cw.Metric(namespace="AWS/WAFV2", metric_name="BlockedRequests", statistic="Sum", period=cdk.Duration.minutes(5),
                                 dimensions_map={"WebACL": web_acl_metric_name(config.ns), "Rule": RATE_LIMIT_RULE, "Region": self.region})

        metrics = [api_failures, runtime_failures, latency, *token_metrics]
        checks = [
            _Check("ApiInvocationErrors", api_failures, 3),
            _Check("AgentErrors", runtime_failures, 3),
            _Check("UnhealthyApi", target_group.unhealthy_host_count(statistic="Maximum"), 1),
            _Check("AlbServerErrors", target_errors, 5),
            _Check("SessionBuildFailures", session_builds, 2, periods=1, description=(
                "The agent failed to build a session twice in 5 minutes, and every turn in such a session fails. Read the "
                f"{SESSION_BUILD_FAILED} events in the Runtime log group: Memory, Gateway, model or identity setup. {_RUNBOOK}")),
            _Check("ModelThrottles", throttles, 1, periods=1, description=(
                "Bedrock throttled the agent's model and a turn failed. Check the tokens- and requests-per-minute quotas "
                f"of the configured model's inference profile. {_RUNBOOK}")),
            _Check("ToolDenials", tool_denials, 5, periods=1, description=(
                "The agent was refused a tool call 5 times in 5 minutes, a prompt-injection signal. Read the "
                f"{TOOL_DENIED} events in the Runtime log group and the turns around them. {_RUNBOOK}")),
            _Check("AlbLoadBalancerErrors", elb_errors, 5, periods=1, description=(
                "The load balancer itself returned 5XX (502, 503 or 504) 5 times in 5 minutes: no healthy API task, tasks "
                f"refusing connections, or turns past the 150 s idle timeout. {_RUNBOOK}")),
            _Check("ApiHealthyHosts", healthy_hosts, config.api.min_tasks, periods=5, datapoints=3,
                   comparison=cw.ComparisonOperator.LESS_THAN_THRESHOLD, missing=cw.TreatMissingData.BREACHING, description=(
                       f"Fewer than api.minTasks ({config.api.min_tasks}) healthy API tasks in 3 of the last 5 minutes. Missing "
                       f"data counts as breaching, so a service with no tasks at all alarms too. {_RUNBOOK}")),
            _Check("WafRateLimitBlocks", rate_limited, 1, periods=1, description=(
                f"WAF blocked a client IP that sent more than api.wafRateLimit ({config.api.waf_rate_limit}) requests in 5 "
                "minutes. Shoppers behind one NAT share an IP: if the blocked IPs in the WAF log group are legitimate, raise "
                f"api.wafRateLimit. {_RUNBOOK}")),
        ]
        if gateway:
            # Metrics by the functions' fixed names, not their constructs: a construct reference
            # becomes a cross-stack export, which blocks the Gateway stack from ever removing
            # that function (CloudFormation refuses to delete an export that is in use).
            for name, function_name in gateway.lambda_function_names.items():
                checks.extend([_Check(f"{name}Errors", self._lambda_metric(function_name, "Errors"), 3),
                               _Check(f"{name}Throttles", self._lambda_metric(function_name, "Throttles"), 1)])
            # Counted by the Gateway stack's metric filter. The agent's own calls always pass the
            # interceptor, so one refusal means a configuration error or the agent's token in use
            # elsewhere.
            denials = cw.Metric(namespace=namespace, metric_name=INTERCEPTOR_DENIALS_METRIC, statistic="Sum",
                                period=cdk.Duration.minutes(5))
            checks.append(_Check("InterceptorDenials", denials, 1, periods=1, description=(
                "The Gateway interceptor refused a call: a failed actor or token check, or a method the agent doesn't "
                "use. The reasons are in the interceptor's log group, on lines starting \"interceptor: target invocation "
                f"denied\". {_RUNBOOK}")))
        checks.append(_Check("SearchFailures", search_failures, 3, periods=1, description=(
            "Product search failed 3 times in 5 minutes: the catalog backend, its alias or its credential. Read the "
            f"{SEARCH_FAILED} events in the Runtime log group for the provider and error type. {_RUNBOOK}")))
        if search:
            # A failed query embedding falls back to keyword search, so searches still answer and
            # nothing else reports it.
            embedding_failures = self._count("SearchEmbeddingFailureEvents", runtime.log_group, event(SEARCH_EMBEDDING_FAILED),
                                             "SearchEmbeddingFailures")
            search_metrics.append(embedding_failures)
            checks.append(_Check("SearchEmbeddingFailures", embedding_failures, 3, periods=1, description=(
                "The search tool couldn't embed the query 3 times in 5 minutes and searched by keyword alone: Bedrock "
                f"access to the embedding model, its quota or the network path. Read the {SEARCH_EMBEDDING_FAILED} events "
                f"in the Runtime log group for the error type. {_RUNBOOK}")))
            domain = search.cluster.domain
            checks.extend([_Check("SearchRed", domain.metric_cluster_status_red(), 1),
                           _Check("SearchDiskLow", cw.MathExpression(expression="IF(free < 10240, 1, 0)", using_metrics={"free": domain.metric_free_storage_space(statistic="Minimum")}), 1),
                           _Check("SearchSnapshotFailure", domain.metric("AutomatedSnapshotFailure", statistic="Maximum"), 1)])
        self.alarms = []
        for check in checks:
            alarm = cw.Alarm(self, check.name, alarm_name=f"{config.ns}-{check.name}", metric=check.metric,
                             threshold=check.threshold, evaluation_periods=check.periods,
                             datapoints_to_alarm=check.datapoints or check.periods, comparison_operator=check.comparison,
                             treat_missing_data=check.missing, alarm_description=check.description)
            if topic:
                alarm.add_alarm_action(actions.SnsAction(topic))
                alarm.add_ok_action(actions.SnsAction(topic))
            self.alarms.append(alarm)
        dashboard = cw.Dashboard(self, "Dashboard", dashboard_name=f"{config.ns}-operations")
        dashboard.add_widgets(cw.GraphWidget(title="Conversation failures and latency", left=metrics[:2], right=[latency], width=24))
        dashboard.add_widgets(cw.GraphWidget(title="Session build failures, model throttles and tool denials",
                                             left=[session_builds, throttles, tool_denials], width=24))
        dashboard.add_widgets(cw.GraphWidget(title="Load balancer 5XX and healthy API tasks", left=[elb_errors, target_errors],
                                             right=[healthy_hosts], width=12),
                              cw.GraphWidget(title="WAF rate-limit blocks", left=[rate_limited], width=12))
        dashboard.add_widgets(cw.GraphWidget(title="Model token usage", left=token_metrics, width=12),
                              cw.GraphWidget(title="Product search failures", left=search_metrics, width=12))
        dashboard.add_widgets(cw.AlarmStatusWidget(title="Service alarms", alarms=self.alarms, width=24))
