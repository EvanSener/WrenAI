"""Deterministic natural-language discovery for Wren Cubes.

The resolver deliberately does not generate SQL.  It maps human vocabulary to
stable Cube/member names, then leaves filters and time ranges to the caller.
This keeps discovery deterministic and lets ``wren cube query`` remain the
single governed execution path.
"""

from __future__ import annotations

from typing import Any

from wren.semantic_matching import (
    rank_semantic_members as _ranked_matches,
)
from wren.semantic_matching import (
    score_semantic_item as _score_item,
)

_DRILL_WORDS = ("下钻", "钻取", "明细", "详情", "drill")


def _members(cube: dict, key: str) -> list[dict]:
    return [member for member in (cube.get(key) or []) if isinstance(member, dict)]


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
