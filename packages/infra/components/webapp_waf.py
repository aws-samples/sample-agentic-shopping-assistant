"""WAF web ACL for the public ALB."""

from aws_cdk import aws_wafv2 as wafv2, aws_logs as logs
import aws_cdk as cdk
from constructs import Construct

# The rate-based rule's name and CloudWatch metric name (the `Rule` dimension of its metrics).
RATE_LIMIT_RULE = "RequestRateLimit"


def web_acl_metric_name(ns: str) -> str:
    """The web ACL's CloudWatch metric name, which is the `WebACL` dimension of its metrics.

    Per stage, so two stages in one account and Region don't share metrics. The Operations
    stack alarms on it by this name rather than by a construct reference.
    """
    return f"{ns}WebACL"


class WebappWaf(Construct):
    def __init__(self, scope: Construct, construct_id: str, *, rate_limit: int = 100):
        super().__init__(scope, construct_id)

        ns: str = self.node.try_get_context("ns")
        self.web_acl = wafv2.CfnWebACL(
            self,
            "WebACL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            scope="REGIONAL",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=web_acl_metric_name(ns),
                sampled_requests_enabled=False,
            ),
            rules=self._rules(rate_limit),
        )

        group = logs.LogGroup(self, "RequestLogs", log_group_name=f"aws-waf-logs-{cdk.Stack.of(self).stack_name.lower()}",
                              retention=logs.RetentionDays.ONE_MONTH, removal_policy=cdk.RemovalPolicy.RETAIN)
        wafv2.CfnLoggingConfiguration(self, "Logging", resource_arn=self.web_acl.attr_arn,
            log_destination_configs=[group.log_group_arn],
            redacted_fields=[wafv2.CfnLoggingConfiguration.FieldToMatchProperty(single_header={"Name": "authorization"}),
                             wafv2.CfnLoggingConfiguration.FieldToMatchProperty(single_header={"Name": "cookie"})])

    def _rules(self, rate_limit: int) -> list:
        return [
            # api.wafRateLimit requests per client IP in any 5 minutes; past it, WAF blocks that IP
            # until its rate falls. Every shopper behind one NAT counts against the same IP.
            wafv2.CfnWebACL.RuleProperty(
                name=RATE_LIMIT_RULE, priority=10,
                action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                statement=wafv2.CfnWebACL.StatementProperty(rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(limit=rate_limit, aggregate_key_type="IP", evaluation_window_sec=300)),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(cloud_watch_metrics_enabled=True, metric_name=RATE_LIMIT_RULE, sampled_requests_enabled=False),
            ),
            self._managed_rule("AWSManagedRulesAmazonIpReputationList", 20),
            self._managed_rule(
                "AWSManagedRulesCommonRuleSet",
                40,
                # API BodyLimit enforces 32 KiB before JSON parsing; ALB WAF inspects only the first 8 KiB.
                excluded_rules=["SizeRestrictions_BODY"],
            ),
            self._managed_rule("AWSManagedRulesKnownBadInputsRuleSet", 50),
        ]

    @staticmethod
    def _managed_rule(name: str, priority: int, excluded_rules: list[str] | None = None):
        return wafv2.CfnWebACL.RuleProperty(
            name=f"AWS-{name}",
            priority=priority,
            statement=wafv2.CfnWebACL.StatementProperty(
                managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                    name=name,
                    vendor_name="AWS",
                    excluded_rules=[
                        wafv2.CfnWebACL.ExcludedRuleProperty(name=rule) for rule in (excluded_rules or [])
                    ]
                    or None,
                )
            ),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{name}Metric",
                sampled_requests_enabled=False,
            ),
            override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
        )
