"""Formatting and splitting — pure functions, no database."""
from __future__ import annotations

import pytest

from apps.channels.telegram.formatting import (
    format_citations,
    render_reply,
    split_message,
    strip_markdown,
)


class TestStripMarkdown:
    def test_bold_and_italic_are_removed(self):
        assert strip_markdown("This is **bold** and _soft_.") == "This is bold and soft."

    def test_inline_code_is_unwrapped(self):
        assert strip_markdown("Run `check-in` now") == "Run check-in now"

    def test_headings_lose_their_hashes(self):
        assert strip_markdown("## Check-in\n15:00") == "Check-in\n15:00"

    def test_bullets_become_dots(self):
        assert strip_markdown("- one\n- two") == "• one\n• two"

    def test_plain_text_is_unchanged(self):
        assert strip_markdown("Check-in starts at 15:00.") == "Check-in starts at 15:00."


class TestSplitMessage:
    def test_short_message_is_one_part(self):
        assert split_message("Hello") == ["Hello"]

    def test_empty_message_falls_back(self):
        assert split_message("   ") == ["I couldn't produce an answer just now. Please try again."]

    def test_splits_on_paragraph_before_hard_cut(self):
        paragraph = "a" * 120
        parts = split_message(f"{paragraph}\n\n{paragraph}", limit=200, max_parts=5)
        assert len(parts) == 2
        assert parts[0] == paragraph

    def test_no_part_exceeds_the_limit(self):
        text = " ".join(["word"] * 500)
        for part in split_message(text, limit=200, max_parts=10):
            assert len(part) <= 200

    def test_hard_cut_when_there_is_no_boundary(self):
        parts = split_message("x" * 500, limit=200, max_parts=3)
        assert len(parts) == 3
        assert all(len(part) <= 200 for part in parts)

    def test_respects_max_parts_and_marks_truncation(self):
        parts = split_message("word " * 400, limit=100, max_parts=2)
        assert len(parts) == 2
        assert parts[-1].endswith("…")

    def test_custom_limits_are_honoured(self):
        assert all(len(p) <= 50 for p in split_message("word " * 60, limit=50, max_parts=4))


class TestCitations:
    def test_renders_numbered_sources(self):
        block = format_citations([{"document_title": "Guest Handbook", "page_number": 3}])
        assert block == "Sources:\n[1] Guest Handbook — p.3"

    def test_empty_citations_produce_nothing(self):
        assert format_citations([]) == ""

    def test_missing_title_falls_back(self):
        assert "[1] Document" in format_citations([{}])


class TestRenderReply:
    def test_refusal_is_plain_content(self):
        parts = render_reply({"content": "I can only help with hotel questions.", "citations": []})
        assert parts == ["I can only help with hotel questions."]

    def test_citations_are_appended_when_there_is_room(self):
        parts = render_reply(
            {"content": "Check-in is at 15:00.", "citations": [{"document_title": "Hotel facts"}]}
        )
        assert "Sources:" in parts[-1]
        assert len(parts) == 1

    def test_internal_fields_are_never_forwarded(self):
        parts = render_reply(
            {
                "content": "Hello",
                "decision": "REFUSE",
                "reason_code": "PROHIBITED_TOPIC",
                "request_id": "abc123",
                "conversation_id": "def456",
                "latency_ms": 42,
            }
        )
        body = "\n".join(parts)
        for secret in ("REFUSE", "PROHIBITED_TOPIC", "abc123", "def456", "42"):
            assert secret not in body

    def test_empty_content_uses_safe_fallback(self):
        assert render_reply({"content": ""})[0].startswith("I couldn't produce")


@pytest.mark.parametrize("decision", ["ALLOW", "REFUSE", "CLARIFY", "ESCALATE"])
def test_every_decision_renders_content(decision):
    parts = render_reply({"content": "An answer.", "decision": decision})
    assert parts == ["An answer."]
