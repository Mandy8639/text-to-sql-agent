"""Basic prompt-injection screening of the user's question, before any LLM call.

Deliberately narrow: it flags only phrasing that tries to override or extract the system
instructions ("ignore previous instructions", "reveal your system prompt", "you are now ...").
Destructive *requests* ("delete all canceled orders") are not flagged here: the agent's
REFUSE rule handles them, and the SQL validator and read-only role block them regardless.
A broad keyword list would refuse legitimate questions ("how many orders were canceled?").

Known limits: paraphrases, other languages and encodings can slip past regexes; that is why
this is one layer of four, not the defense.
"""

import re
from dataclasses import dataclass

_PATTERNS = {
    "override_instructions": r"\b(ignore|disregard|forget|override|bypass)\b[^.?!]{0,40}"
    r"\b(previous|prior|above|earlier|all|your|the|system)\b[^.?!]{0,20}"
    r"\b(instructions?|rules?|prompts?|guidelines?|constraints?)\b",
    "reveal_prompt": r"\b(reveal|show|print|repeat|output|tell me)\b[^.?!]{0,30}"
    r"\b(system prompt|your (instructions|prompt|rules))\b",
    "role_change": r"\b(you are now|act as|pretend (to be|you are)|developer mode|jailbreak|DAN)\b",
    "fake_system_turn": r"(^|\n)\s*(system|assistant)\s*:",
}
_COMPILED = {name: re.compile(p, re.I) for name, p in _PATTERNS.items()}


@dataclass(frozen=True)
class InjectionVerdict:
    flagged: bool
    rules: tuple[str, ...]


def screen_question(question: str) -> InjectionVerdict:
    hits = tuple(name for name, rx in _COMPILED.items() if rx.search(question))
    return InjectionVerdict(bool(hits), hits)


REFUSAL_TEXT = (
    "This request tries to change or reveal my instructions, so I won't act on it. "
    "Ask a question about the e-commerce data instead."
)
