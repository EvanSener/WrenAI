"""Deterministic natural-language discovery for Wren Cubes.

The resolver deliberately does not generate SQL.  It maps human vocabulary to
stable Cube/member names, then leaves filters and time ranges to the caller.
This keeps discovery deterministic and lets ``wren cube query`` remain the
single governed execution path.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+")
_DRILL_WORDS = ("下钻", "钻取", "明细", "详情", "drill")
_MEMBER_MATCH_MIN_SCORE = 60
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


def _normalize(value: Any) -> str:
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


def _semantic_terms(item: dict) -> list[tuple[str, str, int]]:
    terms: list[tuple[str, str, int]] = []
    if name := item.get("name"):
        terms.append(("name", str(name), 70))
    label = item.get("label")
    if label:
        terms.append(("label", str(label), 110))
    terms.extend(
        ("synonym", value, 100) for value in _string_list(item.get("synonyms"))
    )
    return terms


def _score_item(query: str, item: dict) -> tuple[int, list[dict[str, Any]]]:
    normalized_query = _normalize(query)
    query_fragments = _query_fragments(normalized_query)
    hits: list[tuple[int, str, str]] = []
    for source, raw_term, weight in _semantic_terms(item):
        term = _normalize(raw_term)
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

    description = _normalize(item.get("description"))
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


def _members(cube: dict, key: str) -> list[dict]:
    return [member for member in (cube.get(key) or []) if isinstance(member, dict)]


def _member_match(query: str, member: dict) -> dict[str, Any] | None:
    score, hits = _score_item(query, member)
    # Low description-only overlap is useful when ranking cubes, but is too
    # weak to put a field into suggestedQuery. Keep suggestions conservative so
    # a shared word such as "广告" never selects every advertising metric.
    if score < _MEMBER_MATCH_MIN_SCORE:
        return None
    result: dict[str, Any] = {
        "name": member.get("name", ""),
        "score": score,
        "matchedTerms": hits,
    }
    for source, target in (("label", "label"), ("description", "description")):
        value = member.get(source)
        if value not in (None, "", []):
            result[target] = value
    return result


def _hierarchy_matches(
    query: str,
    cube: dict,
    member_by_name: dict[str, dict],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    hierarchies = cube.get("hierarchies") or {}
    if not isinstance(hierarchies, dict):
        return results

    for name, levels in hierarchies.items():
        if not isinstance(name, str) or not isinstance(levels, list):
            continue
        safe_levels = [level for level in levels if isinstance(level, str)]
        if not safe_levels:
            continue
        metadata: dict[str, Any] = {"name": name}
        hierarchy_score, hits = _score_item(query, metadata)
        level_score = 0
        for level in safe_levels:
            member = member_by_name.get(level)
            if member:
                score, _ = _score_item(query, member)
                level_score = max(level_score, score)
        total = hierarchy_score + level_score // 2
        if total <= 0:
            continue
        result: dict[str, Any] = {
            "name": name,
            "levels": safe_levels,
            "score": total,
            "matchedTerms": hits,
        }
        results.append(result)

    return sorted(results, key=lambda item: (-item["score"], item["name"]))


def _ranked_matches(query: str, members: list[dict]) -> list[dict[str, Any]]:
    matches = [match for member in members if (match := _member_match(query, member))]
    ranked = sorted(matches, key=lambda item: (-item["score"], item["name"]))

    def primary_term(match: dict[str, Any]) -> str:
        for hit in match["matchedTerms"]:
            if hit["source"] != "description":
                return _normalize(hit["term"])
        return ""

    # Prefer the more specific semantic member when aliases overlap. For
    # example, "点击率" must resolve to CTR only, not both CTR and a separate
    # click-count measure whose synonym is the shorter word "点击".
    filtered: list[dict[str, Any]] = []
    for match in ranked:
        term = primary_term(match)
        shadowed = any(
            term
            and term != primary_term(other)
            and term in primary_term(other)
            and primary_term(other) in _normalize(query)
            and other["score"] >= match["score"]
            for other in ranked
        )
        if not shadowed:
            filtered.append(match)
    return filtered


def resolve_cubes(manifest: dict, query: str, *, limit: int = 5) -> dict[str, Any]:
    """Return deterministic Cube/member candidates for a natural-language query."""
    candidates: list[dict[str, Any]] = []
    wants_drill = any(word in query.casefold() for word in _DRILL_WORDS)

    for cube in manifest.get("cubes", []) or []:
        if not isinstance(cube, dict):
            continue
        measures = _members(cube, "measures")
        dimensions = _members(cube, "dimensions")
        time_dimensions = _members(cube, "timeDimensions")
        measure_matches = _ranked_matches(query, measures)
        dimension_matches = _ranked_matches(query, dimensions)
        time_matches = _ranked_matches(query, time_dimensions)
        member_by_name = {
            str(member.get("name", "")): member
            for member in dimensions + time_dimensions
        }
        hierarchy_matches = _hierarchy_matches(query, cube, member_by_name)
        cube_score, cube_hits = _score_item(query, cube)
        raw_priority = cube.get("priority", 0)
        priority = (
            raw_priority
            if isinstance(raw_priority, int) and not isinstance(raw_priority, bool)
            else 0
        )

        score = cube_score * 4
        score += sum(item["score"] for item in measure_matches[:3]) * 2
        score += sum(item["score"] for item in dimension_matches[:3])
        score += sum(item["score"] for item in time_matches[:2])
        score += sum(item["score"] for item in hierarchy_matches[:1])
        if score <= 0:
            continue

        if wants_drill and hierarchy_matches:
            suggested_dimensions = list(hierarchy_matches[0]["levels"])
            suggested_dimensions.extend(
                item["name"]
                for item in dimension_matches
                if item["name"] not in suggested_dimensions
            )
        else:
            suggested_dimensions = [item["name"] for item in dimension_matches]

        candidate: dict[str, Any] = {
            "cube": cube.get("name", ""),
            "baseObject": cube.get("baseObject", ""),
            "score": score,
            "priority": priority,
            "matchedTerms": cube_hits,
            "measures": measure_matches,
            "dimensions": dimension_matches,
            "timeDimensions": time_matches,
            "hierarchies": hierarchy_matches,
            "suggestedQuery": {
                "cube": cube.get("name", ""),
                "measures": [item["name"] for item in measure_matches],
                "dimensions": suggested_dimensions,
            },
        }
        for key in ("label", "description"):
            if cube.get(key):
                candidate[key] = cube[key]
        candidates.append(candidate)

    # Semantic evidence always wins. Priority only resolves candidates with an
    # equal semantic score, so a high-default Cube cannot swallow a query that
    # explicitly names a more specific business subject.
    candidates.sort(key=lambda item: (-item["score"], -item["priority"], item["cube"]))
    selected = candidates[: max(1, limit)]
    ambiguous = False
    if len(selected) >= 2 and selected[0]["score"]:
        semantically_close = selected[1]["score"] >= selected[0]["score"] * 0.9
        same_priority = selected[1]["priority"] == selected[0]["priority"]
        ambiguous = semantically_close and same_priority
    return {"query": query, "ambiguous": ambiguous, "matches": selected}
