"""Versioned prompt templates.

Each prompt is a file named `<name>_v<N>.md`. A prompt is never edited after an eval run has
used it: changes go in a new version, so every result in evals/results/ maps to the exact text
that produced it. Templates use `string.Template` `$placeholders` because SQL is full of braces.
"""

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str, version: int) -> Template:
    """Load prompt `<name>_v<version>.md` as a Template."""
    path = PROMPTS_DIR / f"{name}_v{version}.md"
    return Template(path.read_text(encoding="utf-8"))
