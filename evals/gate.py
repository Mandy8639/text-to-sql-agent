"""Regression gate: is the agent worse than the recorded baseline? (`make eval-gate`, D40)

Runs the production agent on the dev split and compares it question by question with
evals/baselines/. Dev, not test: people read gate failures and fix what they see, and that must
never happen to the split behind the reported numbers.

The model is not deterministic, so a single miss proves little. The baseline records which
questions are stable over several runs, and a stable question that fails is run once more: it
counts as broken only if it fails twice.

Exit codes: 0 pass, 1 regression, 2 inconclusive (the run measured an outage, not the agent).
"""

import argparse
import json
import logging
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from evals.run_eval import _git_commit, run

log = logging.getLogger("gate")

AGENT = "v6"
SPLIT = "dev"
BASELINE = Path(__file__).parent / "baselines" / f"{SPLIT}_{AGENT}.json"

# Must never regress: abstention (a wrong answer is worse than none) and the revenue definition.
CRITICAL = frozenset({"amb-05", "ref-01", "ref-05", "ref-09", "ts-03", "ts-12", "win-01"})
MAX_BROKEN = 1  # non-critical questions that may fail twice before the gate blocks
MAX_COST_INCREASE = 0.25
MAX_P95_INCREASE = 0.50  # generous: latency depends on the runner and the API, tokens do not
MAX_RECHECKS = 10  # more failures than this is no flake, so don't pay to confirm it


@dataclass
class GateResult:
    status: str  # pass | regression | inconclusive
    reasons: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)  # stable, failed twice
    flaky: list[str] = field(default_factory=list)  # stable, failed, then passed on recheck
    fixed: list[str] = field(default_factory=list)  # baseline wrong, correct now
    cost_change: float | None = None
    p95_change: float | None = None

    @property
    def exit_code(self) -> int:
        return {"pass": 0, "regression": 1, "inconclusive": 2}[self.status]


def _passed(questions: list[dict]) -> dict[str, bool]:
    return {q["id"]: bool(q["correct"]) for q in questions}


def to_recheck(baseline: dict, current: dict[str, bool]) -> list[str]:
    """Stable questions that failed. Empty when there are too many to be flakes."""
    failed = sorted(i for i, ok in current.items() if not ok and baseline["correct"].get(i))
    return failed if len(failed) <= MAX_RECHECKS else []


def decide(
    baseline: dict,
    current: dict[str, bool],
    rechecked: dict[str, bool],
    *,
    avg_cost_usd: float,
    p95_ms: float,
) -> GateResult:
    """Apply the gate rules. Pure, so every rule is unit-tested without an LLM."""
    stable = baseline["correct"]
    failed = sorted(i for i, ok in current.items() if not ok and stable.get(i))
    broken = [i for i in failed if not rechecked.get(i, False)]
    result = GateResult(
        status="pass",
        broken=broken,
        flaky=[i for i in failed if i not in broken],
        fixed=sorted(i for i, ok in current.items() if ok and stable.get(i) is False),
        cost_change=avg_cost_usd / baseline["avg_cost_usd"] - 1,
        p95_change=p95_ms / baseline["latency_ms_p95"] - 1,
    )

    critical = [i for i in broken if i in CRITICAL]
    if critical:
        result.reasons.append(f"critical question(s) broke: {', '.join(critical)}")
    if len(broken) > MAX_BROKEN:
        result.reasons.append(f"{len(broken)} questions broke (limit {MAX_BROKEN})")
    if result.cost_change > MAX_COST_INCREASE:
        result.reasons.append(
            f"cost per question up {result.cost_change:.0%} (limit {MAX_COST_INCREASE:.0%})"
        )
    if result.p95_change > MAX_P95_INCREASE:
        result.reasons.append(
            f"p95 latency up {result.p95_change:.0%} (limit {MAX_P95_INCREASE:.0%})"
        )
    if result.reasons:
        result.status = "regression"
    return result


def render(result: GateResult, baseline: dict, current: dict) -> str:
    """Markdown for the CI job summary and the PR comment."""
    title = {
        "pass": "✅ Eval gate passed",
        "regression": "❌ Eval gate failed",
        "inconclusive": "⚠️ Eval gate inconclusive",
    }[result.status]
    lines = [f"### {title}", ""]
    lines += [f"- {r}" for r in result.reasons]
    if result.reasons:
        lines.append("")
    lines += [
        f"{AGENT} on `{SPLIT}` with {current['meta']['model']}, against the baseline from "
        f"`{baseline['meta']['commit']}`.",
        "",
        "| | Baseline | This change |",
        "|---|---:|---:|",
        f"| Execution accuracy | {baseline['execution_accuracy']}% "
        f"| {current['metrics']['execution_accuracy']}% |",
        f"| Abstention accuracy | {baseline['abstention_accuracy']}% "
        f"| {current['metrics']['abstention_accuracy']}% |",
        f"| Cost / question | ${baseline['avg_cost_usd']:.5f} "
        f"| ${current['metrics']['avg_cost_usd']:.5f} |",
        f"| p95 latency | {baseline['latency_ms_p95'] / 1000:.1f} s "
        f"| {current['metrics']['latency_ms_p95'] / 1000:.1f} s |",
        "",
        f"Broke: {', '.join(result.broken) or 'none'}. "
        f"Fixed: {', '.join(result.fixed) or 'none'}. "
        f"Failed once, passed on recheck: {', '.join(result.flaky) or 'none'}.",
    ]
    return "\n".join(lines) + "\n"


def run_agent(ids: list[str] | None = None) -> dict:
    return run(AGENT, SPLIT, limit=None, ids=ids, workers=4, save=False)


def record(path: Path, runs: int) -> dict:
    """Write the baseline. A question is stable only if it gave the same verdict in every run."""
    results = [run_agent() for _ in range(runs)]
    invalid = sum(r["meta"]["invalid"] for r in results)
    if invalid:
        raise SystemExit(f"{invalid} of {runs} runs were invalid; baseline not written")
    passes = [_passed(r["questions"]) for r in results]
    correct: dict[str, bool] = {}
    unstable = []
    for qid in passes[0]:
        verdicts = {p[qid] for p in passes}
        if len(verdicts) == 1:
            correct[qid] = verdicts.pop()
        else:
            unstable.append(qid)
    metrics = [r["metrics"] for r in results]
    baseline = {
        "meta": {
            "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": _git_commit(),
            "model": results[0]["meta"]["model"],
            "schema_doc_version": results[0]["meta"]["schema_doc_version"],
            "runs": runs,
        },
        "correct": dict(sorted(correct.items())),
        "unstable": sorted(unstable),
        "execution_accuracy": statistics.mean(m["execution_accuracy"] for m in metrics),
        "abstention_accuracy": statistics.mean(m["abstention_accuracy"] for m in metrics),
        "avg_cost_usd": statistics.mean(m["avg_cost_usd"] for m in metrics),
        "latency_ms_p95": statistics.mean(m["latency_ms_p95"] for m in metrics),
    }
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2) + "\n")
    return baseline


def check(path: Path) -> tuple[GateResult, str]:
    baseline = json.loads(path.read_text())
    model = get_settings().llm_model
    if model != baseline["meta"]["model"]:
        raise SystemExit(f"baseline is for {baseline['meta']['model']}, LLM_MODEL is {model}")
    current = run_agent()
    if current["meta"]["invalid"]:
        return _inconclusive("too many LLM/API errors in the run", baseline, current)
    passed = _passed(current["questions"])
    ids = to_recheck(baseline, passed)
    rechecked: dict[str, bool] = {}
    if ids:
        log.info("rechecking %s", ", ".join(ids))
        again = run_agent(ids)
        if again["meta"]["invalid"]:
            return _inconclusive("too many LLM/API errors in the recheck", baseline, current)
        rechecked = _passed(again["questions"])
    result = decide(
        baseline,
        passed,
        rechecked,
        avg_cost_usd=current["metrics"]["avg_cost_usd"],
        p95_ms=current["metrics"]["latency_ms_p95"],
    )
    return result, render(result, baseline, current)


def _inconclusive(reason: str, baseline: dict, current: dict) -> tuple[GateResult, str]:
    result = GateResult(status="inconclusive", reasons=[reason])
    return result, render(result, baseline, current)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--record", action="store_true", help="write a new baseline instead")
    parser.add_argument("--runs", type=int, default=3, help="runs behind a new baseline")
    parser.add_argument("--report", type=Path, help="also write the Markdown report here")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    log.setLevel(logging.INFO)

    if args.record:
        baseline = record(args.baseline, args.runs)
        print(json.dumps({k: v for k, v in baseline.items() if k != "correct"}, indent=2))
        return 0
    result, report = check(args.baseline)
    print(report)
    if args.report:
        args.report.write_text(report)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
