"""Deterministic matching of business vocabulary to stable semantic names.

The matcher is deliberately storage- and planner-agnostic.  Cube routing and
the semantic-graph frontend share it so labels and synonyms have one scoring
contract while execution continues to use stable technical names.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+")
_DEFAULT_MIN_SCORE = 60
_QUERY_STOP_FRAGMENTS = {
    "一个",
    "今天",
    "什么",
    "分析",
    "各个",
    "多少",
    "如何",
    "查看",
    "最近",
    "昨天",
    "按照",
    "统计",
}


def normalize_semantic_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    words = _WORD_RE.findall(text.replace("_", " ").replace("-", " "))
    return " ".join(words)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Iterable[Any] = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _query_fragments(query: str) -> set[str]:
    fragments: set[str] = set()
    for word in _WORD_RE.findall(query):
        if re.fullmatch(r"[a-z0-9]+", word):
            if len(word) >= 2:
                fragments.add(word)
            continue
        if len(word) <= 4:
            fragments.add(word)
        for width in (2, 3, 4):
            if len(word) < width:
                continue
            fragments.update(word[i : i + width] for i in range(len(word) - width + 1))
    return {
        fragment
        for fragment in fragments
        if fragment not in _QUERY_STOP_FRAGMENTS and len(fragment) >= 2
    }


def _semantic_terms(item: dict[str, Any]) -> list[tuple[str, str, int]]:
    terms: list[tuple[str, str, int]] = []
    if name := item.get("name"):
        terms.append(("name", str(name), 70))
    if label := item.get("label"):
        terms.append(("label", str(label), 110))
    terms.extend(
        ("synonym", value, 100) for value in _string_list(item.get("synonyms"))
    )
    return terms


def score_semantic_item(
    query: str, item: dict[str, Any]
) -> tuple[int, list[dict[str, Any]]]:
    """Score one semantic object and retain auditable matching evidence."""

    normalized_query = normalize_semantic_text(query)
    query_fragments = _query_fragments(normalized_query)
    hits: list[tuple[int, str, str]] = []
    for source, raw_term, weight in _semantic_terms(item):
        term = normalize_semantic_text(raw_term)
        if not term:
            continue
        if normalized_query == term:
            score = weight * 2
        elif term in normalized_query:
            score = weight + min(len(term), 30)
        elif len(normalized_query) >= 2 and normalized_query in term:
            score = int(weight * 0.65)
        else:
            shared_fragments = query_fragments & _query_fragments(term)
            longest = max((len(fragment) for fragment in shared_fragments), default=0)
            if longest >= 3:
                score = int(weight * 0.55) + min(24, longest * 4)
            elif longest == 2 and len(shared_fragments) >= 2:
                score = int(weight * 0.35)
            else:
                continue
        hits.append((score, source, raw_term))

    description = normalize_semantic_text(item.get("description"))
    if description:
        description_hits = sorted(
            fragment for fragment in query_fragments if fragment in description
        )
        if description_hits:
            hits.append(
                (
                    min(36, 8 + len(description_hits) * 3),
                    "description",
                    ", ".join(description_hits[:5]),
                )
            )

    if not hits:
        return 0, []
    hits.sort(reverse=True)
    score = hits[0][0] + min(45, sum(hit[0] for hit in hits[1:]) // 4)
    return score, [
        {"source": source, "term": term, "score": hit_score}
        for hit_score, source, term in hits
    ]


def _member_match(
    query: str, member: dict[str, Any], *, min_score: int
) -> dict[str, Any] | None:
    score, hits = score_semantic_item(query, member)
    if score < min_score:
        return None
    result: dict[str, Any] = {
        "name": member.get("name", ""),
        "score": score,
        "matchedTerms": hits,
    }
    for source in ("label", "description"):
        value = member.get(source)
        if value not in (None, "", []):
            result[source] = value
    return result


def rank_semantic_members(
    query: str,
    members: list[dict[str, Any]],
    *,
    min_score: int = _DEFAULT_MIN_SCORE,
) -> list[dict[str, Any]]:
    """Return stable member matches, preferring longer overlapping terms."""

    matches = [
        match
        for member in members
        if (match := _member_match(query, member, min_score=min_score))
    ]
    ranked = sorted(matches, key=lambda item: (-item["score"], item["name"]))

    def primary_term(match: dict[str, Any]) -> str:
        for hit in match["matchedTerms"]:
            if hit["source"] != "description":
                return normalize_semantic_text(hit["term"])
        return ""

    filtered: list[dict[str, Any]] = []
    for match in ranked:
        term = primary_term(match)
        shadowed = any(
            term
            and term != primary_term(other)
            and term in primary_term(other)
            and primary_term(other) in normalize_semantic_text(query)
            and other["score"] >= match["score"]
            for other in ranked
        )
        if not shadowed:
            filtered.append(match)
    return filtered
