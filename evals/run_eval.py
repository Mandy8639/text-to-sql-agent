"""Evaluate an agent version on a benchmark split.

    python -m evals.run_eval --agent baseline --split test     (or: make eval AGENT=baseline)

Writes evals/results/<timestamp>_<agent_version>_<split>.json with per-question details (including
the agent's result rows, so the run can be re-scored later by evals/rescore.py) and rebuilds
evals/results/summary.md. Partial runs (--limit / --ids) are saved but left out of the summary.
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from app.config import get_settings
from app.db.connection import make_engine
from app.db.executor import QueryResult, execute_readonly
from app.schema_doc import load_schema_doc
from evals.agents import AGENTS, Agent
from evals.dataset import Question, assert_no_leakage, load_split
from evals.metrics import QuestionScore, aggregate, score_question
from evals.summary import RESULTS_DIR, rebuild_summary

log = logging.getLogger("run_eval")

GOLD_MAX_ROWS = 5000
MAX_LLM_ERROR_SHARE = 0.10  # above this, the run is marked invalid


def _git_commit() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        return sha + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _run_one(agent: Agent, q: Question, gold: QueryResult | None) -> QuestionScore:
    res = agent.answer(q.question)
    assert_no_leakage(example_ids=res.example_ids)  # per-question guard for few-shot agents
    score = score_question(q, res, gold)
    log.info("%-8s %-5s %s", q.id, "PASS" if score.correct else "FAIL", score.reason)
    return score


def run(
    agent_name: str,
    split: str,
    *,
    limit: int | None,
    ids: list[str] | None,
    workers: int,
) -> dict:
    settings = get_settings()
    agent = AGENTS[agent_name](settings)
    assert_no_leakage(texts=[agent.system_prompt])  # the static prompt must not contain test data

    questions = load_split(split)
    if ids:
        questions = [q for q in questions if q.id in set(ids)]
    if limit:
        questions = questions[:limit]
    partial = bool(ids or limit)

    # Gold results come from running gold SQL now, as the same read-only role.
    engine = make_engine(settings.agent_database_url)
    gold = {
        q.id: execute_readonly(engine, q.gold_sql, max_rows=GOLD_MAX_ROWS)
        for q in questions
        if q.gold_sql
    }

    log.info(
        "running %s on %d %s questions (%d workers)", agent.version, len(questions), split, workers
    )
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        scores = list(pool.map(lambda q: _run_one(agent, q, gold.get(q.id)), questions))
    wall_s = time.perf_counter() - start

    metrics = aggregate(scores)
    run_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "run_at": run_at,
        "agent": agent_name,
        "agent_version": agent.version,
        "model": settings.llm_model,
        "effort": settings.llm_effort,
        "split": split,
        "partial": partial,
        "workers": workers,
        "wall_clock_s": round(wall_s, 1),
        "commit": _git_commit(),
        "stores_rows": True,
        "schema_doc_version": load_schema_doc()["version"],
    }
    # An outage (no credit, rate limits, network) shows up as llm-stage errors. Such a run measures
    # the API, not the agent, so it is flagged and kept out of summary.md.
    llm_errors = metrics["errors_by_stage"].get("llm", 0)
    meta["invalid"] = llm_errors > MAX_LLM_ERROR_SHARE * len(scores)
    if meta["invalid"]:
        log.error("INVALID RUN: %d/%d questions failed at the LLM stage", llm_errors, len(scores))

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{run_at.replace(':', '').replace('-', '')}_{agent.version}_{split}.json"
    out.write_text(
        json.dumps(
            {"meta": meta, "metrics": metrics, "questions": [s.to_dict() for s in scores]},
            indent=2,
            default=str,
        )
    )
    rebuild_summary()
    log.info("saved %s", out.relative_to(RESULTS_DIR.parent.parent))
    return {"meta": meta, "metrics": metrics, "path": out}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an agent on the benchmark.")
    parser.add_argument("--agent", default="baseline", choices=sorted(AGENTS))
    parser.add_argument("--split", default="test", choices=["dev", "test", "all"])
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--ids", nargs="+", help="only these question ids")
    parser.add_argument("--workers", type=int, default=4, help="parallel questions")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2"):  # one log line per API call is noise
        logging.getLogger(noisy).setLevel(logging.WARNING)

    result = run(
        args.agent,
        args.split,
        limit=args.limit,
        ids=args.ids,
        workers=args.workers,
    )
    m = result["metrics"]
    if result["meta"]["invalid"]:
        print("\nINVALID RUN (too many LLM/API errors); not added to summary.md", file=sys.stderr)
    print(f"\n{result['meta']['agent_version']} on {args.split} ({m['n']} questions)")
    print(f"  execution accuracy : {m['execution_accuracy']}%  (answerable: {m['n_answerable']})")
    print(f"  overall accuracy   : {m['overall_accuracy']}%")
    print(f"  valid SQL rate     : {m['valid_sql_rate']}%")
    print(
        f"  abstention accuracy: {m['abstention_accuracy']}%  (expected: {m['n_abstain_expected']})"
    )
    print(
        "  by difficulty      : "
        + ", ".join(
            f"{k} {v['accuracy']}% ({v['correct']}/{v['n']})" for k, v in m["by_difficulty"].items()
        )
    )
    print(
        "  by category        : "
        + ", ".join(f"{k} {v['accuracy']}%" for k, v in m["by_category"].items())
    )
    p50_s, p95_s = m["latency_ms_p50"] / 1000, m["latency_ms_p95"] / 1000
    print(f"  latency p50 / p95  : {p50_s:.1f}s / {p95_s:.1f}s")
    print(f"  cost               : ${m['avg_cost_usd']}/question, ${m['total_cost_usd']} total")
    print(f"  errors by stage    : {m['errors_by_stage'] or 'none'}")
    return 1 if result["meta"]["invalid"] else 0


if __name__ == "__main__":
    sys.exit(main())
