"""Cycle-safe traversal and path-hint resolution for semantic graph queries."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from wren.semantic_graph.advanced_bridge import normalized_bridge_policy
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError

_PATH_SEARCH_CAP = 20_000


def build_adjacency(state: GraphState) -> dict[str, list[dict[str, Any]]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in state.edges.values():
        models = edge.get("declaredModels") or []
        if not (
            isinstance(models, list)
            and len(models) == 2
            and all(isinstance(model, str) for model in models)
        ):
            continue
        cardinality = edge.get("cardinality")
        if cardinality == "MANY_TO_MANY":
            if isinstance(edge.get("bridgePolicy"), dict):
                for source, target in ((models[0], models[1]), (models[1], models[0])):
                    adjacency[source].append(
                        _path_step(edge, source, target, traversal="BRIDGE")
                    )
            continue
        if edge.get("cardinalityValidation") != "verified":
            continue
        safe = {
            tuple(direction)
            for direction in edge.get("safeDirections") or []
            if isinstance(direction, list) and len(direction) == 2
        }
        for source, target in ((models[0], models[1]), (models[1], models[0])):
            traversal = "SAFE" if (source, target) in safe else "FANOUT"
            adjacency[source].append(_path_step(edge, source, target, traversal))
    for steps in adjacency.values():
        steps.sort(
            key=lambda step: (
                step["relationship"],
                step["to"],
                step.get("role") or "",
            )
        )
    return adjacency


def _path_step(
    edge: dict[str, Any], source: str, target: str, traversal: str
) -> dict[str, Any]:
    return {
        "relationship": edge["name"],
        "from": source,
        "to": target,
        "cardinality": edge.get("cardinality"),
        "traversal": traversal,
        "role": edge.get("role"),
        "entity": edge.get("entity"),
    }


def resolve_node_path(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    target: str,
    relationship_path: list[str] | None,
    role: str | None,
    max_depth: int,
    member: str,
) -> list[dict[str, Any]]:
    if relationship_path is not None:
        reject_unprotected_m2m_hint(state, relationship_path)
        path = path_from_hint(
            adjacency,
            source=source,
            relationships=relationship_path,
            max_depth=max_depth,
        )
        reached = path[-1]["to"] if path else source
        paths = [path] if reached == target else []
    else:
        paths = enumerate_paths(
            adjacency,
            source=source,
            target=target,
            max_depth=max_depth,
            limit=2,
            required_role=role,
        )
    if not paths:
        unprotected = unprotected_many_to_many_edges(state, source, [target])
        code = (
            "GRAPH_MANY_TO_MANY_POLICY_REQUIRED"
            if unprotected
            else "GRAPH_MEMBER_PATH_NOT_FOUND"
        )
        raise GraphPlanningError(
            code,
            f"member '{member}' is not reachable from '{source}'",
            details={
                "member": member,
                "sourceModel": source,
                "targetModel": target,
                "maxDepth": max_depth,
                "relationships": unprotected,
            },
        )
    if len(paths) > 1:
        raise GraphPlanningError(
            "GRAPH_PATH_AMBIGUOUS",
            f"member '{member}' has multiple paths from '{source}'",
            details={
                "member": member,
                "sourceModel": source,
                "targetModel": target,
                "candidatePaths": [
                    [public_step(step) for step in path] for path in paths
                ],
                "hint": "set request.pathHints",
            },
        )
    path = paths[0]
    for step in path:
        if step["traversal"] == "BRIDGE":
            normalized_bridge_policy(state, step)
    return path


def reject_unprotected_m2m_hint(
    state: GraphState, relationship_path: list[str]
) -> None:
    unprotected = [
        name
        for name in relationship_path
        if name in state.edges
        and state.edges[name].get("cardinality") == "MANY_TO_MANY"
        and not isinstance(state.edges[name].get("bridgePolicy"), dict)
    ]
    if unprotected:
        raise GraphPlanningError(
            "GRAPH_MANY_TO_MANY_POLICY_REQUIRED",
            "many-to-many pathHints require a compiled Bridge/Allocation policy",
            details={"relationships": unprotected},
        )


def enumerate_paths(
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    target: str,
    max_depth: int,
    limit: int = 2,
    required_role: str | None = None,
) -> list[list[dict[str, Any]]]:
    if source == target:
        return [[]] if required_role is None else []
    explorations = 0

    def shortest(
        forbidden: tuple[str, str, str] | None = None,
    ) -> list[dict[str, Any]] | None:
        nonlocal explorations
        queue = deque([(source, [], frozenset({source}), False)])
        visited: set[tuple[str, bool]] = {(source, False)}
        while queue:
            current, path, path_nodes, has_role = queue.popleft()
            if len(path) >= max_depth:
                continue
            for step in adjacency.get(current, []):
                explorations += 1
                if explorations > _PATH_SEARCH_CAP:
                    raise GraphPlanningError(
                        "GRAPH_PATH_SEARCH_LIMIT",
                        "graph path search exceeded its bounded exploration limit",
                        details={
                            "sourceModel": source,
                            "targetModel": target,
                            "maxDepth": max_depth,
                            "explorationCap": _PATH_SEARCH_CAP,
                            "hint": "set request.pathHints",
                        },
                    )
                signature = (
                    step["relationship"],
                    step["from"],
                    step["to"],
                )
                if signature == forbidden or step["to"] in path_nodes:
                    continue
                next_has_role = has_role or (
                    required_role is not None and step.get("role") == required_role
                )
                next_path = [*path, step]
                if step["to"] == target and (required_role is None or next_has_role):
                    return next_path
                state_key = (step["to"], next_has_role)
                if state_key in visited:
                    continue
                visited.add(state_key)
                queue.append(
                    (
                        step["to"],
                        next_path,
                        path_nodes | {step["to"]},
                        next_has_role,
                    )
                )
        return None

    first = shortest()
    if first is None or limit <= 0:
        return []
    result = [first]
    if limit == 1:
        return result
    # Any distinct simple path must omit at least one directed edge from the
    # first path.  Re-running bounded BFS with each edge removed detects an
    # alternate path in polynomial time and avoids exponential path enumeration.
    for step in first:
        alternate = shortest((step["relationship"], step["from"], step["to"]))
        if alternate is not None:
            result.append(alternate)
            break
    return result


def path_from_hint(
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    relationships: list[str],
    max_depth: int,
) -> list[dict[str, Any]]:
    if len(relationships) > max_depth:
        raise GraphPlanningError(
            "GRAPH_PATH_HINT_TOO_DEEP",
            f"path hint has {len(relationships)} steps but maxDepth is {max_depth}",
            details={"relationshipPath": relationships, "maxDepth": max_depth},
        )
    current = source
    visited = {source}
    result: list[dict[str, Any]] = []
    for relationship in relationships:
        candidates = [
            step
            for step in adjacency.get(current, [])
            if step["relationship"] == relationship
        ]
        if len(candidates) != 1:
            raise GraphPlanningError(
                "GRAPH_PATH_HINT_INVALID",
                f"relationship '{relationship}' cannot continue from '{current}'",
                details={
                    "sourceModel": source,
                    "currentModel": current,
                    "relationship": relationship,
                    "relationshipPath": relationships,
                },
            )
        step = candidates[0]
        if step["to"] in visited:
            raise GraphPlanningError(
                "GRAPH_PATH_HINT_CYCLE",
                "pathHints must describe a cycle-free simple path",
                details={"relationshipPath": relationships, "node": step["to"]},
            )
        result.append(step)
        current = step["to"]
        visited.add(current)
    return result


def public_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "relationship": step["relationship"],
        "from": step["from"],
        "to": step["to"],
        "cardinality": step.get("cardinality"),
        "traversal": step["traversal"],
        "role": step.get("role"),
        "entity": step.get("entity"),
    }


def unprotected_many_to_many_edges(
    state: GraphState, source: str, targets: list[str]
) -> list[str]:
    pairs = {tuple(sorted((source, target))) for target in targets if target != source}
    return sorted(
        edge["name"]
        for edge in state.edges.values()
        if edge.get("cardinality") == "MANY_TO_MANY"
        and tuple(sorted(edge.get("declaredModels") or [])) in pairs
        and not isinstance(edge.get("bridgePolicy"), dict)
    )
