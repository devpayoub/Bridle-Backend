"""Platform-mandatory controls.

These checks are applied to **every** assistant regardless of customer
configuration. They are intentionally not configurable through the API: a
customer-editable prompt or policy must never be able to switch them off.

Scope is deliberately narrow. This module detects *instruction-override and
prompt-exfiltration attempts*, not general content moderation; domain rules live
in the assistant policy.
"""
from __future__ import annotations

import re

# Instruction-override / jailbreak phrasing. Patterns are applied to normalised,
# lower-cased text with word boundaries where it matters.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(previous|prior|above|earlier|all|any)\b[^.]{0,20}\b(instruction|prompt|rule|direction)s?\b")),
    ("ignore_rules", re.compile(r"\b(ignore|disregard|bypass|break)\b[^.]{0,30}\b(your|the|all)\b[^.]{0,20}\b(rules|restrictions|guidelines|guardrails|policy)\b")),
    ("role_override", re.compile(r"\byou are now\b|\bfrom now on you\b|\bact as (a|an) (unrestricted|unfiltered|uncensored)\b")),
    ("pretend", re.compile(r"\bpretend (that )?you (are|have no)\b")),
    ("system_prompt_exfiltration", re.compile(r"\b(reveal|show|print|repeat|output|display)\b[^.]{0,30}\b(system prompt|initial prompt|hidden instruction|your instruction)s?\b")),
    ("system_prompt_mention", re.compile(r"\bsystem prompt\b|\bdeveloper mode\b|\bjailbreak\b")),
    ("forget_training", re.compile(r"\bforget (everything|all)\b")),
    ("dan_mode", re.compile(r"\bDAN\b|\bdo anything now\b")),
    ("fake_role_tag", re.compile(r"</?(system|assistant|instructions?|prompt)>|\[/?system\]|<\|im_start\|>")),
    ("disable_safety", re.compile(r"\b(disable|turn off|deactivate)\b[^.]{0,25}\b(safety|filter|guardrail|restriction|policy)\b")),
)

_INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


def strip_invisible(text: str) -> str:
    """Remove zero-width and bidi control characters used to smuggle instructions."""
    return _INVISIBLE_CHARS.sub("", text or "")


def detect_prompt_injection(text: str) -> str | None:
    """Return the matched pattern name, or ``None`` when the text looks benign."""
    cleaned = strip_invisible(text).lower()
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            return name
    return None


def is_instruction_like(text: str) -> bool:
    """Used at ingestion time to neutralise instruction-like document content."""
    return detect_prompt_injection(text) is not None
