"""Offline stand-in for a model choosing a tool.

The score is TF-IDF over the same text a host shows the model: tool name,
title, description, and input-schema descriptions. Tokens that show up on
every tool count for less. A description that stops naming its job loses.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass

_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "of",
        "for",
        "this",
        "that",
        "me",
        "whether",
        "against",
        "does",
        "can",
        "ones",
        "it",
        "on",
        "with",
        "into",
        "from",
        "be",
        "is",
        "are",
        "do",
        "i",
        "my",
        "our",
        "please",
        "tell",
        "when",
        "you",
        "need",
        "already",
        "then",
        "each",
        "them",
        "their",
        "its",
        "if",
        "so",
        "by",
        "in",
        "at",
        "as",
        "only",
        "also",
        "not",
        "no",
        "nor",
        "than",
        "too",
        "very",
        "just",
        "about",
        "which",
        "what",
        "how",
        "who",
        "these",
        "those",
        "there",
        "here",
        "via",
        "per",
        "plus",
        "both",
        "such",
        "was",
        "were",
        "will",
        "would",
        "should",
        "could",
    }
)

_STEM = {
    "evaluation": "eval",
    "evaluations": "eval",
    "evals": "eval",
    "comparing": "compare",
    "compared": "compare",
    "scenarios": "scenario",
    "scenario": "scenario",
    "policies": "policy",
    "injects": "inject",
    "injecting": "inject",
    "injected": "inject",
    "injection": "inject",
    "failures": "failure",
    "probes": "probe",
    "baselines": "baseline",
    "reports": "report",
    "validated": "validate",
    "validates": "validate",
    "validation": "validate",
    "recorded": "record",
    "events": "event",
    "enables": "enable",
    "enabled": "enable",
    "grading": "grade",
    "grades": "grade",
    "graded": "grade",
    "thresholds": "threshold",
    "regressions": "regression",
    "budgets": "budget",
    "breakers": "breaker",
}


@dataclass(frozen=True, slots=True)
class ToolView:
    """The text a host puts in front of a model for one tool."""

    name: str
    text: str


def tokenize(text: str) -> list[str]:
    """Lowercase tokens, with a tiny stem list and stopwords removed."""
    tokens: list[str] = []
    for raw in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")):
        token = _STEM.get(raw, raw)
        if token in _STOP or len(token) <= 1:
            continue
        tokens.append(token)
    return tokens


def rank(request: str, tools: list[ToolView]) -> list[tuple[str, float]]:
    """Return tools best-first with their scores."""
    documents = {tool.name: tokenize(tool.text) for tool in tools}
    document_frequency: Counter[str] = Counter()
    for tokens in documents.values():
        document_frequency.update(set(tokens))
    query = tokenize(request)
    query_bigrams = set(itertools.pairwise(query))
    scored: list[tuple[str, float]] = []
    total = len(tools)
    for tool in tools:
        tokens = documents[tool.name]
        frequencies = Counter(tokens)
        name_tokens = set(tokenize(tool.name))
        score = 0.0
        for token in set(query):
            if token not in frequencies:
                continue
            idf = math.log((total + 1) / (document_frequency[token] + 0.5))
            boost = 3.0 if token in name_tokens else 1.0
            score += boost * idf
        description_bigrams = set(itertools.pairwise(tokens))
        for pair in query_bigrams:
            if pair in description_bigrams:
                score += 1.5
        scored.append((tool.name, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def select_tool(request: str, tools: list[ToolView]) -> str:
    """Pick the highest-scoring tool. Ties resolve to the first name after the sort is stable."""
    ordered = rank(request, tools)
    if not ordered or ordered[0][1] <= 0:
        msg = f"no tool scored above zero for {request!r}"
        raise AssertionError(msg)
    return ordered[0][0]
