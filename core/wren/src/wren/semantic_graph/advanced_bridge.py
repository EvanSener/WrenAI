"""Validation for compiled many-to-many Bridge/Allocation policies."""

from __future__ import annotations

from typing import Any

from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError

_ALLOCATION_MODES = frozenset({"weighted", "proportional", "custom"})


def normalized_bridge_policy(state: GraphState, step: dict[str, Any]) -> dict[str, Any]:
    edge = state.edges[step["relationship"]]
    raw = edge.get("bridgePolicy")
    if not isinstance(raw, dict):
        raise GraphPlanningError(
            "GRAPH_MANY_TO_MANY_POLICY_REQUIRED",
            f"relationship '{edge['name']}' has no Bridge/Allocation policy",
            details={"relationship": edge["name"]},
        )
    bridge_model = raw.get("model")
    allocation = raw.get("allocationExpression")
    mode = str(raw.get("allocationMode") or "weighted").lower()
    declared = edge.get("declaredModels") or []
    forward = len(declared) == 2 and step["from"] == declared[0]
    source_relationship = (
        raw.get("sourceRelationship") if forward else raw.get("targetRelationship")
    )
    target_relationship = (
        raw.get("targetRelationship") if forward else raw.get("sourceRelationship")
    )
    problems: list[str] = []
    if not isinstance(bridge_model, str) or bridge_model not in state.nodes:
        problems.append("model must reference an existing bridge node")
    if (
        not isinstance(source_relationship, str)
        or source_relationship not in state.edges
    ):
        problems.append("sourceRelationship must reference an existing edge")
    if (
        not isinstance(target_relationship, str)
        or target_relationship not in state.edges
    ):
        problems.append("targetRelationship must reference an existing edge")
    if not isinstance(allocation, str) or not allocation.strip():
        problems.append("allocationExpression is required")
    if mode not in _ALLOCATION_MODES:
        problems.append("allocationMode must be weighted, proportional, or custom")
    if not problems:
        assert isinstance(bridge_model, str)
        assert isinstance(source_relationship, str)
        assert isinstance(target_relationship, str)
        source_edge = state.edges[source_relationship]
        target_edge = state.edges[target_relationship]
        if source_edge.get("cardinalityValidation") != "verified":
            problems.append("sourceRelationship cardinality is not verified")
        if target_edge.get("cardinalityValidation") != "verified":
            problems.append("targetRelationship cardinality is not verified")
        if set(source_edge.get("declaredModels") or []) != {
            step["from"],
            bridge_model,
        }:
            problems.append("sourceRelationship does not connect source to bridge")
        if set(target_edge.get("declaredModels") or []) != {
            bridge_model,
            step["to"],
        }:
            problems.append("targetRelationship does not connect bridge to target")
    if problems:
        raise GraphPlanningError(
            "GRAPH_BRIDGE_POLICY_INVALID",
            f"relationship '{edge['name']}' has an invalid bridgePolicy",
            details={"relationship": edge["name"], "problems": problems, "policy": raw},
        )
    return {
        "model": bridge_model,
        "sourceRelationship": source_relationship,
        "targetRelationship": target_relationship,
        "allocationExpression": allocation,
        "allocationMode": mode,
    }
