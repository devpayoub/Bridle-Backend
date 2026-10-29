"""Turning a chat payload into messages Telegram will accept.

Plain text only. Markdown/HTML parse modes fail on malformed model output and the
reply is lost entirely, which is worse than plain formatting.
"""
from __future__ import annotations

import re

from django.conf import settings

_MARKDOWN_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),
    (re.compile(r"__(.+?)__", re.S), r"\1"),
    # Single underscores, but never inside a snake_case identifier.
    (re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", re.S), r"\1"),
    (re.compile(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", re.S), r"\1"),
    (re.compile(r"`{1,3}([^`]+)`{1,3}"), r"\1"),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*[-*]\s+", re.M), "• "),
]

_SAFE_FALLBACK = "I couldn't produce an answer just now. Please try again."


def strip_markdown(text: str) -> str:
    """Remove the markdown a model likes to emit. Idempotent and lossless enough."""
    out = text or ""
    for pattern, replacement in _MARKDOWN_PATTERNS:
        out = pattern.sub(replacement, out)
    return out.strip()


def split_message(
    text: str,
    *,
    limit: int | None = None,
    max_parts: int | None = None,
) -> list[str]:
    """Split on paragraph → line → sentence → word boundaries, never mid-word if avoidable."""
    limit = limit or settings.CHANNELS_TELEGRAM_MAX_MESSAGE_CHARS
    max_parts = max_parts or settings.CHANNELS_TELEGRAM_MAX_PARTS
    text = (text or "").strip()
    if not text:
        return [_SAFE_FALLBACK]

    parts: list[str] = []
    remaining = text
    while remaining and len(parts) < max_parts:
        if len(remaining) <= limit:
            parts.append(remaining)
            remaining = ""
            break

        window = remaining[:limit]
        cut = -1
        for separator in ("\n\n", "\n", ". ", " "):
            index = window.rfind(separator)
            # Only accept a boundary in the back half, otherwise the part is tiny.
            if index >= limit * 0.5:
                cut = index + len(separator)
                break
        if cut <= 0:
            cut = limit

        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        # Ran out of parts: keep a truncated tail, but never exceed the limit.
        tail = remaining[: max(0, limit - 1)].rstrip()
        combined = f"{parts[-1]}\n\n{tail}…" if parts else f"{tail}…"
        if len(combined) > limit:
            combined = combined[: limit - 1].rstrip() + "…"
        parts[-1] = combined

    return [part for part in parts if part]


def format_citations(citations: list[dict] | None, *, limit: int = 600) -> str:
    """A compact source list. No URLs: the console holds the link, chat does not need it."""
    if not citations:
        return ""
    lines: list[str] = []
    for index, citation in enumerate(citations, start=1):
        title = str(citation.get("document_title") or "Document").strip()
        page = citation.get("page_number")
        suffix = f" — p.{page}" if page else ""
        lines.append(f"[{index}] {title}{suffix}")
    block = "Sources:\n" + "\n".join(lines)
    return block[:limit]


def render_reply(payload: dict) -> list[str]:
    """Payload → one or more message bodies.

    Never forwards decision, reason_code, usage, ids or latency: those belong to the
    console, and a reason code in chat teaches users how to probe the filter.
    """
    content = strip_markdown(str(payload.get("content") or "")) or _SAFE_FALLBACK
    citations = format_citations(payload.get("citations"))
    parts = split_message(content)
    if citations:
        if len(f"{parts[-1]}\n\n{citations}") <= settings.CHANNELS_TELEGRAM_MAX_MESSAGE_CHARS:
            parts[-1] = f"{parts[-1]}\n\n{citations}"
        elif len(parts) < settings.CHANNELS_TELEGRAM_MAX_PARTS:
            parts.append(citations)
        # else: drop the source list rather than exceed the part budget
    return parts
