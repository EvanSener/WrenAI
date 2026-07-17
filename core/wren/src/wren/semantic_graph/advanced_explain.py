"""Build Virtual Wide Table schema and structured graph explanations."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

from wren.semantic_graph.advanced_member_routes import member_routes, member_steps
from wren.semantic_graph.advanced_types import GraphState


def virtual_wide_table(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    facts: list[dict[str, Any]],
    discovery: dict[str, Any],
) -> dict[str, Any]:
    first = facts[0] if facts else {"entityGrain": [], "dimensions": []}
    schema: list[dict[str, Any]] = []
    projected_attributes = {
        (item["model"], item["field"]): item["alias"] for item in request["attributes"]
    }
    if request["includeReachable"]:
        anchors = (
            [request["anchorModel"]]
            if request.get("anchorModel")
            else [fact["sourceModel"] for fact in facts]
        )
        governed_nodes = reachable_nodes_bfs(
            adjacency, anchors=anchors, max_depth=request["maxDepth"]
        )
        reachable_nodes = reachable_nodes_bfs(
            declared_adjacency(state),
            anchors=anchors,
            max_depth=request["maxDepth"],
        )
    else:
        reachable_nodes = {model for model, _ in projected_attributes}
        governed_nodes = set(reachable_nodes)
    for model in sorted(reachable_nodes):
        node = state.nodes.get(model)
        if node is None:
            continue
        for attribute in sorted(
            node.get("attributes") or [], key=lambda item: item.get("name") or ""
        ):
            field = attribute.get("name")
            if not isinstance(field, str):
                continue
            projected_alias = projected_attributes.get((model, field))
            schema.append(
                {
                    "name": f"{model}.{field}",
                    "outputName": projected_alias,
                    "kind": "attribute",
                    "model": model,
                    "field": field,
                    "type": attribute.get("type"),
                    "projected": projected_alias is not None,
                    "reachability": (
                        "governed" if model in governed_nodes else "declared_only"
                    ),
                }
            )
    for entity in first["entityGrain"]:
        for field, alias in zip(entity["fields"], entity["outputAliases"], strict=True):
            schema.append(
                {
                    "name": alias,
                    "kind": "entity",
                    "semanticMember": entity["name"],
                    "sourceField": field,
                    "projected": True,
                }
            )
    schema.extend(
        {
            "name": dimension["alias"],
            "kind": dimension.get("memberKind", "dimension"),
            "semanticMember": dimension["name"],
            "type": dimension.get("type"),
            "projected": True,
        }
        for dimension in first["dimensions"]
        if dimension.get("memberKind") != "attribute"
    )
    schema.extend(
        {
            "name": metric["alias"],
            "kind": "metric",
            "semanticMember": metric["name"],
            "sourceModel": fact["sourceModel"],
            "masterModel": metric.get("masterModel"),
            "isMaster": metric.get("isMaster", False),
            "additivity": metric["additivity"],
            "projected": True,
        }
        for fact in facts
        for metric in fact["metrics"]
    )
    schema.extend(
        {
            "name": calculation["alias"],
            "kind": "post_metric",
            "semanticMember": calculation["name"],
            "stage": "post_aggregate",
            "projected": True,
        }
        for calculation in request["postCalculations"]
    )
    projected_semantic = {
        (item["kind"], item["semanticMember"])
        for item in schema
        if item.get("kind") in {"metric", "dimension"}
        and isinstance(item.get("semanticMember"), str)
    }
    for item in discovery["acceptedMembers"]:
        kind = item.get("kind")
        name = item.get("name")
        if kind not in {"metric", "dimension"} or not isinstance(name, str):
            continue
        if (kind, name) in projected_semantic:
            continue
        schema.append(
            {
                "name": name,
                "outputName": None,
                "kind": kind,
                "semanticMember": name,
                "bindingModel": item.get("bindingModel"),
                "path": deepcopy(item.get("anchorPath") or []),
                "projected": False,
            }
        )
    for item in discovery["rejectedMembers"]:
        kind = item.get("kind")
        name = item.get("name")
        if kind not in {"metric", "dimension"} or not isinstance(name, str):
            continue
        if any(
            entry.get("kind") == kind and entry.get("semanticMember") == name
            for entry in schema
        ):
            continue
        schema.append(
            {
                "name": name,
                "outputName": None,
                "kind": kind,
                "semanticMember": name,
                "projected": False,
                "queryable": False,
                "rejectionCode": item.get("code"),
                "rejectionReason": item.get("reason"),
            }
        )
    # Preserve the pre-existing dimension-first ordering. Metric binding
    # decisions are additive explain evidence and must not move established
    # path-hint/master-data entries relied on by callers.
    path_decisions = [
        {
            "memberKind": dimension.get("memberKind", "dimension"),
            "member": dimension["name"],
            "sourceModel": fact["sourceModel"],
            "bindingModel": route.get("model"),
            "decision": route_decision(request, dimension, route),
            "path": deepcopy(route.get("path") or []),
        }
        for fact in facts
        for dimension in fact["dimensions"]
        for route in member_routes(dimension)
    ]
    path_decisions.extend(
        {
            "memberKind": "metric",
            "member": metric["name"],
            "sourceModel": fact["sourceModel"],
            "bindingModel": metric.get("bindingModel") or fact["sourceModel"],
            "decision": (
                "masterDataBinding" if metric.get("masterModel") else "factBinding"
            ),
            "path": [],
        }
        for fact in facts
        for metric in fact["metrics"]
    )
    path_decisions.extend(
        {
            "memberKind": "metric_calculation_input",
            "member": metric["name"],
            "sourceModel": fact["sourceModel"],
            "bindingModel": route.get("model"),
            "decision": route_decision(request, metric, route),
            "path": deepcopy(route.get("path") or []),
        }
        for fact in facts
        for metric in fact["metrics"]
        if metric.get("routes")
        for route in member_routes(metric)
    )
    path_decisions.extend(
        {
            "memberKind": item["kind"],
            "member": item["name"],
            "sourceModel": request.get("anchorModel"),
            "bindingModel": item.get("bindingModel"),
            "decision": "reachableBinding",
            "path": deepcopy(item.get("anchorPath") or []),
        }
        for item in discovery["acceptedMembers"]
        if item["kind"] in {"metric", "dimension"} and "anchorPath" in item
    )
    return {
        "anchorModel": request.get("anchorModel"),
        "includeReachable": request["includeReachable"],
        "maxDepth": request["maxDepth"],
        "cyclePolicy": "SIMPLE_PATHS_ONLY",
        "schema": schema,
        "pathDecisions": path_decisions,
        "memberDiscovery": deepcopy(discovery),
    }


def route_decision(
    request: dict[str, Any],
    member: dict[str, Any],
    route: dict[str, Any],
) -> str:
    relationships = [step["relationship"] for step in route.get("path") or []]
    if any(
        item.get("model") == route.get("model")
        and item.get("relationshipPath") == relationships
        for item in member.get("inputs") or []
    ):
        return "calculationInputPath"
    if decision := member.get("routeDecision"):
        return str(decision)
    member_name = member.get("name")
    member_alias = member.get("alias")
    if any(
        candidate.get("name") == member_name
        and candidate.get("alias", candidate.get("name")) == member_alias
        and candidate.get("relationshipPath") == relationships
        for candidates in (
            request.get("dimensions") or [],
            request.get("attributes") or [],
            request.get("dimensionCalculations") or [],
        )
        for candidate in candidates
    ):
        return "explicitRelationshipPath"
    if any(
        hint.get("relationshipPath") == relationships
        for hints in request["pathHints"].values()
        for hint in hints.values()
    ):
        return "pathHint"
    return "uniqueSafePath"


def reachable_nodes_bfs(
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    anchors: list[str],
    max_depth: int,
) -> set[str]:
    distances = {anchor: 0 for anchor in anchors}
    queue = deque(anchors)
    while queue:
        current = queue.popleft()
        depth = distances[current]
        if depth >= max_depth:
            continue
        for step in adjacency.get(current, []):
            target = step["to"]
            if target in distances:
                continue
            distances[target] = depth + 1
            queue.append(target)
    return set(distances)


def declared_adjacency(state: GraphState) -> dict[str, list[dict[str, str]]]:
    """Expose every configured relationship for discovery, even if unqueryable."""

    adjacency: dict[str, list[dict[str, str]]] = {model: [] for model in state.nodes}
    for edge in state.edges.values():
        models = edge.get("declaredModels") or []
        if not (
            isinstance(models, list)
            and len(models) == 2
            and all(isinstance(model, str) and model in state.nodes for model in models)
        ):
            continue
        adjacency[models[0]].append({"to": models[1]})
        adjacency[models[1]].append({"to": models[0]})
    return adjacency


def build_graph_explain(
    facts: list[dict[str, Any]],
    *,
    strategy: str,
    merge_plan: dict[str, Any] | None,
    max_depth: int,
    output_grain: dict[str, Any],
    discovery: dict[str, Any],
    post_calculations: list[dict[str, Any]],
    path_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "maxDepth": max_depth,
        "cyclePolicy": "SIMPLE_PATHS_ONLY",
        "relationshipSource": "relationships.yml",
        "outputGrain": deepcopy(output_grain),
        "memberDiscovery": deepcopy(discovery),
        "pathDecisions": deepcopy(path_decisions),
        "facts": [
            {
                "sourceModel": fact["sourceModel"],
                "strategy": fact["strategy"],
                "sourceKeys": list(fact["sourceKeys"]),
                "dateRange": deepcopy(fact.get("dateRange")),
                "relationPartitions": deepcopy(fact.get("relationPartitions") or {}),
                "metrics": [
                    {
                        "name": metric["name"],
                        "alias": metric["alias"],
                        "binding": metric["binding"],
                        "bindingModel": metric.get("bindingModel")
                        or fact["sourceModel"],
                        "masterModel": metric.get("masterModel"),
                        "isMaster": metric.get("isMaster", False),
                        "additivity": metric["additivity"],
                        "blockedDimensions": metric["blockedDimensions"],
                        "additivitySource": metric.get("additivitySource"),
                        "bindingModels": metric.get("bindingModels"),
                        "routes": metric.get("routes"),
                        "modelAliases": metric.get("modelAliases"),
                    }
                    for metric in fact["metrics"]
                ],
                "dimensions": [
                    {
                        "name": dimension["name"],
                        "alias": dimension["alias"],
                        "bindingModel": dimension["bindingModel"],
                        "bindingModels": dimension.get("bindingModels"),
                        "isMaster": dimension["isMaster"],
                        "hops": dimension["hops"],
                        "path": dimension["path"],
                        "routes": dimension.get("routes"),
                        "modelAliases": dimension.get("modelAliases"),
                        "routeDecision": dimension.get("routeDecision"),
                    }
                    for dimension in fact["dimensions"]
                ],
                "safety": {
                    "fanout": fact["fanout"],
                    "fanoutSemantics": fact["fanoutSemantics"],
                    "additiveAcrossSelectedDimension": (
                        fact["fanoutSemantics"] != "REPEAT_NON_ADDITIVE"
                    ),
                    "preAggregated": fact["fanout"],
                    "deduplicatedMapping": fact["fanout"],
                    "bridgeAllocation": any(
                        step["traversal"] == "BRIDGE"
                        for dimension in fact["dimensions"]
                        for step in member_steps(dimension)
                    ),
                },
                "stages": fact["stages"],
            }
            for fact in facts
        ],
        "merge": deepcopy(merge_plan),
        "postAggregate": deepcopy(post_calculations),
    }
