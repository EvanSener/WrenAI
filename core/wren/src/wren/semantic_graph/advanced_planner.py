"""Public facade for governed Dynamic Virtual Cube planning.

The existing :func:`wren.semantic_graph.planner.plan_virtual_cube` API remains
unchanged.  This additive facade orchestrates structured requests, arbitrary
depth graph traversal, fanout-safe staging, multi-fact merging, explain output,
and MaxCompute/Hive SQL rendering through focused implementation modules.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from wren.semantic_graph.advanced_discovery import (
    discover_reachable_catalog,
    prepare_members,
)
from wren.semantic_graph.advanced_explain import (
    build_graph_explain,
    virtual_wide_table,
)
from wren.semantic_graph.advanced_fact import (
    overall_strategy,
    plan_fact,
    public_fact_plan,
    validate_entity_grain_compatibility,
    validate_output_aliases,
)
from wren.semantic_graph.advanced_post_calculations import (
    compile_post_calculations,
    render_post_aggregate,
)
from wren.semantic_graph.advanced_request import normalize_request
from wren.semantic_graph.advanced_sql import (
    output_grain_aliases,
    render_fact_ctes,
    render_final_select,
    render_multi_fact_merge,
    render_with_query,
)
from wren.semantic_graph.advanced_traversal import build_adjacency
from wren.semantic_graph.advanced_types import make_state
from wren.semantic_graph.model import GraphPlanningError

__all__ = [
    "GraphPlanningError",
    "plan_dynamic_virtual_cube",
    "plan_graph_query",
]


def plan_graph_query(
    semantic_graph: dict[str, Any],
    queryability_index: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Compile a structured graph request into plan, explain, and SQL.

    ``facts`` supports explicit source bindings.  Alternatively, ``anchorModel``
    plus ``metrics``/``dimensions`` (including ``*``) discovers global semantic
    members.  ``maxDepth`` bounds cycle-safe simple-path traversal and
    ``pathHints`` resolves graphs with multiple valid paths.  Raw ``attributes``
    and SQLGlot-validated ``calculations`` extend the virtual wide-table schema.
    """

    state = make_state(semantic_graph, queryability_index)
    normalized = normalize_request(request, state)
    adjacency = build_adjacency(state)
    if _is_discovery_only(normalized):
        discovery = discover_reachable_catalog(state, adjacency, normalized)
        virtual = virtual_wide_table(state, adjacency, normalized, [], discovery)
        return {
            "schemaVersion": 1,
            "kind": "VIRTUAL_WIDE_TABLE_DISCOVERY",
            "strategy": "DISCOVERY_ONLY",
            "request": _public_request(normalized),
            "relationalPlan": {
                "kind": "VIRTUAL_WIDE_TABLE_DISCOVERY",
                "strategy": "DISCOVERY_ONLY",
                "facts": [],
                "merge": None,
                "outputGrain": {"dimensions": [], "entities": [], "columns": []},
                "outputMetrics": [],
                "virtualWideTable": virtual,
            },
            "graphExplain": {
                "strategy": "DISCOVERY_ONLY",
                "maxDepth": normalized["maxDepth"],
                "cyclePolicy": "SIMPLE_PATHS_ONLY",
                "relationshipSource": "relationships.yml",
                "memberDiscovery": deepcopy(discovery),
            },
            "sql": None,
        }
    normalized, discovery = prepare_members(state, adjacency, normalized)
    if normalized["includeReachable"]:
        catalog = discover_reachable_catalog(state, adjacency, normalized)
        discovery = _merge_discovery(discovery, catalog)

    fact_plans = [
        plan_fact(
            state,
            adjacency,
            fact,
            dimensions=normalized["dimensions"],
            attributes=normalized["attributes"],
            dimension_calculations=normalized["dimensionCalculations"],
            entity_grain=normalized["entityGrain"],
            fanout_mode=normalized["fanoutMode"],
            max_depth=normalized["maxDepth"],
            fact_index=index,
        )
        for index, fact in enumerate(normalized["facts"])
    ]
    validate_entity_grain_compatibility(fact_plans)
    validate_output_aliases(fact_plans)

    ctes: list[tuple[str, str]] = []
    for fact in fact_plans:
        ctes.extend(render_fact_ctes(state, fact))
    grain_aliases = output_grain_aliases(fact_plans[0])
    metric_aliases = [
        metric["alias"] for fact in fact_plans for metric in fact["metrics"]
    ]
    merge_plan: dict[str, Any] | None = None
    output_relation = fact_plans[0]["outputRelation"]
    if len(fact_plans) > 1:
        merge_ctes, output_relation, merge_plan = render_multi_fact_merge(
            state, fact_plans, grain_aliases=grain_aliases
        )
        ctes.extend(merge_ctes)

    aggregate_columns = [*grain_aliases, *metric_aliases]
    post_calculations = compile_post_calculations(
        normalized["postCalculations"],
        available_columns=aggregate_columns,
        dialect=state.dialect,
    )
    if post_calculations:
        post_relation = "post_aggregate"
        ctes.append(
            (
                post_relation,
                render_post_aggregate(
                    state,
                    relation=output_relation,
                    input_columns=aggregate_columns,
                    calculations=post_calculations,
                ),
            )
        )
        output_relation = post_relation
    post_aliases = [item["alias"] for item in post_calculations]
    final_sql = render_final_select(
        state,
        relation=output_relation,
        columns=[*aggregate_columns, *post_aliases],
    )
    strategy = overall_strategy(fact_plans)
    output_grain = {
        "dimensions": [item["alias"] for item in fact_plans[0]["dimensions"]],
        "entities": [item["name"] for item in normalized["entityGrain"]],
        "columns": grain_aliases,
    }
    virtual_table = virtual_wide_table(
        state, adjacency, normalized, fact_plans, discovery
    )
    relational_plan = {
        "kind": "DYNAMIC_VIRTUAL_CUBE",
        "strategy": strategy,
        "facts": [public_fact_plan(fact) for fact in fact_plans],
        "merge": deepcopy(merge_plan),
        "outputGrain": output_grain,
        "outputMetrics": [*metric_aliases, *post_aliases],
        "postCalculations": deepcopy(post_calculations),
        "virtualWideTable": virtual_table,
    }
    return {
        "schemaVersion": 1,
        "kind": "DYNAMIC_VIRTUAL_CUBE",
        "strategy": strategy,
        "request": _public_request(normalized),
        "relationalPlan": relational_plan,
        "graphExplain": build_graph_explain(
            fact_plans,
            strategy=strategy,
            merge_plan=merge_plan,
            max_depth=normalized["maxDepth"],
            output_grain=output_grain,
            discovery=discovery,
            post_calculations=post_calculations,
            path_decisions=virtual_table["pathDecisions"],
        ),
        "sql": render_with_query(state, ctes, final_sql),
    }


plan_dynamic_virtual_cube = plan_graph_query


def _public_request(value: Any) -> Any:
    """Remove normalization-only keys while preserving the public request IR."""

    if isinstance(value, dict):
        return {
            key: _public_request(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_public_request(item) for item in value]
    return deepcopy(value)


def _is_discovery_only(request: dict[str, Any]) -> bool:
    return bool(
        request["includeReachable"]
        and not request["facts"]
        and not request["topMetrics"]
        and not request["metricsWildcard"]
        and not request["dimensions"]
        and not request["dimensionsWildcard"]
        and not request["attributes"]
        and not request["dimensionCalculations"]
        and not request["metricCalculations"]
        and not request["postCalculations"]
        and not request["entityGrain"]
    )


def _merge_discovery(
    selected: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(selected)
    merged["reachableCatalog"] = deepcopy(catalog)
    for key in ("acceptedMembers", "rejectedMembers"):
        existing = {
            (
                item.get("kind"),
                item.get("name"),
                item.get("bindingModel"),
                item.get("code"),
            )
            for item in merged[key]
        }
        for item in catalog[key]:
            signature = (
                item.get("kind"),
                item.get("name"),
                item.get("bindingModel"),
                item.get("code"),
            )
            if signature not in existing:
                merged[key].append(deepcopy(item))
                existing.add(signature)
    return merged
