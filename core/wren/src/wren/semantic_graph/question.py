"""Natural-language frontend for governed semantic-graph queries."""

from __future__ import annotations

import re
from collections import deque
from copy import deepcopy
from typing import Any

from wren.semantic_graph.advanced_planner import plan_graph_query
from wren.semantic_graph.binding_policy import allowed_bindings, master_model
from wren.semantic_graph.frontend import plan_frontend_query
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_matching import rank_semantic_members, score_semantic_item

_CHINESE_DIMENSION_PHRASE = re.compile(
    r"(?:按|按照)\s*(.+?)\s*(?:查看|看|统计|分析|对比|展示|汇总)"
)
_ENGLISH_DIMENSION_PHRASE = re.compile(
    r"\bby\s+(.+?)(?:\s+(?:show|compare|with|for)\b|$)", re.IGNORECASE
)


class NaturalLanguageGraphFrontend:
    """Resolve labels/synonyms through Ontology, then emit GraphQueryRequest."""

    name = "natural_language"

    def compile(
        self,
        payload: Any,
        *,
        semantic_graph: dict[str, Any],
        queryability_index: dict[str, Any],
        ontology_graph: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        resolution = resolve_graph_question(
            semantic_graph,
            queryability_index,
            str(payload),
            ontology_graph=ontology_graph,
            anchor_model=options.get("anchorModel"),
            max_depth=options.get("maxDepth"),
            path_hints=options.get("pathHints"),
        )
        if resolution["status"] != "resolved":
            rejections = resolution.get("rejectedCandidates") or []
            if rejections and all(
                item.get("code") == "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
                for item in rejections
            ):
                rejection = rejections[0]
                raise GraphPlanningError(
                    "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN",
                    rejection.get("message")
                    or "natural-language graph query requested a non-master binding",
                    details=resolution,
                )
            code = {
                "ambiguous": "GRAPH_QUESTION_SOURCE_AMBIGUOUS",
                "master_source_conflict": "GRAPH_QUESTION_MASTER_SOURCE_CONFLICT",
                "unresolved_dimension": "GRAPH_QUESTION_DIMENSION_NOT_FOUND",
                "unresolved_metric": "GRAPH_QUESTION_METRIC_NOT_FOUND",
            }.get(resolution["status"], "GRAPH_QUESTION_NOT_QUERYABLE")
            raise GraphPlanningError(
                code,
                resolution.get("message")
                or "natural-language graph query is unresolved",
                details=resolution,
            )
        return {
            "inputKind": "natural_language",
            "request": resolution["graphQuery"],
            "resolution": resolution,
        }


def plan_graph_question(
    semantic_graph: dict[str, Any],
    queryability_index: dict[str, Any],
    question: str,
    *,
    ontology_graph: dict[str, Any] | None = None,
    anchor_model: str | None = None,
    max_depth: int | None = None,
    path_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a natural-language question through the pluggable frontend."""

    options = {
        "anchorModel": anchor_model,
        "maxDepth": max_depth,
        "pathHints": path_hints,
    }
    return plan_frontend_query(
        semantic_graph,
        queryability_index,
        NaturalLanguageGraphFrontend(),
        question,
        ontology_graph=ontology_graph,
        options={key: value for key, value in options.items() if value is not None},
    )


def resolve_graph_question(
    semantic_graph: dict[str, Any],
    queryability_index: dict[str, Any],
    question: str,
    *,
    ontology_graph: dict[str, Any] | None = None,
    anchor_model: str | None = None,
    max_depth: int | None = None,
    path_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a question without executing or hiding ambiguous candidates."""

    if not isinstance(question, str) or not question.strip():
        raise GraphPlanningError(
            "GRAPH_QUESTION_INVALID", "graph question must be a non-empty string"
        )
    question = question.strip()
    metrics, dimensions = _member_catalog(semantic_graph, ontology_graph)
    metric_matches = rank_semantic_members(question, metrics)
    dimension_phrase = _dimension_phrase(question)
    dimension_phrases = _split_dimension_phrase(dimension_phrase)
    dimension_matches = rank_semantic_members(question, dimensions)
    phrase_match_groups = [
        (phrase, rank_semantic_members(phrase, dimensions))
        for phrase in dimension_phrases
    ]
    phrase_matches = _merge_matches(
        *(matches for _phrase, matches in phrase_match_groups)
    )
    dimension_matches = _merge_matches(phrase_matches, dimension_matches)
    unresolved_dimension_phrases = [
        phrase for phrase, matches in phrase_match_groups if not matches
    ]

    base: dict[str, Any] = {
        "schemaVersion": 1,
        "question": question,
        "resolver": "ontology_lexical_v1",
        "catalogSource": "ontology_graph"
        if _has_ontology_members(ontology_graph)
        else "semantic_graph_fallback",
        "dimensionPhrase": dimension_phrase,
        "dimensionPhrases": dimension_phrases,
        "metrics": metric_matches,
        "dimensions": dimension_matches,
        "candidates": [],
        "rejectedCandidates": [],
    }
    if not metric_matches:
        return {
            **base,
            "status": "unresolved_metric",
            "message": "question did not resolve to a governed global metric",
        }
    if unresolved_dimension_phrases:
        return {
            **base,
            "status": "unresolved_dimension",
            "unresolvedDimensionPhrases": unresolved_dimension_phrases,
            "message": (
                "dimension phrase(s) "
                + ", ".join(repr(item) for item in unresolved_dimension_phrases)
                + " did not resolve to a governed global dimension"
            ),
        }

    metric_names = [item["name"] for item in metric_matches]
    dimension_names = [item["name"] for item in dimension_matches]
    metric_definitions = {
        item["name"]: item for item in semantic_graph.get("metrics") or []
    }
    configured_masters = {
        metric: master
        for metric in metric_names
        if (master := master_model(metric_definitions.get(metric))) is not None
    }
    if len(set(configured_masters.values())) > 1 and anchor_model is None:
        return {
            **base,
            "status": "master_source_conflict",
            "masterModels": configured_masters,
            "message": (
                "resolved metrics require different master models; use a "
                "structured multi-fact graph request"
            ),
        }
    sources = _candidate_sources(semantic_graph, metric_names)
    if anchor_model is not None:
        sources = [anchor_model]
    if not sources:
        return {
            **base,
            "status": "not_queryable",
            "message": "no graph node can bind every resolved metric",
        }

    graph_limit = max(len(semantic_graph.get("nodes") or []) - 1, 0)
    effective_depth = graph_limit if max_depth is None else max_depth
    successful: list[tuple[dict[str, Any], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for source in sources:
        dimension_selectors = _automatic_dimension_selectors(
            semantic_graph,
            source=source,
            dimensions=dimension_names,
            max_depth=effective_depth,
        )
        request: dict[str, Any] = {
            "schemaVersion": 1,
            "anchorModel": source,
            "facts": [{"sourceModel": source, "metrics": metric_names}],
            "dimensions": dimension_selectors,
            "maxDepth": effective_depth,
        }
        if path_hints:
            request["pathHints"] = deepcopy(path_hints)
        try:
            plan = plan_graph_query(semantic_graph, queryability_index, request)
            if any(
                fact.get("strategy") != "DIRECT_AGGREGATE"
                for fact in (plan.get("relationalPlan") or {}).get("facts") or []
            ):
                raise GraphPlanningError(
                    "GRAPH_QUESTION_FANOUT_REQUIRES_STRUCTURED_REQUEST",
                    "natural-language auto-planning only selects M:1/1:1 paths; "
                    "fanout requires an explicit governed request and attribution policy",
                    details={"anchorModel": source, "dimensions": dimension_names},
                )
        except GraphPlanningError as exc:
            code = exc.code
            message = str(exc)
            details = deepcopy(exc.details)
            if code == "GRAPH_FANOUT_ALLOCATION_REQUIRED":
                code = "GRAPH_QUESTION_FANOUT_REQUIRES_STRUCTURED_REQUEST"
                message = (
                    "natural-language auto-planning only selects M:1/1:1 paths; "
                    "fanout requires an explicit governed request and attribution policy"
                )
                details = {
                    "anchorModel": source,
                    "dimensions": dimension_names,
                    "plannerRejection": deepcopy(exc.details),
                }
            rejected.append(
                {
                    "anchorModel": source,
                    "code": code,
                    "message": message,
                    "details": details,
                }
            )
            continue
        candidate = _candidate_summary(
            question,
            source,
            plan,
            semantic_graph,
            ontology_graph,
            metric_names,
            dimension_names,
        )
        successful.append((candidate, request))

    successful.sort(key=lambda item: _candidate_sort_key(item[0]))
    public_candidates = [candidate for candidate, _ in successful]
    base["candidates"] = public_candidates
    base["rejectedCandidates"] = rejected
    if not successful:
        return {
            **base,
            "status": "not_queryable",
            "message": "resolved members have no unambiguous governed graph plan",
        }

    if anchor_model is not None or len(successful) == 1:
        selected, request = successful[0]
    else:
        first_key = _selection_key(successful[0][0])
        second_key = _selection_key(successful[1][0])
        if first_key == second_key:
            return {
                **base,
                "status": "ambiguous",
                "message": (
                    "multiple graph sources have equal semantic evidence; set an "
                    "anchorModel instead of guessing"
                ),
            }
        selected, request = successful[0]

    return {
        **base,
        "status": "resolved",
        "selectedAnchor": selected["anchorModel"],
        "selectionEvidence": selected["selectionEvidence"],
        "graphQuery": request,
    }


def _member_catalog(
    semantic_graph: dict[str, Any], ontology_graph: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fallback = {
        "METRIC": {item["name"]: item for item in semantic_graph.get("metrics") or []},
        "DIMENSION": {
            item["name"]: item for item in semantic_graph.get("dimensions") or []
        },
    }
    if not _has_ontology_members(ontology_graph):
        return list(fallback["METRIC"].values()), list(fallback["DIMENSION"].values())

    result: dict[str, list[dict[str, Any]]] = {"METRIC": [], "DIMENSION": []}
    assert ontology_graph is not None
    for node in ontology_graph.get("nodes") or []:
        kind = node.get("type")
        name = node.get("name")
        if kind not in result or name not in fallback[kind]:
            continue
        semantic = fallback[kind][name]
        result[kind].append(
            {
                **semantic,
                "label": node.get("label") or semantic.get("label"),
                "description": node.get("description") or semantic.get("description"),
                "synonyms": node.get("synonyms") or semantic.get("synonyms") or [],
                "ontologyNodeId": node.get("id"),
            }
        )
    for kind, members in result.items():
        found = {item["name"] for item in members}
        members.extend(
            item for name, item in fallback[kind].items() if name not in found
        )
    return result["METRIC"], result["DIMENSION"]


def _has_ontology_members(ontology_graph: dict[str, Any] | None) -> bool:
    return isinstance(ontology_graph, dict) and any(
        node.get("type") in {"METRIC", "DIMENSION"}
        for node in ontology_graph.get("nodes") or []
        if isinstance(node, dict)
    )


def _dimension_phrase(question: str) -> str | None:
    for pattern in (_CHINESE_DIMENSION_PHRASE, _ENGLISH_DIMENSION_PHRASE):
        if match := pattern.search(question):
            value = match.group(1).strip(" ，,。?")
            if value:
                return value
    return None


def _split_dimension_phrase(value: str | None) -> list[str]:
    if value is None:
        return []
    return [
        item.strip()
        for item in re.split(r"\s*(?:、|,|，|和|及|与|\band\b)\s*", value)
        if item.strip()
    ]


def _merge_matches(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            current = merged.get(item["name"])
            if current is None or item["score"] > current["score"]:
                merged[item["name"]] = item
    return sorted(merged.values(), key=lambda item: (-item["score"], item["name"]))


def _candidate_sources(
    semantic_graph: dict[str, Any], metric_names: list[str]
) -> list[str]:
    definitions = {item["name"]: item for item in semantic_graph.get("metrics") or []}
    per_metric = [
        {
            binding["model"]
            for binding in allowed_bindings(
                definitions.get(metric),
                (
                    item
                    for item in semantic_graph.get("metricBindings") or []
                    if item.get("metric") == metric
                ),
            )
        }
        for metric in metric_names
    ]
    if not per_metric:
        return []
    return sorted(set.intersection(*per_metric))


def _automatic_dimension_selectors(
    semantic_graph: dict[str, Any],
    *,
    source: str,
    dimensions: list[str],
    max_depth: int,
) -> list[dict[str, Any]]:
    """Select only a unique shortest path made entirely of safe directions.

    A direct M:1 edge therefore wins over a longer detour, while equal-length
    role paths remain ambiguous and are left for the planner to reject.
    """

    definitions = {
        item["name"]: item for item in semantic_graph.get("dimensions") or []
    }
    bindings: dict[str, list[str]] = {}
    for dimension in dimensions:
        definition = definitions.get(dimension) or {}
        candidates = sorted(
            {
                binding["model"]
                for binding in allowed_bindings(
                    definition,
                    (
                        item
                        for item in semantic_graph.get("dimensionBindings") or []
                        if item.get("dimension") == dimension
                    ),
                )
            }
        )
        bindings[dimension] = candidates

    adjacency: dict[str, list[tuple[str, str]]] = {
        node["name"]: [] for node in semantic_graph.get("nodes") or []
    }
    for edge in semantic_graph.get("edges") or []:
        relationship = edge.get("name")
        if not isinstance(relationship, str):
            continue
        for direction in edge.get("safeDirections") or []:
            if (
                isinstance(direction, list)
                and len(direction) == 2
                and direction[0] in adjacency
                and direction[1] in adjacency
            ):
                adjacency[direction[0]].append((direction[1], relationship))
    for steps in adjacency.values():
        steps.sort(key=lambda item: (item[1], item[0]))

    selectors: list[dict[str, Any]] = []
    for dimension in dimensions:
        paths = _shortest_safe_paths(
            adjacency,
            source=source,
            targets=set(bindings[dimension]),
            max_depth=max_depth,
        )
        selector: dict[str, Any] = {"name": dimension}
        if len(paths) == 1:
            target, relationships = paths[0]
            selector["bindingModel"] = target
            selector["relationshipPath"] = relationships
        selectors.append(selector)
    return selectors


def _shortest_safe_paths(
    adjacency: dict[str, list[tuple[str, str]]],
    *,
    source: str,
    targets: set[str],
    max_depth: int,
) -> list[tuple[str, list[str]]]:
    if source not in adjacency or not targets:
        return []
    if source in targets:
        return [(source, [])]
    distances = {source: 0}
    routes: dict[str, list[tuple[str, ...]]] = {source: [()]}
    queue = deque([source])
    while queue:
        current = queue.popleft()
        depth = distances[current]
        if depth >= max_depth:
            continue
        for target, relationship in adjacency.get(current, []):
            next_depth = depth + 1
            candidates = [(*route, relationship) for route in routes.get(current, [])]
            if target not in distances:
                distances[target] = next_depth
                routes[target] = candidates[:2]
                queue.append(target)
                continue
            if distances[target] != next_depth:
                continue
            for route in candidates:
                if route not in routes[target] and len(routes[target]) < 2:
                    routes[target].append(route)

    reachable_targets = [
        target
        for target in targets
        if target in distances and distances[target] <= max_depth
    ]
    if not reachable_targets:
        return []
    shortest_depth = min(distances[target] for target in reachable_targets)
    matches = [
        (target, list(route))
        for target in sorted(reachable_targets)
        if distances[target] == shortest_depth
        for route in routes[target]
    ]
    return matches[:2]


def _candidate_summary(
    question: str,
    source: str,
    plan: dict[str, Any],
    semantic_graph: dict[str, Any],
    ontology_graph: dict[str, Any] | None,
    metrics: list[str],
    dimensions: list[str],
) -> dict[str, Any]:
    node = next(
        (
            item
            for item in semantic_graph.get("nodes") or []
            if item.get("name") == source
        ),
        {"name": source},
    )
    ontology_node = _ontology_dataset(ontology_graph, source)
    source_score, source_hits = score_semantic_item(question, ontology_node or node)
    context = _best_query_context(
        ontology_graph, question, source, metrics=metrics, dimensions=dimensions
    )
    facts = (plan.get("relationalPlan") or {}).get("facts") or []
    total_hops = sum(
        dimension.get("hops", 0)
        for fact in facts
        for dimension in fact.get("dimensions") or []
    )
    evidence = {
        "queryContext": context,
        "sourceSemanticScore": source_score,
        "sourceMatchedTerms": source_hits,
        "totalRelationshipHops": total_hops,
        "masterMetricBindings": sorted(
            metric
            for metric in metrics
            if master_model(
                next(
                    (
                        item
                        for item in semantic_graph.get("metrics") or []
                        if item.get("name") == metric
                    ),
                    None,
                )
            )
            == source
        ),
    }
    return {
        "anchorModel": source,
        "facts": [fact.get("sourceModel") for fact in facts],
        "strategy": plan.get("strategy"),
        "selectionEvidence": evidence,
    }


def _ontology_dataset(
    ontology_graph: dict[str, Any] | None, source: str
) -> dict[str, Any] | None:
    if not isinstance(ontology_graph, dict):
        return None
    return next(
        (
            node
            for node in ontology_graph.get("nodes") or []
            if node.get("type") == "DATASET" and node.get("name") == source
        ),
        None,
    )


def _best_query_context(
    ontology_graph: dict[str, Any] | None,
    question: str,
    source: str,
    *,
    metrics: list[str],
    dimensions: list[str],
) -> dict[str, Any] | None:
    if not isinstance(ontology_graph, dict):
        return None
    nodes = {node.get("id"): node for node in ontology_graph.get("nodes") or []}
    base_edges = [
        edge
        for edge in ontology_graph.get("edges") or []
        if edge.get("type") == "CUBE_BASE_DATASET"
        and (nodes.get(edge.get("targetId")) or {}).get("name") == source
    ]
    contexts: list[dict[str, Any]] = []
    for edge in base_edges:
        cube = nodes.get(edge.get("sourceId"))
        if not isinstance(cube, dict):
            continue
        cube_id = cube.get("id")
        cube_metrics = {
            (nodes.get(member.get("targetId")) or {}).get("name")
            for member in ontology_graph.get("edges") or []
            if member.get("sourceId") == cube_id and member.get("type") == "CUBE_METRIC"
        }
        cube_dimensions = {
            (nodes.get(member.get("targetId")) or {}).get("name")
            for member in ontology_graph.get("edges") or []
            if member.get("sourceId") == cube_id
            and member.get("type") in {"CUBE_DIMENSION", "CUBE_TIME_DIMENSION"}
        }
        if not set(metrics).issubset(cube_metrics):
            continue
        semantic_score, hits = score_semantic_item(question, cube)
        properties = cube.get("properties") or {}
        priority = properties.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            priority = 0
        contexts.append(
            {
                "name": cube.get("name"),
                "semanticScore": semantic_score,
                "matchedTerms": hits,
                "dimensionCoverage": len(set(dimensions) & cube_dimensions),
                "priority": priority,
            }
        )
    if not contexts:
        return None
    contexts.sort(
        key=lambda item: (
            -item["semanticScore"],
            -item["dimensionCoverage"],
            -item["priority"],
            str(item["name"]),
        )
    )
    return contexts[0]


def _selection_key(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    evidence = candidate["selectionEvidence"]
    context = evidence.get("queryContext") or {}
    return (
        int(context.get("semanticScore") or 0),
        int(context.get("dimensionCoverage") or 0),
        int(context.get("priority") or 0),
        int(evidence.get("sourceSemanticScore") or 0),
    )


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    selection = _selection_key(candidate)
    hops = candidate["selectionEvidence"].get("totalRelationshipHops") or 0
    return (*(-value for value in selection), hops, candidate["anchorModel"])
