"""Unit test for the question-level run diff."""

import json

from evals.compare_runs import diff


def _run(path, version, correct: dict[str, bool]):
    questions = [
        {"id": i, "question": f"q {i}", "correct": c, "outcome": "answer", "reason": "r"}
        for i, c in correct.items()
    ]
    meta = {"agent_version": version, "model": "m"}
    path.write_text(json.dumps({"meta": meta, "questions": questions}))
    return path


def test_diff_reports_fixed_broken_and_net(tmp_path):
    old = _run(tmp_path / "a.json", "v1", {"a": False, "b": True, "c": False})
    new = _run(tmp_path / "b.json", "v2", {"a": True, "b": False, "c": True})
    out = diff(old, new)
    assert "fixed 2, broke 1, net +1" in out
    assert "FIXED a" in out and "FIXED c" in out and "BROKE b" in out
