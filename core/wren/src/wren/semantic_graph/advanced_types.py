"""Shared runtime state for the advanced semantic graph planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wren.metric_compiler import dialect_for
from wren.semantic_graph.model import GraphPlanningError


@dataclass(frozen=True)
class GraphState:
    graph: dict[str, Any]
    index: dict[str, Any]
    nodes: dict[str, dict[str, Any]]
    edges: dict[str, dict[str, Any]]
    metrics: dict[str, dict[str, Any]]
    dimensions: dict[str, dict[str, Any]]
    metric_bindings: tuple[dict[str, Any], ...]
    dimension_bindings: tuple[dict[str, Any], ...]
    dialect: str | None


def make_state(graph: dict[str, Any], index: dict[str, Any]) -> GraphState:
    if not isinstance(graph, dict) or not isinstance(index, dict):
        raise GraphPlanningError(
            "GRAPH_REQUEST_ARTIFACT_INVALID",
            "semantic graph and queryability index must be objects",
        )
    return GraphState(
        graph=graph,
        index=index,
        nodes={item["name"]: item for item in graph.get("nodes") or []},
        edges={item["name"]: item for item in graph.get("edges") or []},
        metrics={item["name"]: item for item in graph.get("metrics") or []},
        dimensions={item["name"]: item for item in graph.get("dimensions") or []},
        metric_bindings=tuple(graph.get("metricBindings") or []),
        dimension_bindings=tuple(graph.get("dimensionBindings") or []),
        dialect=dialect_for((graph.get("project") or {}).get("dataSource")),
    )
