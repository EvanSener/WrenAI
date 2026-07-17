"""Build per-fact relational plans and validate shared query grain."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from wren.semantic_graph.advanced_bridge import normalized_bridge_policy
from wren.semantic_graph.advanced_dimensions import (
    resolve_attribute,
    resolve_dimension,
    resolve_dimension_calculation,
)
from wren.semantic_graph.advanced_member_routes import (
    member_paths,
    member_routes,
    member_steps,
)
from wren.semantic_graph.advanced_metrics import plan_metric, validate_metric_policies
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.partition import plan_relation_partitions
from wren.semantic_graph.planner import plan_virtual_cube


def plan_fact(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    fact: dict[str, Any],
    *,
    dimensions: list[dict[str, Any]],
    attributes: list[dict[str, Any]],
    dimension_calculations: list[dict[str, Any]],
    entity_grain: list[dict[str, Any]],
    fanout_mode: str,
    max_depth: int,
    fact_index: int,
) -> dict[str, Any]:
    source = fact["sourceModel"]
    node = state.nodes.get(source)
    if node is None:
        raise GraphPlanningError(
            "GRAPH_FACT_SOURCE_NOT_FOUND",
            f"fact source '{source}' is not a semantic graph node",
            details={"sourceModel": source},
        )
    metrics = [
        deepcopy(metric_request["_compiledMetric"])
        if "_compiledMetric" in metric_request
        else plan_metric(state, source, metric_request)
        for metric_request in fact["metrics"]
    ]
    dimension_plans = [
        resolve_dimension(
            state,
            adjacency,
            source=source,
            request=dimension,
            max_depth=max_depth,
        )
        for dimension in dimensions
    ]
    dimension_plans.extend(
        resolve_attribute(
            state,
            adjacency,
            source=source,
            request=attribute,
            max_depth=max_depth,
        )
        for attribute in attributes
    )
    dimension_plans.extend(
        resolve_dimension_calculation(
            state,
            adjacency,
            source=source,
            request=calculation,
            max_depth=max_depth,
        )
        for calculation in dimension_calculations
    )
    entity_plans = [resolve_entity_grain(node, item) for item in entity_grain]
    traversals = {
        step["traversal"]
        for dimension in dimension_plans
        for step in member_steps(dimension)
    }
    fanout = bool(traversals & {"FANOUT", "BRIDGE"})
    has_unallocated_fanout = "FANOUT" in traversals
    has_bridge = "BRIDGE" in traversals
    if has_unallocated_fanout and fanout_mode != "repeat":
        raise GraphPlanningError(
            "GRAPH_FANOUT_ALLOCATION_REQUIRED",
            "one-to-many traversal can repeat one fact across multiple dimension "
            "members; provide governed allocation or explicitly set fanoutMode: repeat",
            details={
                "sourceModel": source,
                "fanoutMode": fanout_mode,
                "dimensions": [item["name"] for item in dimension_plans],
                "hint": (
                    "use fanoutMode: repeat only when consumers understand that "
                    "the metric is not additive across the repeated dimension"
                ),
            },
        )
    remote_metric_routes = [
        route
        for metric in metrics
        if metric.get("routes")
        for route in member_routes(metric)
        if route.get("model") != source
    ]
    if fanout and remote_metric_routes:
        raise GraphPlanningError(
            "GRAPH_REMOTE_METRIC_WITH_FANOUT_UNSUPPORTED",
            "a leaf-field metric cannot be evaluated in a fact plan that also "
            "expands a fanout dimension",
            details={
                "sourceModel": source,
                "metricModels": sorted(
                    {
                        route["model"]
                        for route in remote_metric_routes
                        if isinstance(route.get("model"), str)
                    }
                ),
                "hint": "split the leaf metric into its own fact before merging grains",
            },
        )
    validate_metric_policies(metrics, dimension_plans, fanout=fanout)
    validate_fanout_shape(dimension_plans)

    has_selector = bool(
        attributes or dimension_calculations or remote_metric_routes
    ) or any(
        any(key in dimension for key in ("bindingModel", "relationshipPath", "role"))
        for dimension in dimensions
    )
    index_depth = state.index.get("maxHops", 0)
    index_fast_path = isinstance(index_depth, int) and all(
        dimension["hops"] <= index_depth for dimension in dimension_plans
    )
    if dimension_plans and not fanout and not has_selector and index_fast_path:
        plan_virtual_cube(
            state.graph,
            state.index,
            source_model=source,
            metrics=[metric["name"] for metric in metrics],
            dimensions=[dimension["name"] for dimension in dimension_plans],
            date_range=fact.get("dateRange"),
        )

    relation_models = {source}
    for member in (*dimension_plans, *metrics):
        for step in member_steps(member):
            relation_models.update((step["from"], step["to"]))
            if step.get("traversal") == "BRIDGE":
                relation_models.add(normalized_bridge_policy(state, step)["model"])
    relation_partitions = plan_relation_partitions(
        state.nodes,
        relation_models,
        date_range=fact.get("dateRange"),
        source_model=source,
    )

    source_keys = source_grain_fields(node)
    if fanout and not source_keys:
        raise GraphPlanningError(
            "GRAPH_FANOUT_GRAIN_REQUIRED",
            f"fact '{source}' needs a primary key or declared grain before fanout traversal",
            details={"sourceModel": source, "grain": node.get("grain")},
        )
    strategy = "PREAGGREGATE_DEDUP_MAPPING" if fanout else "DIRECT_AGGREGATE"
    prefix = f"fact_{fact_index}"
    stages = fact_stages(
        prefix,
        strategy=strategy,
        has_bridge=has_bridge,
    )
    return {
        "id": prefix,
        "sourceModel": source,
        "sourceGrain": deepcopy(node.get("grain")),
        "sourceKeys": source_keys,
        "dateRange": deepcopy(fact.get("dateRange")),
        "relationPartitions": relation_partitions,
        "metrics": metrics,
        "dimensions": dimension_plans,
        "entityGrain": entity_plans,
        "strategy": strategy,
        "fanout": fanout,
        "fanoutSemantics": (
            "REPEAT_NON_ADDITIVE"
            if has_unallocated_fanout
            else "BRIDGE_ALLOCATED"
            if has_bridge
            else "NONE"
        ),
        "stages": stages,
        "outputRelation": prefix,
    }


def resolve_entity_grain(
    node: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    name = request["name"]
    role = request.get("role")
    candidates = [
        entity
        for entity in node.get("entities") or []
        if entity.get("name") == name and (role is None or entity.get("role") == role)
    ]
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entity in candidates:
        fields = tuple(entity.get("fields") or [])
        unique[(fields, entity.get("role"))] = entity
    if not unique:
        raise GraphPlanningError(
            "GRAPH_ENTITY_GRAIN_NOT_FOUND",
            f"entity '{name}' is not declared on fact '{node['name']}'",
            details={"entity": name, "sourceModel": node["name"], "role": role},
        )
    if len(unique) != 1:
        raise GraphPlanningError(
            "GRAPH_ENTITY_GRAIN_AMBIGUOUS",
            f"entity '{name}' has multiple roles on fact '{node['name']}'",
            details={
                "entity": name,
                "sourceModel": node["name"],
                "candidates": [
                    {"role": item.get("role"), "fields": item.get("fields")}
                    for item in unique.values()
                ],
                "hint": "set entityGrain[].role",
            },
        )
    entity = next(iter(unique.values()))
    fields = entity.get("fields") or []
    if not fields:
        raise GraphPlanningError(
            "GRAPH_ENTITY_GRAIN_FIELDS_MISSING",
            f"entity '{name}' has no key fields on fact '{node['name']}'",
        )
    aliases = [
        request["alias"] if len(fields) == 1 else f"{request['alias']}__{position + 1}"
        for position in range(len(fields))
    ]
    return {
        "name": name,
        "alias": request["alias"],
        "role": entity.get("role"),
        "type": entity.get("type"),
        "fields": list(fields),
        "outputAliases": aliases,
    }


def validate_entity_grain_compatibility(facts: list[dict[str, Any]]) -> None:
    expected = [
        (entity["name"], entity["role"], len(entity["fields"]))
        for entity in facts[0]["entityGrain"]
    ]
    for fact in facts[1:]:
        actual = [
            (entity["name"], entity["role"], len(entity["fields"]))
            for entity in fact["entityGrain"]
        ]
        if actual != expected:
            raise GraphPlanningError(
                "GRAPH_MULTI_FACT_ENTITY_GRAIN_INCOMPATIBLE",
                "multi-fact sources do not expose the same entity grain",
                details={
                    "expected": expected,
                    "sourceModel": fact["sourceModel"],
                    "actual": actual,
                },
            )


def validate_fanout_shape(dimensions: list[dict[str, Any]]) -> None:
    branches: set[tuple[str, ...]] = set()
    for dimension in dimensions:
        for path in member_paths(dimension):
            unsafe_index = next(
                (
                    index
                    for index, step in enumerate(path)
                    if step["traversal"] in {"FANOUT", "BRIDGE"}
                ),
                None,
            )
            if unsafe_index is not None:
                branches.add(
                    tuple(step["relationship"] for step in path[: unsafe_index + 1])
                )
    if len(branches) > 1:
        raise GraphPlanningError(
            "GRAPH_MULTIPLE_FANOUT_BRANCHES_UNSUPPORTED",
            "one fact cannot safely expand independent fanout branches in one query",
            details={"fanoutBranches": sorted(branches)},
        )


def validate_output_aliases(facts: list[dict[str, Any]]) -> None:
    claims: dict[str, list[dict[str, str]]] = defaultdict(list)
    for dimension in facts[0]["dimensions"]:
        claims[dimension["alias"].casefold()].append(
            {
                "kind": dimension.get("memberKind", "dimension"),
                "name": dimension["name"],
                "alias": dimension["alias"],
            }
        )
    for entity in facts[0]["entityGrain"]:
        for alias in entity["outputAliases"]:
            claims[alias.casefold()].append(
                {"kind": "entity", "name": entity["name"], "alias": alias}
            )
    for fact in facts:
        for metric in fact["metrics"]:
            claims[metric["alias"].casefold()].append(
                {
                    "kind": "metric",
                    "name": metric["name"],
                    "alias": metric["alias"],
                    "sourceModel": fact["sourceModel"],
                }
            )
    conflicts = [values for values in claims.values() if len(values) > 1]
    if conflicts:
        raise GraphPlanningError(
            "GRAPH_OUTPUT_ALIAS_CONFLICT",
            "metric, dimension, and entity output aliases must be globally unique",
            details={"conflicts": conflicts},
        )


def source_grain_fields(node: dict[str, Any]) -> list[str]:
    primary = node.get("primaryKey") or []
    if isinstance(primary, list) and all(isinstance(item, str) for item in primary):
        if primary:
            return list(dict.fromkeys(primary))
    grain = (node.get("grain") or {}).get("fields") or []
    if isinstance(grain, list) and all(isinstance(item, str) for item in grain):
        return list(dict.fromkeys(grain))
    return []


def fact_stages(
    prefix: str, *, strategy: str, has_bridge: bool
) -> list[dict[str, Any]]:
    if strategy == "DIRECT_AGGREGATE":
        return [{"id": prefix, "kind": "FACT_AGGREGATE"}]
    stages = [
        {"id": f"{prefix}_preaggregate", "kind": "FACT_PREAGGREGATE"},
        {"id": f"{prefix}_mapping_raw", "kind": "DEDUP_MAPPING_INPUT"},
        {"id": f"{prefix}_mapping", "kind": "DEDUPLICATED_MAPPING"},
        {"id": prefix, "kind": "GRAIN_ROLLUP"},
    ]
    if has_bridge:
        stages[2]["allocation"] = "BRIDGE_POLICY"
    return stages


def overall_strategy(facts: list[dict[str, Any]]) -> str:
    if len(facts) > 1:
        return "MULTI_FACT_AGGREGATE_MERGE"
    if facts[0]["fanout"]:
        if facts[0]["fanoutSemantics"] == "REPEAT_NON_ADDITIVE":
            return "SINGLE_FACT_FANOUT_REPEAT"
        return "SINGLE_FACT_BRIDGE_ALLOCATED"
    return "SINGLE_FACT_SAFE"


def public_fact_plan(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": fact["id"],
        "sourceModel": fact["sourceModel"],
        "sourceGrain": deepcopy(fact["sourceGrain"]),
        "sourceKeys": list(fact["sourceKeys"]),
        "dateRange": deepcopy(fact.get("dateRange")),
        "relationPartitions": deepcopy(fact.get("relationPartitions") or {}),
        "strategy": fact["strategy"],
        "metrics": deepcopy(fact["metrics"]),
        "dimensions": deepcopy(fact["dimensions"]),
        "entityGrain": deepcopy(fact["entityGrain"]),
        "fanoutSemantics": fact["fanoutSemantics"],
        "stages": deepcopy(fact["stages"]),
        "outputRelation": fact["outputRelation"],
    }
