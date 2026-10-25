"""Unit tests for the deterministic topic matcher."""
from __future__ import annotations

import pytest

from apps.policies.engine.matcher import (
    content_token_count,
    normalize,
    score_topics,
    stem,
    tokenize,
)

HOTEL_TOPICS = [
    "check-in times|checkin|arrival time",
    "room service",
    "reservations|booking",
    "pet policy|pets",
]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Check-in!", "check in"),
        ("  Multiple    spaces ", "multiple spaces"),
        ("Café", "café"),
        ("", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "token,expected",
    [("times", "time"), ("reservations", "reservation"), ("booking", "book"), ("class", "class")],
)
def test_stem(token, expected):
    assert stem(token) == expected


def test_tokenize_drops_stopwords():
    assert tokenize("What time is check-in?") == ["time", "check"]


def test_content_token_count():
    assert content_token_count("What time is check-in?") == 2


def test_exact_phrase_scores_one():
    score, topic, _ = score_topics("what is your room service menu", HOTEL_TOPICS)
    assert score == 1.0
    assert topic == "room service"


def test_inflected_query_matches_topic():
    score, topic, _ = score_topics("What time is check-in?", HOTEL_TOPICS)
    assert score >= 0.6
    assert topic.startswith("check-in times")


def test_alias_matches():
    score, topic, _ = score_topics("Do you allow pets?", HOTEL_TOPICS)
    assert score >= 0.6
    assert topic.startswith("pet policy")


def test_out_of_domain_scores_zero():
    score, topic, scores = score_topics("Who won the World Cup final?", HOTEL_TOPICS)
    assert score == 0.0
    assert topic == ""
    assert all(value == 0.0 for value in scores.values())


def test_scores_are_explainable():
    _, _, scores = score_topics("Can I change my booking?", HOTEL_TOPICS)
    assert "reservations|booking" in scores
    assert max(scores.values()) >= 0.6
