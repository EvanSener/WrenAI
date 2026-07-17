"""Discover globally unique metric bindings and common reachable dimensions."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from wren.semantic_graph.advanced_bridge import normalized_bridge_policy
from wren.semantic_graph.advanced_dimensions import resolve_dimension
from wren.semantic_graph.advanced_metrics import assign_metric_calculations
from wren.semantic_graph.advanced_request import apply_member_hint
from wren.semantic_graph.advanced_traversal import (
    enumerate_paths,
    path_from_hint,
    public_step,
    reject_unprotected_m2m_hint,
    unprotected_many_to_many_edges,
)
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.binding_policy import allowed_bindings, enforce_master_model
from wren.semantic_graph.model import GraphPlanningError


def prepare_members(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = deepcopy(request)
    automatic = bool(request["metricsWildcard"] or request["dimensionsWildcard"])
    discovery: dict[str, Any] = {
        "mode": "automatic" if automatic else "explicit",
        "acceptedMembers": [],
        "rejectedMembers": [],
    }
    if request["facts"]:
        facts = deepcopy(request["facts"])
        for fact in facts:
            for metric in fact["metrics"]:
                discovery["acceptedMembers"].append(
                    {
                        "kind": "metric",
                        "name": metric["name"],
                        "bindingModel": fact["sourceModel"],
                        "reason": "explicit fact binding",
                    }
                )
    else:
        facts = _discover_metric_facts(state, adjacency, request, discovery)
    assign_metric_calculations(
        state,
        adjacency,
        facts,
        request["metricCalculations"],
        discovery,
        max_depth=request["maxDepth"],
    )
    prepared["facts"] = facts

    if request["dimensionsWildcard"]:
        prepared["dimensions"] = _discover_dimensions(
            state, adjacency, request, facts, discovery
        )
    else:
        for dimension in request["dimensions"]:
            discovery["acceptedMembers"].append(
                {
                    "kind": "dimension",
                    "name": dimension["name"],
                    "reason": "explicit request; validated during planning",
                }
            )
    for attribute in request["attributes"]:
        discovery["acceptedMembers"].append(
            {
                "kind": "attribute",
                "name": f"{attribute['model']}.{attribute['field']}",
                "alias": attribute["alias"],
                "reason": "explicit projection; validated during planning",
            }
        )
    for calculation in request["dimensionCalculations"]:
        discovery["acceptedMembers"].append(
            {
                "kind": "calculation",
                "calculationKind": "dimension",
                "name": calculation["name"],
                "alias": calculation["alias"],
                "reason": "validated SQLGlot dimension calculation",
            }
        )
    for calculation in request["postCalculations"]:
        discovery["acceptedMembers"].append(
            {
                "kind": "calculation",
                "calculationKind": "post_metric",
                "name": calculation["name"],
                "alias": calculation["alias"],
                "reason": "validated after fact aggregation and grain merge",
            }
        )
    return prepared, discovery


def discover_reachable_catalog(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Describe governed members reachable from an anchor without selecting them.

    Raw node attributes are enumerated by ``virtual_wide_table``.  This helper
    adds global Metric and Dimension decisions while deliberately producing no
    fact plan and therefore no SQL projection.
    """

    anchor = request.get("anchorModel")
    if not isinstance(anchor, str):
        facts = request.get("facts") or []
        anchor = facts[0].get("sourceModel") if facts else None
    if not isinstance(anchor, str) or anchor not in state.nodes:
        raise GraphPlanningError(
            "GRAPH_DISCOVERY_ANCHOR_REQUIRED",
            "reachable member discovery requires anchorModel or a selected fact",
        )

    discovery: dict[str, Any] = {
        "mode": "reachability",
        "anchorModel": anchor,
        "acceptedMembers": [],
        "rejectedMembers": [],
    }
    for name in sorted(state.metrics):
        candidate = apply_member_hint(
            {"name": name, "alias": name},
            request["pathHints"]["metrics"].get(name),
        )
        try:
            source, path = select_metric_binding_from_anchor(
                state,
                adjacency,
                anchor=anchor,
                metric=candidate,
                max_depth=request["maxDepth"],
            )
        except GraphPlanningError as exc:
            discovery["rejectedMembers"].append(
                {
                    "kind": "metric",
                    "name": name,
                    "code": exc.code,
                    "reason": str(exc),
                    "details": deepcopy(exc.details),
                }
            )
            continue
        discovery["acceptedMembers"].append(
            {
                "kind": "metric",
                "name": name,
                "bindingModel": source,
                "anchorPath": [public_step(step) for step in path],
                "reason": "reachable global metric binding",
            }
        )

    for name in sorted(state.dimensions):
        candidate = apply_member_hint(
            {"name": name, "alias": name},
            request["pathHints"]["dimensions"].get(name),
        )
        try:
            resolved = resolve_dimension(
                state,
                adjacency,
                source=anchor,
                request=candidate,
                max_depth=request["maxDepth"],
            )
        except GraphPlanningError as exc:
            discovery["rejectedMembers"].append(
                {
                    "kind": "dimension",
                    "name": name,
                    "code": exc.code,
                    "reason": str(exc),
                    "details": deepcopy(exc.details),
                }
            )
            continue
        discovery["acceptedMembers"].append(
            {
                "kind": "dimension",
                "name": name,
                "bindingModel": resolved["bindingModel"],
                "anchorPath": deepcopy(resolved["path"]),
                "reason": "reachable global dimension binding",
            }
        )
    return discovery


def _discover_metric_facts(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    discovery: dict[str, Any],
) -> list[dict[str, Any]]:
    anchor = request["anchorModel"]
    assert isinstance(anchor, str)
    metric_requests = (
        [{"name": name, "alias": name} for name in sorted(state.metrics)]
        if request["metricsWildcard"]
        else deepcopy(request["topMetrics"])
    )
    metric_requests = [
        apply_member_hint(metric, request["pathHints"]["metrics"].get(metric["name"]))
        for metric in metric_requests
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for metric in metric_requests:
        try:
            source, path = select_metric_binding_from_anchor(
                state,
                adjacency,
                anchor=anchor,
                metric=metric,
                max_depth=request["maxDepth"],
            )
        except GraphPlanningError as exc:
            rejected = {
                "kind": "metric",
                "name": metric["name"],
                "code": exc.code,
                "reason": str(exc),
                "details": deepcopy(exc.details),
            }
            if request["metricsWildcard"]:
                discovery["rejectedMembers"].append(rejected)
                continue
            raise
        grouped[source].append({"name": metric["name"], "alias": metric["alias"]})
        discovery["acceptedMembers"].append(
            {
                "kind": "metric",
                "name": metric["name"],
                "bindingModel": source,
                "anchorPath": [public_step(step) for step in path],
                "reason": "reachable global metric binding",
            }
        )
    facts = [
        {
            "sourceModel": source,
            "metrics": metrics,
            "dateRange": deepcopy(request.get("dateRange")),
        }
        for source, metrics in sorted(grouped.items())
    ]
    if not facts:
        raise GraphPlanningError(
            "GRAPH_NO_REACHABLE_METRICS",
            f"anchor '{anchor}' has no accepted metric bindings",
            details=deepcopy(discovery),
        )
    return facts


def _discover_dimensions(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    request: dict[str, Any],
    facts: list[dict[str, Any]],
    discovery: dict[str, Any],
) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    for name in sorted(state.dimensions):
        candidate = apply_member_hint(
            {"name": name, "alias": name},
            request["pathHints"]["dimensions"].get(name),
        )
        decisions: list[dict[str, Any]] = []
        try:
            for fact in facts:
                resolved = resolve_dimension(
                    state,
                    adjacency,
                    source=fact["sourceModel"],
                    request=candidate,
                    max_depth=request["maxDepth"],
                )
                decisions.append(
                    {
                        "sourceModel": fact["sourceModel"],
                        "bindingModel": resolved["bindingModel"],
                        "path": resolved["path"],
                    }
                )
        except GraphPlanningError as exc:
            discovery["rejectedMembers"].append(
                {
                    "kind": "dimension",
                    "name": name,
                    "code": exc.code,
                    "reason": str(exc),
                    "details": deepcopy(exc.details),
                }
            )
            continue
        dimensions.append(candidate)
        discovery["acceptedMembers"].append(
            {
                "kind": "dimension",
                "name": name,
                "pathDecisions": decisions,
                "reason": "reachable from every selected fact binding",
            }
        )
    return dimensions


def select_metric_binding_from_anchor(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    anchor: str,
    metric: dict[str, Any],
    max_depth: int,
) -> tuple[str, list[dict[str, Any]]]:
    name = metric["name"]
    if name not in state.metrics:
        raise GraphPlanningError(
            "GRAPH_METRIC_NOT_FOUND", f"metric '{name}' is not defined"
        )
    definition = state.metrics[name]
    candidates = sorted(
        {
            binding["model"]
            for binding in allowed_bindings(
                definition,
                (item for item in state.metric_bindings if item.get("metric") == name),
            )
        }
    )
    requested_source = metric.get("sourceModel") or metric.get("bindingModel")
    if requested_source:
        enforce_master_model(
            member_kind="metric",
            member_name=name,
            definition=definition,
            requested_model=requested_source,
        )
        candidates = [source for source in candidates if source == requested_source]
    if not candidates:
        raise GraphPlanningError(
            "GRAPH_METRIC_BINDING_NOT_FOUND",
            f"metric '{name}' has no permitted binding",
            details={"metric": name, "requestedSource": requested_source},
        )
    # An anchor-local binding is the deterministic representation of a global
    # metric for this virtual table.  Do not search equivalent remote bindings.
    if requested_source is None and anchor in candidates:
        return anchor, []
    relationship_path = metric.get("relationshipPath")
    role = metric.get("role")
    paths: list[tuple[str, list[dict[str, Any]]]] = []
    if relationship_path is not None:
        reject_unprotected_m2m_hint(state, relationship_path)
        path = path_from_hint(
            adjacency,
            source=anchor,
            relationships=relationship_path,
            max_depth=max_depth,
        )
        if path and path[-1]["to"] in candidates:
            paths.append((path[-1]["to"], path))
        elif not path and anchor in candidates:
            paths.append((anchor, []))
    else:
        for candidate in candidates:
            remaining = 2 - len(paths)
            if remaining <= 0:
                break
            paths.extend(
                (candidate, path)
                for path in enumerate_paths(
                    adjacency,
                    source=anchor,
                    target=candidate,
                    max_depth=max_depth,
                    limit=remaining,
                    required_role=role,
                )
            )
    unique = {
        (
            source,
            tuple((step["relationship"], step["from"], step["to"]) for step in path),
        ): (source, path)
        for source, path in paths
    }
    if not unique:
        unprotected = unprotected_many_to_many_edges(state, anchor, candidates)
        if unprotected:
            raise GraphPlanningError(
                "GRAPH_MANY_TO_MANY_POLICY_REQUIRED",
                f"metric '{name}' is behind an ungoverned many-to-many relationship",
                details={"metric": name, "relationships": unprotected},
            )
        raise GraphPlanningError(
            "GRAPH_METRIC_PATH_NOT_FOUND",
            f"metric '{name}' has no binding reachable from anchor '{anchor}'",
            details={"metric": name, "anchorModel": anchor, "maxDepth": max_depth},
        )
    if len(unique) > 1:
        raise GraphPlanningError(
            "GRAPH_METRIC_BINDING_AMBIGUOUS",
            f"metric '{name}' has multiple reachable bindings or paths",
            details={
                "metric": name,
                "anchorModel": anchor,
                "candidatePaths": [
                    {
                        "bindingModel": source,
                        "path": [public_step(step) for step in path],
                    }
                    for source, path in unique.values()
                ],
                "hint": "set pathHints.metrics.<name>.sourceModel and relationshipPath",
            },
        )
    source, path = next(iter(unique.values()))
    for step in path:
        if step["traversal"] == "BRIDGE":
            normalized_bridge_policy(state, step)
    return source, path
