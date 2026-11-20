"""OnDemandEvaluationDatasetRunner with two fixes for this agent's Runtime traces.

1. Tool-level targets. The SDK treats every span tagged
   `gen_ai.operation.name=execute_tool` as a tool span. On AgentCore Runtime,
   ADOT's MCP instrumentation tags its `mcp tools/call` CLIENT span that way too,
   next to Strands' own `execute_tool` span for the same call. The Evaluate API
   accepts only the Strands span and rejects the whole request ("spanIds that do
   not exist in the provided data"), so ADOT MCP spans are left out of the
   tool-level targets. They are still sent as session spans.
2. Ground truth per scenario. The SDK sends every evaluator to every scenario,
   and the service rejects trajectory evaluators for a scenario without
   `expected_trajectory`. Those requests are skipped for that scenario.

Both override private methods of bedrock-agentcore's runner; the behaviour is
pinned by tests/unit/test_evals.py against the locked SDK version.
"""

from typing import Any, Dict, List, Tuple

from bedrock_agentcore.evaluation import OnDemandEvaluationDatasetRunner

ADOT_MCP_SCOPE = "amazon.opentelemetry.distro.instrumentation.mcp"
TRAJECTORY_EVALUATOR_PREFIX = "Builtin.Trajectory"


class AriaEvaluationRunner(OnDemandEvaluationDatasetRunner):
    def _extract_tool_span_ids(self, spans: list) -> List[str]:
        return [
            span["spanId"]
            for span in spans
            if self._is_tool_span(span) and span.get("spanId") and (span.get("scope") or {}).get("name") != ADOT_MCP_SCOPE
        ]

    def _build_evaluate_requests(
        self, scenario: Any, spans: list, evaluator_ids: List[str], session_id: str = ""
    ) -> List[Tuple[str, Dict[str, Any]]]:
        if not getattr(scenario, "expected_trajectory", None):
            evaluator_ids = [e for e in evaluator_ids if not e.startswith(TRAJECTORY_EVALUATOR_PREFIX)]
        return super()._build_evaluate_requests(
            scenario=scenario, spans=spans, evaluator_ids=evaluator_ids, session_id=session_id
        )
