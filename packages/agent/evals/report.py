"""Print and save evaluation results."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Mapping, Optional

from bedrock_agentcore.evaluation import EvaluationResult

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def write_results(result: EvaluationResult, sessions: dict[str, str], results_dir: Path = RESULTS_DIR) -> Path:
    """Save the raw Evaluate API responses (judge explanations included).

    `sessions` maps each scenario's runner session ID to the Runtime session ID,
    which is the ID to search for in CloudWatch.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"{stamp}.json"
    path.write_text(json.dumps({"runtimeSessions": sessions, **result.model_dump(mode="json")}, ensure_ascii=False, indent=2))
    return path


def summarize(result: EvaluationResult, minimums: Optional[Mapping[str, float]] = None) -> int:
    """Print one line per scenario and evaluator, and return a process exit code.

    Non-zero when a scenario failed to run, an evaluator returned an error, or an
    evaluator in `minimums` scored a mean below its minimum. The mean is taken
    over every score the evaluator returned in the run, and an evaluator with a
    minimum but no scores fails it: "not scored" (for example, a tool-level
    evaluator on a scenario that called no tools) is missing coverage, not a
    pass. "ignored" lists ground-truth fields the service did not use; check the
    dataset if they appear.
    """
    failed = False
    scores: dict[str, list[float]] = defaultdict(list)
    for scenario in result.scenario_results:
        if scenario.status != "COMPLETED":
            failed = True
            print(f"{scenario.scenario_id}  {scenario.status}  {scenario.error}")
            continue
        for evaluator in scenario.evaluator_results:
            errors = [r for r in evaluator.results if "errorCode" in r]
            values = [r["value"] for r in evaluator.results if isinstance(r.get("value"), (int, float))]
            scores[evaluator.evaluator_id].extend(values)
            labels = sorted({str(r["label"]) for r in evaluator.results if r.get("label") is not None})
            ignored = sorted({f for r in evaluator.results for f in r.get("ignoredReferenceInputFields") or []})
            prefix = f"{scenario.scenario_id}  {evaluator.evaluator_id}"
            if errors:
                failed = True
                print(f"{prefix}  ERROR  " + "; ".join(f"{e['errorCode']}: {e.get('errorMessage', '')}" for e in errors))
            elif values:
                print(f"{prefix}  {mean(values):.2f}  {', '.join(labels)}")
            else:
                print(f"{prefix}  not scored")
            if ignored:
                print(f"{prefix}  ignored: {', '.join(ignored)}")
    for evaluator_id, minimum in (minimums or {}).items():
        values = scores.get(evaluator_id)
        if not values:
            failed = True
            print(f"{evaluator_id}  FAILED  no scores; minimum {minimum:.2f} not met")
        elif mean(values) < minimum:
            failed = True
            print(f"{evaluator_id}  FAILED  mean {mean(values):.2f} below minimum {minimum:.2f}")
        else:
            print(f"{evaluator_id}  passed  mean {mean(values):.2f}, minimum {minimum:.2f}")
    return 1 if failed else 0
