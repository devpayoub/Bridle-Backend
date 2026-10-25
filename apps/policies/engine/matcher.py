"""Deterministic text matching used by the rules evaluator.

Design goals: reproducible, dependency free, explainable. This is a lexical
matcher, not a semantic one — it is a *measurable control*, not a guarantee. The
optional model-based evaluator exists to cover the semantic gap, and both are
reported separately in policy metadata so the two signals can be compared.

Topic syntax: ``"check-in|checkin|arrival time"`` — the pipe separates aliases.
"""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those there here
    is are was were be been being am do does did doing have has had
    i me my we our you your he she it they them their his her its
    to of for with about into over under again further once
    in on at by from up down out off
    what when where which who whom why how
    can could would should will shall may might must
    please thanks thank hi hello
    """.split()
)

_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\b")


def normalize(text: str) -> str:
    """NFKC, lower-case, punctuation to spaces, collapsed whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _NON_WORD.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def stem(token: str) -> str:
    """Very small suffix stripper (English-leaning, deterministic).

    Not a linguistics-grade stemmer: it only needs to make plural/inflected forms
    of the same topic collide consistently.
    """
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    tokens = [stem(tok) for tok in normalize(text).split()]
    if drop_stopwords:
        tokens = [tok for tok in tokens if tok not in STOPWORDS]
    return tokens


def parse_topic(topic: str) -> list[str]:
    """Split a topic definition into aliases."""
    return [alias.strip() for alias in str(topic).split("|") if alias.strip()]


def _alias_score(alias: str, query_normalized: str, query_tokens: set[str]) -> float:
    alias_normalized = normalize(alias)
    if not alias_normalized:
        return 0.0
    # Exact phrase containment is the strongest signal.
    if f" {alias_normalized} " in f" {query_normalized} ":
        return 1.0

    alias_tokens = set(tokenize(alias))
    if not alias_tokens:
        return 0.0

    overlap = alias_tokens & query_tokens
    if not overlap:
        return 0.0

    topic_coverage = len(overlap) / len(alias_tokens)
    query_coverage = len(overlap) / max(1, len(query_tokens))
    return round(0.7 * topic_coverage + 0.3 * query_coverage, 4)


def score_topics(query: str, topics: list[str]) -> tuple[float, str, dict[str, float]]:
    """Return ``(best_score, best_topic, per_topic_scores)``."""
    query_normalized = normalize(query)
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0, "", {}

    scores: dict[str, float] = {}
    for topic in topics:
        best = max((_alias_score(alias, query_normalized, query_tokens) for alias in parse_topic(topic)), default=0.0)
        scores[topic] = round(best, 4)

    if not scores or max(scores.values()) <= 0.0:
        return 0.0, "", scores

    best_topic = max(scores, key=lambda key: scores[key])
    return scores.get(best_topic, 0.0), best_topic, scores


def content_token_count(text: str) -> int:
    return len(tokenize(text))


def extract_numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER.finditer(text or "")}
