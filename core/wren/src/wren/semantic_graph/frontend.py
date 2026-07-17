"""Open extension point for graph-query input languages.

Frontends translate user-facing input into the versioned dictionary consumed by
``plan_graph_query``.  They never render SQL themselves, so every frontend
shares the same relationship, grain, fanout, and additivity checks.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from wren.semantic_graph.advanced_planner import plan_graph_query
from wren.semantic_graph.model import GraphPlanningError


@runtime_checkable
class GraphQueryFrontend(Protocol):
    """Adapter implemented by natural-language, BI, or future GQL inputs."""

    name: str

    def compile(
        self,
        payload: Any,
        *,
        semantic_graph: dict[str, Any],
        queryability_index: dict[str, Any],
        ontology_graph: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return ``request`` and optional auditable ``resolution`` metadata."""


def plan_frontend_query(
    semantic_graph: dict[str, Any],
    queryability_index: dict[str, Any],
    frontend: GraphQueryFrontend,
    payload: Any,
    *,
    ontology_graph: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one frontend input through the existing graph planner."""

    compiled = frontend.compile(
        payload,
        semantic_graph=semantic_graph,
        queryability_index=queryability_index,
        ontology_graph=ontology_graph,
        options=options,
    )
    if not isinstance(compiled, dict) or not isinstance(compiled.get("request"), dict):
        raise GraphPlanningError(
            "GRAPH_FRONTEND_RESULT_INVALID",
            f"graph frontend '{frontend.name}' must return an object request",
        )
    request = compiled["request"]
    plan = plan_graph_query(semantic_graph, queryability_index, request)
    plan["queryFrontend"] = {
        "name": frontend.name,
        "inputKind": compiled.get("inputKind", "unknown"),
    }
    plan["graphQuery"] = request
    if isinstance(compiled.get("resolution"), dict):
        plan["frontendResolution"] = compiled["resolution"]
    return plan
