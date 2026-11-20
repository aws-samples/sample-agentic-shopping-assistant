"""Run an AgentCore dataset against a deployed ARIA Runtime; AgentCore Evaluations scores it.

    cd packages/agent
    export ARIA_ACCESS_TOKEN="$(cat <token file from evals.login>)"
    uv run python -m evals.run evals/cases/example.json \\
        --runtime-arn arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id> --invoke \\
        --min Builtin.GoalSuccessRate=0.8

Each scenario becomes one Runtime session. The runner waits for CloudWatch
ingestion, collects each session's spans, and sends them to the Evaluate API
with the dataset's ground truth. See evals/README.md for prerequisites.

The run exits non-zero when a scenario fails, an evaluator errors, or an
evaluator given with --min scores a mean below its minimum.
"""

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Optional

from bedrock_agentcore.evaluation import (
    CloudWatchAgentSpanCollector,
    EvaluationRunConfig,
    EvaluatorConfig,
    FileDatasetProvider,
)

from evals.report import summarize, write_results
from evals.runner import AriaEvaluationRunner
from evals.runtime_invoker import (
    RuntimeInvoker,
    SessionMappedCollector,
    https_sender,
    runtime_invocation_url,
    runtime_log_group,
    runtime_region,
    token_owner,
)

# The online evaluation defaults (packages/infra/config/loader.py), plus the
# ground-truth evaluators that read the dataset's expected fields.
DEFAULT_EVALUATORS = (
    "Builtin.ToolSelectionAccuracy",
    "Builtin.ToolParameterAccuracy",
    "Builtin.GoalSuccessRate",  # scenario `assertions`
    "Builtin.Faithfulness",
    "Builtin.InstructionFollowing",
    "Builtin.TrajectoryInOrderMatch",  # scenario `expected_trajectory`
    "Builtin.Correctness",  # turn `expected_response`
)


def minimum(value: str) -> tuple[str, float]:
    """Parse EVALUATOR=SCORE, for example Builtin.GoalSuccessRate=0.8."""
    evaluator, sep, score = value.rpartition("=")
    if not sep or not evaluator:
        raise argparse.ArgumentTypeError(f"expected EVALUATOR=SCORE, got {value!r}")
    try:
        number = float(score)
    except ValueError:
        raise argparse.ArgumentTypeError(f"the minimum for {evaluator} is not a number: {score!r}") from None
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"the minimum for {evaluator} must be a finite number")
    return evaluator, number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("dataset", type=Path, help="AgentCore dataset file (.json or .jsonl)")
    parser.add_argument(
        "--runtime-arn",
        default=os.environ.get("ARIA_RUNTIME_ARN"),
        help="AgentRuntime stack output RuntimeArn (default: $ARIA_RUNTIME_ARN)",
    )
    parser.add_argument("--qualifier", default="DEFAULT", help="Runtime endpoint (default DEFAULT)")
    parser.add_argument(
        "--log-group",
        help="Runtime log group (default: derived from the ARN and qualifier). aws/spans is queried as well.",
    )
    parser.add_argument(
        "--tenant-claim",
        default="",
        help="The Runtime's OIDC tenant claim, if the stage uses one (auth.tenantClaim)",
    )
    parser.add_argument(
        "--evaluator",
        action="append",
        dest="evaluators",
        metavar="ID",
        help="Built-in name or custom evaluator ID. Repeat to add more; replaces the defaults.",
    )
    parser.add_argument(
        "--min",
        action="append",
        dest="minimums",
        type=minimum,
        default=[],
        metavar="EVALUATOR=SCORE",
        help="Exit non-zero if the evaluator's mean score over the run is below SCORE (or it scored nothing). Repeatable.",
    )
    parser.add_argument("--concurrency", type=int, default=5, help="Scenarios invoked in parallel (default 5)")
    parser.add_argument(
        "--ingestion-delay",
        type=int,
        default=180,
        help="Seconds to wait for CloudWatch ingestion before collecting spans (default 180)",
    )
    parser.add_argument(
        "--invoke",
        action="store_true",
        help="Authorize billed agent invocations, their Memory writes, and billed judge-model calls",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.invoke:
        parser.error("Explicit --invoke is required. This invokes the deployed agent and the Evaluate API.")
    if not args.runtime_arn:
        parser.error("Set --runtime-arn or ARIA_RUNTIME_ARN")
    token = os.environ.get("ARIA_ACCESS_TOKEN", "")
    if not token:
        parser.error("Set ARIA_ACCESS_TOKEN (see python -m evals.login)")
    try:
        region = runtime_region(args.runtime_arn)
        owner = token_owner(token, args.tenant_claim)
    except ValueError as exc:
        parser.error(str(exc))
    evaluator_ids = list(args.evaluators or DEFAULT_EVALUATORS)
    minimums = dict(args.minimums)
    unknown = sorted(set(minimums) - set(evaluator_ids))
    if unknown:
        # Checked before anything is invoked: a minimum on an evaluator that never runs can't pass.
        parser.error(f"--min names evaluators this run does not use: {', '.join(unknown)}")

    dataset = FileDatasetProvider(str(args.dataset)).get_dataset()
    invoker = RuntimeInvoker(https_sender(runtime_invocation_url(args.runtime_arn, args.qualifier), token), owner)
    log_group = args.log_group or runtime_log_group(args.runtime_arn, args.qualifier)
    collector = SessionMappedCollector(CloudWatchAgentSpanCollector(log_group_name=log_group, region=region), invoker)
    config = EvaluationRunConfig(
        evaluator_config=EvaluatorConfig(evaluator_ids=evaluator_ids),
        evaluation_delay_seconds=args.ingestion_delay,
        max_concurrent_scenarios=args.concurrency,
    )

    result = AriaEvaluationRunner(region=region).run(
        config=config, dataset=dataset, agent_invoker=invoker, span_collector=collector
    )
    print(f"Results: {write_results(result, invoker.sessions)}")
    return summarize(result, minimums)


if __name__ == "__main__":
    sys.exit(main())
