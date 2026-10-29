"""Load the benchmark (evals/questions.yaml) and guard the test split against leakage.

Rule: dev questions may be used for few-shot examples and tuning; test questions (and the
owner's blind hold-out set, evals/holdout.yaml) must never reach a prompt. Two guards enforce
it in code:
- `few_shot_pool()` is the only sanctioned source of examples, and it returns dev only.
- `assert_no_leakage()` scans any prompt text (or example ids) for test questions/gold SQL.
  The eval harness calls it on every agent before and during a run.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.yaml"
HOLDOUT_PATH = Path(__file__).resolve().parent / "holdout.yaml"

Split = Literal["dev", "test", "holdout"]
Expected = Literal["answer", "clarify", "refuse"]
CATEGORIES = {
    "aggregation",
    "filtering",
    "joins",
    "time_series",
    "window_functions",
    "ambiguous",
    "refusal",
}
DIFFICULTIES = {"easy", "medium", "hard"}
MIN_SQL_NEEDLE = 80  # characters of normalized gold SQL before it is checked for leakage


class LeakageError(AssertionError):
    """Test-split content was found where only dev content is allowed."""


@dataclass(frozen=True)
class Question:
    id: str
    split: Split
    category: str
    difficulty: str
    expected: Expected
    question: str
    gold_sql: str | None = None
    order_matters: bool = False
    verify: bool = False
    notes: str | None = None
    reason: str | None = None
    grading: Literal["auto", "manual"] = "auto"  # manual = open-ended, reviewed by rubric
    accept_answer: bool = False  # clarify question where a stated-definition answer is OK


def _validate(q: Question) -> None:
    problems = []
    if q.split not in ("dev", "test", "holdout"):
        problems.append(f"bad split {q.split!r}")
    if q.category not in CATEGORIES:
        problems.append(f"bad category {q.category!r}")
    if q.difficulty not in DIFFICULTIES:
        problems.append(f"bad difficulty {q.difficulty!r}")
    if q.expected not in ("answer", "clarify", "refuse"):
        problems.append(f"bad expected {q.expected!r}")
    if q.expected == "answer" and not q.gold_sql:
        problems.append("answer question without gold_sql")
    if q.expected != "answer" and q.gold_sql:
        problems.append("non-answer question with gold_sql")
    if q.expected != "answer" and not q.reason:
        problems.append("non-answer question without reason")
    if problems:
        raise ValueError(f"{q.id}: {'; '.join(problems)}")


@lru_cache
def load_benchmark(path: Path = QUESTIONS_PATH) -> tuple[Question, ...]:
    """Load and validate every question. Raises ValueError on any malformed entry."""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)["questions"]
    questions = tuple(Question(**{**q, "gold_sql": (q.get("gold_sql") or None)}) for q in raw)
    for q in questions:
        _validate(q)
    ids = [q.id for q in questions]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate question ids: {sorted(dupes)}")
    return questions


def load_split(split: Split | Literal["all"]) -> list[Question]:
    """Questions in one split (or all of dev+test), in file order. `holdout` is its own file."""
    if split == "holdout":
        return list(load_benchmark(HOLDOUT_PATH))
    return [q for q in load_benchmark() if split == "all" or q.split == split]


def _protected() -> list[Question]:
    """Questions that must never reach a prompt: test split plus the blind hold-out set."""
    return [q for q in load_benchmark() if q.split == "test"] + list(load_benchmark(HOLDOUT_PATH))


def few_shot_pool() -> list[Question]:
    """The ONLY sanctioned source of few-shot examples: dev questions that have gold SQL."""
    return [q for q in load_benchmark() if q.split == "dev" and q.gold_sql]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def assert_no_leakage(texts: Iterable[str] = (), example_ids: Iterable[str] = ()) -> None:
    """Raise LeakageError if any test question, test gold SQL, or test id appears.

    `texts` are prompt strings (system prompt, retrieved examples); `example_ids` are ids of
    benchmark questions an agent used as examples.
    """
    test = _protected()
    test_ids = {q.id for q in test}
    leaked_ids = sorted(set(example_ids) & test_ids)
    if leaked_ids:
        raise LeakageError(f"test questions used as examples: {leaked_ids}")

    # Content that also occurs in dev is not secret: e.g. the hold-out set was written blind and
    # h01 happens to match dev flt-05 word for word. Only protected-only content is searched for.
    dev_text = [
        _normalize(t)
        for q in load_benchmark()
        if q.split == "dev"
        for t in (q.question, q.gold_sql)
        if t
    ]
    needles: dict[str, str] = {}
    for q in test:
        candidates = [q.question]
        # Very short SQL (e.g. "select count(*) as orders from orders") occurs inside many
        # legitimate queries, so only distinctive gold SQL is searched for.
        if q.gold_sql and len(_normalize(q.gold_sql)) >= MIN_SQL_NEEDLE:
            candidates.append(q.gold_sql)
        for c in map(_normalize, candidates):
            if not any(c in d for d in dev_text):
                needles.setdefault(c, q.id)
    for text in texts:
        haystack = _normalize(text)
        hits = sorted({qid for needle, qid in needles.items() if needle in haystack})
        if hits:
            raise LeakageError(f"test-split content found in prompt: {hits}")
