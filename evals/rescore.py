"""Re-score a saved run against the CURRENT gold SQL, without calling the LLM again.

    python -m evals.rescore evals/results/<run>.json

Use after fixing a gold query: the agent's stored result rows are compared with the new gold
result. Writes a new run file (meta.rescored_from points at the original) and rebuilds
summary.md. Only answerable questions are re-graded; abstention scores don't depend on gold.
"""

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db.connection import make_engine
from app.db.executor import execute_readonly
from evals.dataset import load_benchmark
from evals.metrics import aggregate, grade_answer
from evals.summary import RESULTS_DIR, load_scores, rebuild_summary

GOLD_MAX_ROWS = 5000


def rescore(path: Path) -> Path:
    run = json.loads(path.read_text())
    if not run["meta"].get("stores_rows", False):
        raise SystemExit(f"{path.name} has no stored result rows; re-run the eval instead.")

    questions = {q.id: q for q in load_benchmark()}
    engine = make_engine(get_settings().agent_database_url)
    rescored, changed = [], []
    for s in load_scores(run):
        q = questions.get(s.id)
        if q is None or q.expected != "answer" or s.outcome != "answer":
            rescored.append(s)
            continue
        if s.pred_data is None or (s.pred_rows or 0) > len(s.pred_data):
            rescored.append(replace(s, correct=False, reason="result not fully stored"))
            continue
        gold = execute_readonly(engine, q.gold_sql, max_rows=GOLD_MAX_ROWS)
        correct, reason = grade_answer(
            q, gold, s.pred_columns, [tuple(r) for r in s.pred_data], s.pred_truncated
        )
        if correct != s.correct:
            changed.append(f"{s.id}: {'FAIL->PASS' if correct else 'PASS->FAIL'} ({reason})")
        rescored.append(replace(s, correct=correct, reason=reason, gold_rows=gold.row_count))

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {**run["meta"], "rescored_from": path.name, "rescored_at": now}
    out = RESULTS_DIR / f"{path.stem}_rescored{now.replace(':', '').replace('-', '')}.json"
    out.write_text(
        json.dumps(
            {
                "meta": meta,
                "metrics": aggregate(rescored),
                "questions": [s.to_dict() for s in rescored],
            },
            indent=2,
        )
    )
    print("\n".join(changed) or "no verdicts changed")
    rebuild_summary()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_file", type=Path)
    print(f"saved {rescore(parser.parse_args().run_file)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
