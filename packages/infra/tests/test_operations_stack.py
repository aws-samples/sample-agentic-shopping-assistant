"""Operations alarms: they must catch whole outages, and import nothing from the Gateway stack.

A construct reference across stacks becomes a CloudFormation export, and CloudFormation
refuses to delete an export that another stack imports. Alarms that referenced the Gateway's
functions would stop the Gateway from ever removing one, so the alarms name the functions instead.
"""

import importlib.util
import logging
import re
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from app import build_app
from config.loader import load_config
from stacks.gateway_stack import INTERCEPTOR_DENIAL_LOG, INTERCEPTOR_DENIALS_METRIC
from stacks.operations_stack import (
    AGENT_TURN_FAILED, API_INVOCATION_FAILED, SEARCH_EMBEDDING_FAILED, SEARCH_FAILED, SESSION_BUILD_FAILED, THROTTLED, TOOL_DENIED,
)

DEV = Path(__file__).parents[1] / "config/dev.toml"
PROD = Path(__file__).parents[1] / "config/prod.toml"
PACKAGES = Path(__file__).resolve().parents[2]


def imports(node) -> list:
    if isinstance(node, dict):
        found = [node["Fn::ImportValue"]] if "Fn::ImportValue" in node else []
        return found + [item for value in node.values() for item in imports(value)]
    if isinstance(node, list):
        return [item for value in node for item in imports(value)]
    return []


def test_alarms_name_the_gateway_functions_without_importing_them():
    config = load_config(DEV)
    app = build_app(config, offline=True)
    app.synth()
    stacks = {s.stack_name: s for s in app.node.children if isinstance(s, cdk.Stack)}
    operations = Template.from_stack(stacks[f"{config.ns}Operations"]).to_json()

    assert not [value for value in imports(operations) if f"{config.ns}Gateway" in str(value)]
    alarmed = {dimension["Value"] for resource in operations["Resources"].values() if resource["Type"] == "AWS::CloudWatch::Alarm"
               for dimension in resource["Properties"].get("Dimensions", []) if dimension["Name"] == "FunctionName"}
    gateway = Template.from_stack(stacks[f"{config.ns}Gateway"]).to_json()
    deployed = {resource["Properties"]["FunctionName"] for resource in gateway["Resources"].values()
                if resource["Type"] == "AWS::Lambda::Function" and "FunctionName" in resource["Properties"]}
    assert alarmed and alarmed <= deployed


# ── Alarms that catch whole outages ─────────────────────────────


def synthesized(path: Path) -> dict[str, dict]:
    app = build_app(load_config(path), offline=True)
    app.synth()
    return {s.stack_name: Template.from_stack(s).to_json() for s in app.node.children if isinstance(s, cdk.Stack)}


@pytest.fixture(scope="module")
def dev():
    return synthesized(DEV)


@pytest.fixture(scope="module")
def prod():
    return synthesized(PROD)


def of_type(template: dict, kind: str) -> list[dict]:
    return [r["Properties"] for r in template["Resources"].values() if r["Type"] == kind]


def alarms(template: dict) -> dict[str, dict]:
    """Alarms by name without the namespace prefix, e.g. "ToolDenials"."""
    return {p["AlarmName"].split("-", 1)[1]: p for p in of_type(template, "AWS::CloudWatch::Alarm")}


def test_metric_filters_count_exactly_the_events_the_services_emit(dev):
    # The agent and the API log these structlog events; a renamed event silently zeroes its alarm.
    patterns = [p["FilterPattern"] for p in of_type(dev["ARIADevOperations"], "AWS::Logs::MetricFilter")]
    events = {name for pattern in patterns for name in re.findall(r'\$\.event = "([^"]+)"', pattern)}
    assert events == {"runtime invocation failed", "agent_turn_failed", "session_build_failed", "tool_denied", "product_search_failed"}
    assert events == {API_INVOCATION_FAILED, AGENT_TURN_FAILED, SESSION_BUILD_FAILED, TOOL_DENIED, SEARCH_FAILED}
    assert '{ ($.event = "agent_turn_failed") && ($.code = "throttled") }' in patterns
    # AgentErrors counts the failures the service owns, not a caller's invalid or forbidden turn.
    assert '{ ($.event = "agent_turn_failed") && ($.retryable IS TRUE) }' in patterns
    # ToolDenials is a prompt-injection signal: a spent call budget doesn't count.
    assert '{ ($.event = "tool_denied") && ($.reason != "budget") }' in patterns


def logged_in(package: str, pattern: str) -> list[str]:
    """The service package's source files that match: where it emits the event."""
    return [path.name for path in sorted((PACKAGES / package / "src").rglob("*.py")) if re.search(pattern, path.read_text())]


@pytest.mark.parametrize("package,event", [("api", API_INVOCATION_FAILED), ("agent", AGENT_TURN_FAILED),
                                           ("agent", SESSION_BUILD_FAILED), ("agent", TOOL_DENIED), ("agent", SEARCH_FAILED),
                                           ("agent", SEARCH_EMBEDDING_FAILED)])
def test_each_counted_event_is_logged_by_its_service(package, event):
    # Read from the service's own source: renaming the event there would leave its alarm at zero.
    assert logged_in(package, rf'\b(?:logger\.\w+|log)\(\s*"{re.escape(event)}"'), f"packages/{package} never logs {event!r}"


def test_the_throttle_code_is_the_one_the_agent_reports():
    assert logged_in("agent", rf'\.failed\(\s*"{re.escape(THROTTLED)}"')


def test_the_denial_metric_counts_the_line_the_interceptor_logs(dev, monkeypatch, caplog):
    # Run the interceptor itself: a request it refuses must write the line the filter matches.
    spec = importlib.util.spec_from_file_location("interceptor_handler", PACKAGES / "mcp-servers/gateway-interceptor/src/handler.py")
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    monkeypatch.delenv("GATEWAY_AUTH_FLOW", raising=False)
    monkeypatch.delenv("AGENT_CLIENT_ID", raising=False)
    with caplog.at_level(logging.WARNING):
        handler.lambda_handler({"mcp": {"gatewayRequest": {"body": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}}}}, None)
    assert any(record.getMessage().startswith(f"{INTERCEPTOR_DENIAL_LOG}: ") for record in caplog.records)

    (metric_filter,) = [f for f in of_type(dev["ARIADevGateway"], "AWS::Logs::MetricFilter")
                        if f["MetricTransformations"][0]["MetricName"] == INTERCEPTOR_DENIALS_METRIC]
    assert metric_filter["FilterPattern"] == f'"{INTERCEPTOR_DENIAL_LOG}"'
    alarm = alarms(dev["ARIADevOperations"])["InterceptorDenials"]
    assert alarm["MetricName"] == INTERCEPTOR_DENIALS_METRIC
    assert alarm["Namespace"] == metric_filter["MetricTransformations"][0]["MetricNamespace"]
    assert (alarm["Threshold"], alarm["EvaluationPeriods"], alarm["Statistic"]) == (1, 1, "Sum")


def test_log_counts_are_summed(dev):
    # Each filter emits 1 per match and 0 per other event: an Average never reaches a count threshold.
    found = alarms(dev["ARIADevOperations"])
    for name in ("ApiInvocationErrors", "AgentErrors", "SessionBuildFailures", "ModelThrottles", "ToolDenials", "SearchFailures"):
        assert found[name]["Statistic"] == "Sum", name


@pytest.mark.parametrize("name,metric,threshold", [
    ("SessionBuildFailures", "SessionBuildFailures", 2),
    ("ModelThrottles", "ModelThrottles", 1),
    ("ToolDenials", "ToolDenials", 5),
    ("SearchFailures", "SearchFailures", 3),
    ("AlbLoadBalancerErrors", "HTTPCode_ELB_5XX_Count", 5),
    ("WafRateLimitBlocks", "BlockedRequests", 1),
])
def test_outage_signals_alarm_within_one_five_minute_period(dev, name, metric, threshold):
    alarm = alarms(dev["ARIADevOperations"])[name]
    assert (alarm["MetricName"], alarm["Statistic"], alarm["Period"]) == (metric, "Sum", 300)
    assert (alarm["Threshold"], alarm["EvaluationPeriods"], alarm["DatapointsToAlarm"]) == (threshold, 1, 1)
    assert (alarm["ComparisonOperator"], alarm["TreatMissingData"]) == ("GreaterThanOrEqualToThreshold", "notBreaching")


@pytest.mark.parametrize("stage,min_tasks", [("dev", 1), ("prod", 2)])
def test_too_few_healthy_tasks_alarms_even_with_no_data(dev, prod, stage, min_tasks):
    templates = {"dev": dev, "prod": prod}[stage]
    operations = next(body for name, body in templates.items() if name.endswith("Operations"))
    alarm = alarms(operations)["ApiHealthyHosts"]
    assert (alarm["MetricName"], alarm["Statistic"], alarm["Threshold"]) == ("HealthyHostCount", "Minimum", min_tasks)
    assert (alarm["ComparisonOperator"], alarm["TreatMissingData"]) == ("LessThanThreshold", "breaching")
    assert {d["Name"] for d in alarm["Dimensions"]} == {"LoadBalancer", "TargetGroup"}


def test_waf_alarm_watches_this_stages_rate_based_rule(dev):
    (acl,) = of_type(dev["ARIADevCommonApp"], "AWS::WAFv2::WebACL")
    (rule,) = [r for r in acl["Rules"] if "RateBasedStatement" in r["Statement"]]
    alarm = alarms(dev["ARIADevOperations"])["WafRateLimitBlocks"]
    assert alarm["Namespace"] == "AWS/WAFV2"
    # WAF names its dimensions by metric name, so these must match the web ACL's visibility config.
    assert {d["Name"]: d["Value"] for d in alarm["Dimensions"]} == {
        "WebACL": acl["VisibilityConfig"]["MetricName"], "Rule": rule["VisibilityConfig"]["MetricName"], "Region": "ap-northeast-2"}
    assert acl["VisibilityConfig"]["MetricName"] == "ARIADevWebACL"


def test_prod_alarms_all_notify_the_topic(prod):
    found = alarms(prod["ARIAProdOperations"])
    assert {"SessionBuildFailures", "ModelThrottles", "ToolDenials", "AlbLoadBalancerErrors", "ApiHealthyHosts", "WafRateLimitBlocks",
            "ApiInvocationErrors", "AgentErrors", "UnhealthyApi", "AlbServerErrors"} <= set(found)
    assert all(alarm.get("AlarmActions") and alarm.get("OKActions") for alarm in found.values())


def test_dashboard_shows_the_outage_signals(dev):
    (dashboard,) = of_type(dev["ARIADevOperations"], "AWS::CloudWatch::Dashboard")
    body = str(dashboard["DashboardBody"])
    for metric in ("SessionBuildFailures", "ModelThrottles", "ToolDenials", "HTTPCode_ELB_5XX_Count", "HealthyHostCount", "BlockedRequests",
                   "SearchFailures"):
        assert metric in body, metric
