"""Question-level diff between two runs: `python -m evals.compare_runs OLD.json NEW.json`.

A net accuracy change can hide churn (a technique that fixes 3 questions and breaks 2 looks
like +1). This lists what was fixed, what broke, and per-run cost and latency.
"""

import argparse
import json
from pathlib import Path


def load(path: Path) -> tuple[dict, dict[str, dict]]:
    run = json.loads(path.read_text())
    return run["meta"], {q["id"]: q for q in run["questions"]}


def diff(old_path: Path, new_path: Path) -> str:
    old_meta, old = load(old_path)
    new_meta, new = load(new_path)
    shared = sorted(set(old) & set(new))
    fixed = [i for i in shared if not old[i]["correct"] and new[i]["correct"]]
    broke = [i for i in shared if old[i]["correct"] and not new[i]["correct"]]

    def label(meta: dict) -> str:
        return f"{meta['agent_version']} ({meta['model']})"

    lines = [f"{label(old_meta)}  ->  {label(new_meta)}   [{len(shared)} shared questions]"]
    lines.append(f"fixed {len(fixed)}, broke {len(broke)}, net {len(fixed) - len(broke):+d}")
    for title, ids in (("FIXED", fixed), ("BROKE", broke)):
        for i in ids:
            q = new[i]
            lines.append(f"  {title} {i:8} {q['question'][:70]}")
            lines.append(f"         now: {q['outcome']}, {q['reason']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    args = parser.parse_args()
    print(diff(args.old, args.new))


if __name__ == "__main__":
    main()
