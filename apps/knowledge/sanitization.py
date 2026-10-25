"""Defences against prompt injection embedded in uploaded documents.

Uploaded content is *evidence*, never instruction. Two controls are applied:

1. Instruction-like sentences are neutralised at ingestion time (recorded as
   ``is_flagged`` on the chunk so operators can see it happened).
2. At generation time the context is wrapped in delimiters and the system prompt
   states that delimited content is untrusted reference material.

This reduces, but does not eliminate, injection risk. See README limitations.
"""
from __future__ import annotations

import re

from apps.policies.engine.platform import is_instruction_like, strip_invisible

NEUTRALISED = "[neutralised: instruction-like text removed]"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_ROLE_PREFIX = re.compile(r"^\s*(system|assistant|developer|user)\s*:\s*", flags=re.IGNORECASE)


def sanitize_text(text: str) -> tuple[str, bool]:
    """Return ``(sanitised_text, was_flagged)``."""
    cleaned = strip_invisible(text)
    flagged = False
    output: list[str] = []

    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped:
            output.append("")
            continue
        if _ROLE_PREFIX.match(stripped) or is_instruction_like(stripped):
            flagged = True
            output.append(NEUTRALISED)
            continue
        output.append(line)

    return "\n".join(output).strip(), flagged
