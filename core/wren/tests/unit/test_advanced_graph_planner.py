"""Post-implementation coverage for the advanced semantic graph planner."""

from __future__ import annotations

from copy import deepcopy

import pytest
import sqlglot

from wren.semantic_graph.advanced_planner import plan_graph_query
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.queryability import build_queryability_index


def _node(name: str, fields: list[str], primary_key: str) -> dict:
    return {
        "name": name,
        "primaryKey": [primary_key],
        "grain": {"fields": [primary_key], "source": "primary_key"},
        "attributes": [{"name": field, "type": "STRING"} for field in fields],
        "entities": [],
        "relation": {"type": "table", "tableReference": {"table": name}},
    }


def _edge(
    name: str,
    left: str,
    right: str,
    cardinality: str,
    condition: str,
) -> dict:
    if cardinality == "MANY_TO_ONE":
        safe = [[left, right]]
    elif cardinality == "ONE_TO_MANY":
        safe = [[right, left]]
    elif cardinality == "ONE_TO_ONE":
        safe = [[left, right], [right, left]]
    else:
        safe = []
    return {
        "name": name,
        "declaredModels": [left, right],
        "cardinality": cardinality,
        "condition": condition,
        "safeDirections": safe,
        "cardinalityValidation": "disabled"
        if cardinality == "MANY_TO_MANY"
        else "verified",
        "role": None,
        "entity": None,
    }


def _graph(
    *,
    nodes: list[dict],
    edges: list[dict],
    metrics: list[dict],
    dimensions: list[dict],
    metric_bindings: list[dict],
    dimension_bindings: list[dict],
) -> dict:
    return {
        "schemaVersion": 1,
        "project": {"name": "advanced", "dataSource": "maxcompute"},
        "nodes": nodes,
        "edges": edges,
        "metrics": metrics,
        "dimensions": dimensions,
        "metricBindings": metric_bindings,
        "dimensionBindings": dimension_bindings,
    }


def _metric(
    name: str,
    field: str,
    *,
    additivity: str = "additive",
    blocked: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "expandedExpression": f"SUM({field})",
        "atomicFields": [field],
        "additivity": additivity,
        "blockedDimensions": blocked or [],
        "additivitySource": "configured",
    }


def _binding(metric: str, model: str) -> dict:
    return {"id": f"{metric}@{model}", "metric": metric, "model": model}


def _dimension_binding(dimension: str, model: str) -> dict:
    return {
        "id": f"{dimension}@{model}",
        "dimension": dimension,
        "model": model,
    }


def _empty_index(max_hops: int = 0) -> dict:
    return {"schemaVersion": 1, "maxHops": max_hops, "bindings": []}


def _parse_hive(plan: dict) -> None:
    assert sqlglot.parse_one(plan["sql"], dialect="hive") is not None


def test_unique_arbitrary_depth_path_builds_virtual_wide_table() -> None:
    nodes = [
        _node("fact", ["id", "l1_id", "amount"], "id"),
        _node("level_1", ["id", "l2_id"], "id"),
        _node("level_2", ["id", "leaf_id"], "id"),
        _node("leaf", ["id", "label"], "id"),
    ]
    edges = [
        _edge("fact_l1", "fact", "level_1", "MANY_TO_ONE", "fact.l1_id = level_1.id"),
        _edge(
            "l1_l2",
            "level_1",
            "level_2",
            "MANY_TO_ONE",
            "level_1.l2_id = level_2.id",
        ),
        _edge(
            "l2_leaf",
            "level_2",
            "leaf",
            "MANY_TO_ONE",
            "level_2.leaf_id = leaf.id",
        ),
    ]
    graph = _graph(
        nodes=nodes,
        edges=edges,
        metrics=[_metric("revenue", "amount")],
        dimensions=[
            {
                "name": "leaf_label",
                "expression": "label",
                "type": "STRING",
                "masterModel": "leaf",
            }
        ],
        metric_bindings=[_binding("revenue", "fact")],
        dimension_bindings=[_dimension_binding("leaf_label", "leaf")],
    )

    plan = plan_graph_query(
        graph,
        _empty_index(max_hops=2),
        {
            "facts": [{"sourceModel": "fact", "metrics": ["revenue"]}],
            "dimensions": ["leaf_label"],
            "maxDepth": 3,
        },
    )

    dimension = plan["relationalPlan"]["facts"][0]["dimensions"][0]
    assert dimension["hops"] == 3
    assert [step["relationship"] for step in dimension["path"]] == [
        "fact_l1",
        "l1_l2",
        "l2_leaf",
    ]
    assert plan["relationalPlan"]["virtualWideTable"]["maxDepth"] == 3
    assert plan["relationalPlan"]["virtualWideTable"]["cyclePolicy"] == (
        "SIMPLE_PATHS_ONLY"
    )
    assert plan["graphExplain"]["pathDecisions"][0]["decision"] == ("masterDataBinding")
    _parse_hive(plan)


def test_structured_planner_enforces_and_explains_master_bindings() -> None:
    metric = _metric("revenue", "amount")
    metric["masterModel"] = "fact_primary"
    graph = _graph(
        nodes=[
            _node("fact_primary", ["id", "amount", "segment"], "id"),
            _node("fact_copy", ["id", "amount", "segment"], "id"),
        ],
        edges=[],
        metrics=[metric],
        dimensions=[
            {
                "name": "segment",
                "expression": "segment",
                "type": "STRING",
                "masterModel": "fact_primary",
            }
        ],
        metric_bindings=[
            _binding("revenue", "fact_primary") | {"isMaster": True},
            _binding("revenue", "fact_copy") | {"isMaster": False},
        ],
        dimension_bindings=[
            _dimension_binding("segment", "fact_primary") | {"isMaster": True},
            _dimension_binding("segment", "fact_copy") | {"isMaster": False},
        ],
    )
    index = build_queryability_index(graph, max_hops=0)

    plan = plan_graph_query(
        graph,
        index,
        {
            "facts": [{"sourceModel": "fact_primary", "metrics": ["revenue"]}],
            "dimensions": ["segment"],
        },
    )

    explained_metric = plan["graphExplain"]["facts"][0]["metrics"][0]
    explained_dimension = plan["graphExplain"]["facts"][0]["dimensions"][0]
    assert explained_metric["masterModel"] == "fact_primary"
    assert explained_metric["isMaster"] is True
    assert explained_dimension["isMaster"] is True
    assert explained_dimension["routeDecision"] == "masterDataBinding"

    decisions = {
        (item["memberKind"], item["member"]): item
        for item in plan["graphExplain"]["pathDecisions"]
    }
    assert decisions[("metric", "revenue")]["decision"] == "masterDataBinding"
    assert decisions[("metric", "revenue")]["bindingModel"] == "fact_primary"
    assert decisions[("dimension", "segment")]["decision"] == ("masterDataBinding")
    metric_schema = next(
        item
        for item in plan["relationalPlan"]["virtualWideTable"]["schema"]
        if item.get("kind") == "metric"
    )
    assert metric_schema["masterModel"] == "fact_primary"
    assert metric_schema["isMaster"] is True

    with pytest.raises(GraphPlanningError) as metric_override:
        plan_graph_query(
            graph,
            index,
            {
                "facts": [{"sourceModel": "fact_copy", "metrics": ["revenue"]}],
                "dimensions": [],
            },
        )
    assert metric_override.value.code == "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
    assert metric_override.value.details["memberKind"] == "metric"
    assert metric_override.value.details["requestedModel"] == "fact_copy"

    with pytest.raises(GraphPlanningError) as dimension_override:
        plan_graph_query(
            graph,
            index,
            {
                "facts": [{"sourceModel": "fact_primary", "metrics": ["revenue"]}],
                "dimensions": [{"name": "segment", "bindingModel": "fact_copy"}],
            },
        )
    assert dimension_override.value.code == "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
    assert dimension_override.value.details["memberKind"] == "dimension"
    assert dimension_override.value.details["requestedModel"] == "fact_copy"


def test_path_ambiguity_requires_path_hint() -> None:
    nodes = [
        _node("fact", ["id", "a_id", "b_id", "amount"], "id"),
        _node("a", ["id", "leaf_id"], "id"),
        _node("b", ["id", "leaf_id"], "id"),
        _node("leaf", ["id", "label"], "id"),
    ]
    edges = [
        _edge("fact_a", "fact", "a", "MANY_TO_ONE", "fact.a_id = a.id"),
        _edge("fact_b", "fact", "b", "MANY_TO_ONE", "fact.b_id = b.id"),
        _edge("a_leaf", "a", "leaf", "MANY_TO_ONE", "a.leaf_id = leaf.id"),
        _edge("b_leaf", "b", "leaf", "MANY_TO_ONE", "b.leaf_id = leaf.id"),
    ]
    graph = _graph(
        nodes=nodes,
        edges=edges,
        metrics=[_metric("revenue", "amount")],
        dimensions=[{"name": "label", "expression": "label", "masterModel": "leaf"}],
        metric_bindings=[_binding("revenue", "fact")],
        dimension_bindings=[_dimension_binding("label", "leaf")],
    )
    request = {
        "facts": [{"sourceModel": "fact", "metrics": ["revenue"]}],
        "dimensions": ["label"],
    }

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), request)
    assert caught.value.code == "GRAPH_PATH_AMBIGUOUS"
    assert len(caught.value.details["candidatePaths"]) == 2

    request["pathHints"] = {"dimensions": {"label": ["fact_a", "a_leaf"]}}
    plan = plan_graph_query(graph, _empty_index(), request)
    path = plan["relationalPlan"]["facts"][0]["dimensions"][0]["path"]
    assert [step["relationship"] for step in path] == ["fact_a", "a_leaf"]
    assert plan["graphExplain"]["pathDecisions"][0]["decision"] == "pathHint"

    explicit = deepcopy(request)
    explicit.pop("pathHints")
    explicit["dimensions"] = [
        {"name": "label", "relationshipPath": ["fact_a", "a_leaf"]}
    ]
    explicit_plan = plan_graph_query(graph, _empty_index(), explicit)
    assert explicit_plan["graphExplain"]["pathDecisions"][0]["decision"] == (
        "explicitRelationshipPath"
    )


def test_graph_query_request_rejects_unknown_fields_and_versions() -> None:
    graph = _graph(
        nodes=[_node("fact", ["id", "amount"], "id")],
        edges=[],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "fact")],
        dimension_bindings=[],
    )
    request = {
        "schemaVersion": 1,
        "facts": [{"sourceModel": "fact", "metrics": ["revenue"]}],
    }

    with pytest.raises(GraphPlanningError) as unknown:
        plan_graph_query(
            graph,
            _empty_index(),
            request | {"silentTypo": True},
        )
    assert unknown.value.code == "GRAPH_REQUEST_FIELD_UNKNOWN"
    assert unknown.value.details == {"unknownFields": ["silentTypo"]}

    with pytest.raises(GraphPlanningError) as version:
        plan_graph_query(
            graph,
            _empty_index(),
            request | {"schemaVersion": 2},
        )
    assert version.value.code == "GRAPH_REQUEST_SCHEMA_VERSION_UNSUPPORTED"

    with pytest.raises(GraphPlanningError) as fanout_mode:
        plan_graph_query(
            graph,
            _empty_index(),
            request | {"fanoutMode": "guess"},
        )
    assert fanout_mode.value.code == "GRAPH_FANOUT_MODE_INVALID"


def test_one_to_many_fanout_preaggregates_and_deduplicates_mapping() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["order_id", "amount"], "order_id"),
            _node("lines", ["line_id", "order_id", "sku"], "line_id"),
        ],
        edges=[
            _edge(
                "orders_lines",
                "orders",
                "lines",
                "ONE_TO_MANY",
                "orders.order_id = lines.order_id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[{"name": "sku", "expression": "sku", "masterModel": "lines"}],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[_dimension_binding("sku", "lines")],
    )

    request = {
        "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        "dimensions": ["sku"],
    }
    with pytest.raises(GraphPlanningError) as rejected:
        plan_graph_query(graph, _empty_index(), request)
    assert rejected.value.code == "GRAPH_FANOUT_ALLOCATION_REQUIRED"

    plan = plan_graph_query(
        graph,
        _empty_index(),
        request | {"fanoutMode": "repeat"},
    )

    assert plan["strategy"] == "SINGLE_FACT_FANOUT_REPEAT"
    assert plan["relationalPlan"]["facts"][0]["fanoutSemantics"] == (
        "REPEAT_NON_ADDITIVE"
    )
    assert (
        plan["graphExplain"]["facts"][0]["safety"]["additiveAcrossSelectedDimension"]
        is False
    )
    stages = plan["relationalPlan"]["facts"][0]["stages"]
    assert [stage["kind"] for stage in stages] == [
        "FACT_PREAGGREGATE",
        "DEDUP_MAPPING_INPUT",
        "DEDUPLICATED_MAPPING",
        "GRAIN_ROLLUP",
    ]
    assert "`fact_0_preaggregate`" in plan["sql"]
    assert "`fact_0_mapping`" in plan["sql"]
    assert "SUM(p.revenue)" in plan["sql"]
    _parse_hive(plan)


def test_multi_fact_aggregates_then_full_outer_joins_common_grain() -> None:
    nodes = [
        _node("orders", ["id", "customer_id", "amount"], "id"),
        _node("events", ["id", "customer_id", "click_count"], "id"),
        _node("customers", ["customer_id", "country"], "customer_id"),
    ]
    graph = _graph(
        nodes=nodes,
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.customer_id",
            ),
            _edge(
                "events_customer",
                "events",
                "customers",
                "MANY_TO_ONE",
                "events.customer_id = customers.customer_id",
            ),
        ],
        metrics=[_metric("revenue", "amount"), _metric("clicks", "click_count")],
        dimensions=[
            {
                "name": "country",
                "expression": "country",
                "masterModel": "customers",
            }
        ],
        metric_bindings=[
            _binding("revenue", "orders"),
            _binding("clicks", "events"),
        ],
        dimension_bindings=[_dimension_binding("country", "customers")],
    )

    plan = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [
                {"sourceModel": "orders", "metrics": ["revenue"]},
                {"sourceModel": "events", "metrics": ["clicks"]},
            ],
            "dimensions": ["country"],
        },
    )

    assert plan["strategy"] == "MULTI_FACT_AGGREGATE_MERGE"
    merge = plan["relationalPlan"]["merge"]
    assert merge["strategy"] == "AGGREGATE_THEN_FULL_OUTER_JOIN"
    assert merge["grainColumns"] == ["country"]
    assert "FULL OUTER JOIN" in plan["sql"]
    assert "l.country <=> r.country" in plan["sql"]
    _parse_hive(plan)


def test_many_to_many_requires_policy_and_consumes_weighted_bridge() -> None:
    nodes = [
        _node("authors", ["id", "amount"], "id"),
        _node(
            "author_tags",
            ["id", "author_id", "tag_id", "allocation_weight"],
            "id",
        ),
        _node("tags", ["id", "tag"], "id"),
    ]
    source_edge = _edge(
        "authors_bridge",
        "authors",
        "author_tags",
        "ONE_TO_MANY",
        "authors.id = author_tags.author_id",
    )
    target_edge = _edge(
        "bridge_tag",
        "author_tags",
        "tags",
        "MANY_TO_ONE",
        "author_tags.tag_id = tags.id",
    )
    many_to_many = _edge(
        "authors_tags",
        "authors",
        "tags",
        "MANY_TO_MANY",
        "authors.id = tags.id",
    )
    graph = _graph(
        nodes=nodes,
        edges=[source_edge, target_edge, many_to_many],
        metrics=[_metric("amount", "amount")],
        dimensions=[{"name": "tag", "expression": "tag", "masterModel": "tags"}],
        metric_bindings=[_binding("amount", "authors")],
        dimension_bindings=[_dimension_binding("tag", "tags")],
    )
    request = {
        "facts": [{"sourceModel": "authors", "metrics": ["amount"]}],
        "dimensions": [{"name": "tag", "relationshipPath": ["authors_tags"]}],
    }

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), request)
    assert caught.value.code == "GRAPH_MANY_TO_MANY_POLICY_REQUIRED"

    protected = deepcopy(graph)
    protected["edges"][2]["bridgePolicy"] = {
        "model": "author_tags",
        "sourceRelationship": "authors_bridge",
        "targetRelationship": "bridge_tag",
        "allocationExpression": "author_tags.allocation_weight",
        "allocationMode": "weighted",
    }
    protected["edges"][2]["cardinalityValidation"] = "bridge_verified"
    plan = plan_graph_query(protected, _empty_index(), request)

    assert "j0.allocation_weight AS `__allocation_weight`" in plan["sql"]
    assert "p.amount * COALESCE(m.__allocation_weight, 0)" in plan["sql"]
    assert plan["graphExplain"]["facts"][0]["safety"]["bridgeAllocation"] is True
    _parse_hive(plan)


@pytest.mark.parametrize(
    ("metric", "expected_code"),
    [
        (
            _metric("blocked", "amount", blocked=["sku"]),
            "GRAPH_METRIC_DIMENSION_BLOCKED",
        ),
        (
            _metric("ratio", "amount", additivity="non_additive"),
            "GRAPH_METRIC_NOT_ADDITIVE_FOR_FANOUT",
        ),
    ],
)
def test_metric_additivity_policy_rejects_illegal_fanout(
    metric: dict, expected_code: str
) -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "amount"], "id"),
            _node("lines", ["id", "order_id", "sku"], "id"),
        ],
        edges=[
            _edge(
                "orders_lines",
                "orders",
                "lines",
                "ONE_TO_MANY",
                "orders.id = lines.order_id",
            )
        ],
        metrics=[metric],
        dimensions=[{"name": "sku", "expression": "sku", "masterModel": "lines"}],
        metric_bindings=[_binding(metric["name"], "orders")],
        dimension_bindings=[_dimension_binding("sku", "lines")],
    )

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(
            graph,
            _empty_index(),
            {
                "fanoutMode": "repeat",
                "facts": [{"sourceModel": "orders", "metrics": [metric["name"]]}],
                "dimensions": ["sku"],
            },
        )
    assert caught.value.code == expected_code


def test_alias_conflict_is_structured() -> None:
    graph = _graph(
        nodes=[_node("fact", ["id", "amount"], "id")],
        edges=[],
        metrics=[_metric("revenue", "amount"), _metric("cost", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "fact"), _binding("cost", "fact")],
        dimension_bindings=[],
    )

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(
            graph,
            _empty_index(),
            {
                "facts": [
                    {
                        "sourceModel": "fact",
                        "metrics": [
                            {"name": "revenue", "alias": "value"},
                            {"name": "cost", "alias": "value"},
                        ],
                    }
                ]
            },
        )
    assert caught.value.code == "GRAPH_OUTPUT_ALIAS_CONFLICT"
    assert len(caught.value.details["conflicts"][0]) == 2


def test_reachable_discovery_lists_attributes_without_projecting_them() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "customer_id", "amount"], "id"),
            _node("customers", ["id", "country"], "id"),
        ],
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[
            {
                "name": "country",
                "expression": "country",
                "masterModel": "customers",
            },
            {
                "name": "missing_dimension",
                "expression": "missing",
                "masterModel": "missing_node",
            },
        ],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[_dimension_binding("country", "customers")],
    )

    plan = plan_graph_query(
        graph,
        _empty_index(),
        {"anchorModel": "orders", "includeReachable": True},
    )

    virtual = plan["relationalPlan"]["virtualWideTable"]
    raw_attributes = [item for item in virtual["schema"] if item["kind"] == "attribute"]
    assert {item["name"] for item in raw_attributes} >= {
        "orders.amount",
        "customers.country",
    }
    assert all(item["projected"] is False for item in raw_attributes)
    assert plan["strategy"] == "DISCOVERY_ONLY"
    assert plan["sql"] is None
    assert plan["relationalPlan"]["facts"] == []
    semantic_members = {
        (item["kind"], item["name"])
        for item in virtual["schema"]
        if item["kind"] in {"metric", "dimension"}
    }
    assert semantic_members >= {("metric", "revenue"), ("dimension", "country")}
    rejected = virtual["memberDiscovery"]["rejectedMembers"]
    assert any(item["name"] == "missing_dimension" for item in rejected)

    selected = plan_graph_query(
        graph,
        _empty_index(),
        {
            "anchorModel": "orders",
            "includeReachable": True,
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        },
    )
    assert "JOIN" not in selected["sql"]
    assert selected["relationalPlan"]["outputMetrics"] == ["revenue"]
    country = next(
        item
        for item in selected["relationalPlan"]["virtualWideTable"]["schema"]
        if item["name"] == "customers.country"
    )
    assert country["projected"] is False


def test_explicit_attribute_and_safe_calculations_are_projected() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "customer_id", "amount"], "id"),
            _node("customers", ["id", "country"], "id"),
        ],
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )

    plan = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
            "attributes": [
                {"model": "customers", "field": "country", "alias": "country"}
            ],
            "calculations": [
                {
                    "name": "amount_band",
                    "kind": "dimension",
                    "expression": "CASE WHEN orders.amount >= 100 THEN 'high' ELSE 'low' END",
                },
                {
                    "name": "double_revenue",
                    "kind": "metric",
                    "expression": "SUM(orders.amount) * 2",
                },
            ],
        },
    )

    assert "j0.country AS `country`" in plan["sql"]
    assert "CASE WHEN s.amount >= 100" in plan["sql"]
    assert "SUM(s.amount) * 2 AS `double_revenue`" in plan["sql"]
    assert "GROUP BY" in plan["sql"]
    _parse_hive(plan)

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(
            graph,
            _empty_index(),
            {
                "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
                "calculations": [
                    {
                        "name": "unsafe_metric",
                        "kind": "metric",
                        "expression": "orders.amount + 1",
                    }
                ],
            },
        )
    assert caught.value.code == "GRAPH_METRIC_CALCULATION_UNSAFE"


def test_count_distinct_calculation_is_not_inferred_additive() -> None:
    graph = _graph(
        nodes=[_node("orders", ["id", "customer_id", "amount"], "id")],
        edges=[],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )

    plan = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
            "calculations": [
                {
                    "name": "distinct_customers",
                    "kind": "metric",
                    "expression": "COUNT(DISTINCT orders.customer_id)",
                }
            ],
        },
    )

    metric = next(
        item
        for item in plan["relationalPlan"]["facts"][0]["metrics"]
        if item["name"] == "distinct_customers"
    )
    assert metric["additivity"] == "non_additive"


def test_dimension_calculation_uses_declared_inputs_across_reachable_nodes() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "customer_id", "amount"], "id"),
            _node("customers", ["id", "region_id", "tier"], "id"),
            _node("regions", ["id", "label"], "id"),
        ],
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.id",
            ),
            _edge(
                "customer_region",
                "customers",
                "regions",
                "MANY_TO_ONE",
                "customers.region_id = regions.id",
            ),
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )

    plan = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
            "calculations": [
                {
                    "name": "regional_order_band",
                    "kind": "dimension",
                    "expression": (
                        "CASE WHEN orders.amount >= 100 AND customers.tier = 'gold' "
                        "THEN regions.label ELSE customers.tier END"
                    ),
                    "inputs": [
                        {"model": "orders", "field": "amount"},
                        {
                            "model": "customers",
                            "field": "tier",
                            "relationshipPath": ["orders_customer"],
                        },
                        {
                            "model": "regions",
                            "field": "label",
                            "relationshipPath": [
                                "orders_customer",
                                "customer_region",
                            ],
                        },
                    ],
                }
            ],
        },
    )

    calculation = plan["relationalPlan"]["facts"][0]["dimensions"][0]
    assert calculation["bindingModel"] is None
    assert calculation["bindingModels"] == ["orders", "customers", "regions"]
    assert {
        route["model"]: [step["relationship"] for step in route["path"]]
        for route in calculation["routes"]
    } == {
        "orders": [],
        "customers": ["orders_customer"],
        "regions": ["orders_customer", "customer_region"],
    }
    assert calculation["modelAliases"] == {
        "orders": "s",
        "customers": "j0",
        "regions": "j1",
    }
    assert plan["sql"].count("LEFT JOIN") == 2
    assert (
        "CASE WHEN s.amount >= 100 AND j0.tier = 'gold' THEN j1.label ELSE j0.tier END"
        in plan["sql"]
    )
    _parse_hive(plan)


def test_calculation_inputs_require_exact_declared_qualified_fields() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "customer_id", "amount"], "id"),
            _node("customers", ["id", "country", "tier"], "id"),
        ],
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )
    base = {
        "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        "calculations": [
            {
                "name": "customer_band",
                "kind": "dimension",
                "expression": "CONCAT(orders.amount, customers.tier)",
                "inputs": [{"model": "orders", "field": "amount"}],
            }
        ],
    }

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), base)
    assert caught.value.code == "GRAPH_CALCULATION_INPUT_UNDECLARED"
    assert caught.value.details["fields"] == ["customers.tier"]

    unqualified = deepcopy(base)
    unqualified["calculations"][0]["expression"] = "amount"
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), unqualified)
    assert caught.value.code == "GRAPH_CALCULATION_INPUT_QUALIFICATION_REQUIRED"

    unused = deepcopy(base)
    unused["calculations"][0]["expression"] = "orders.amount"
    unused["calculations"][0]["inputs"].append({"model": "customers", "field": "tier"})
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), unused)
    assert caught.value.code == "GRAPH_CALCULATION_INPUT_UNUSED"
    assert caught.value.details["fields"] == ["customers.tier"]


def test_each_calculation_input_resolves_its_own_ambiguous_route() -> None:
    graph = _graph(
        nodes=[
            _node(
                "orders",
                ["id", "billing_customer_id", "shipping_customer_id", "amount"],
                "id",
            ),
            _node("customers", ["id", "segment"], "id"),
        ],
        edges=[
            _edge(
                "orders_billing_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.billing_customer_id = customers.id",
            ),
            _edge(
                "orders_shipping_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.shipping_customer_id = customers.id",
            ),
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )
    request = {
        "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        "calculations": [
            {
                "name": "shipping_segment",
                "kind": "dimension",
                "expression": "customers.segment",
                "inputs": [{"model": "customers", "field": "segment"}],
            }
        ],
    }

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), request)
    assert caught.value.code == "GRAPH_PATH_AMBIGUOUS"

    request["calculations"][0]["inputs"][0]["relationshipPath"] = [
        "orders_shipping_customer"
    ]
    plan = plan_graph_query(graph, _empty_index(), request)
    calculation = plan["relationalPlan"]["facts"][0]["dimensions"][0]
    assert calculation["modelAliases"] == {"orders": "s", "customers": "j0"}
    assert "s.shipping_customer_id = j0.id" in plan["sql"]
    assert "j0.segment AS `shipping_segment`" in plan["sql"]
    _parse_hive(plan)


def test_cross_node_calculation_renders_through_fanout_and_rejects_two_branches() -> (
    None
):
    graph = _graph(
        nodes=[
            _node("orders", ["id", "status", "amount"], "id"),
            _node("lines", ["id", "order_id", "sku"], "id"),
            _node("payments", ["id", "order_id", "method"], "id"),
        ],
        edges=[
            _edge(
                "orders_lines",
                "orders",
                "lines",
                "ONE_TO_MANY",
                "orders.id = lines.order_id",
            ),
            _edge(
                "orders_payments",
                "orders",
                "payments",
                "ONE_TO_MANY",
                "orders.id = payments.order_id",
            ),
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )
    request = {
        "fanoutMode": "repeat",
        "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        "calculations": [
            {
                "name": "status_sku",
                "kind": "dimension",
                "expression": "CONCAT(orders.status, ':', lines.sku)",
                "inputs": [
                    {"model": "orders", "field": "status"},
                    {
                        "model": "lines",
                        "field": "sku",
                        "relationshipPath": ["orders_lines"],
                    },
                ],
            }
        ],
    }

    plan = plan_graph_query(graph, _empty_index(), request)
    assert plan["strategy"] == "SINGLE_FACT_FANOUT_REPEAT"
    assert "CONCAT(s.status, ':', j0.sku) AS `status_sku`" in plan["sql"]
    assert "SUM(p.revenue) AS `revenue`" in plan["sql"]
    _parse_hive(plan)

    unsafe = deepcopy(request)
    calculation = unsafe["calculations"][0]
    calculation["expression"] = (
        "CONCAT(orders.status, ':', lines.sku, ':', payments.method)"
    )
    calculation["inputs"].append(
        {
            "model": "payments",
            "field": "method",
            "relationshipPath": ["orders_payments"],
        }
    )
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), unsafe)
    assert caught.value.code == "GRAPH_MULTIPLE_FANOUT_BRANCHES_UNSUPPORTED"


def test_calculation_input_keeps_legacy_path_and_m2m_guards() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "customer_id", "amount"], "id"),
            _node("customers", ["id", "country"], "id"),
        ],
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )
    legacy = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
            "calculations": [
                {
                    "name": "customer_country",
                    "kind": "dimension",
                    "expression": "customers.country",
                    "relationshipPath": ["orders_customer"],
                }
            ],
        },
    )
    legacy_calculation = legacy["relationalPlan"]["facts"][0]["dimensions"][0]
    assert legacy_calculation["inputsDeclared"] is False
    assert [step["relationship"] for step in legacy_calculation["path"]] == [
        "orders_customer"
    ]
    _parse_hive(legacy)

    declared_with_legacy_path = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
            "calculations": [
                {
                    "name": "order_country",
                    "kind": "dimension",
                    "expression": "CONCAT(orders.amount, customers.country)",
                    "relationshipPath": ["orders_customer"],
                    "inputs": [
                        {"model": "orders", "field": "amount"},
                        {"model": "customers", "field": "country"},
                    ],
                }
            ],
        },
    )
    assert (
        "CONCAT(s.amount, j0.country) AS `order_country`"
        in (declared_with_legacy_path["sql"])
    )
    _parse_hive(declared_with_legacy_path)

    many_to_many = deepcopy(graph)
    many_to_many["edges"] = [
        _edge(
            "orders_customer",
            "orders",
            "customers",
            "MANY_TO_MANY",
            "orders.customer_id = customers.id",
        )
    ]
    request = {
        "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        "calculations": [
            {
                "name": "customer_country",
                "kind": "dimension",
                "expression": "customers.country",
                "inputs": [
                    {
                        "model": "customers",
                        "field": "country",
                        "relationshipPath": ["orders_customer"],
                    }
                ],
            }
        ],
    }
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(many_to_many, _empty_index(), request)
    assert caught.value.code == "GRAPH_MANY_TO_MANY_POLICY_REQUIRED"


def test_safe_leaf_field_can_participate_in_metric_calculation() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "customer_id", "amount"], "id"),
            _node("customers", ["id", "credit_limit"], "id"),
        ],
        edges=[
            _edge(
                "orders_customer",
                "orders",
                "customers",
                "MANY_TO_ONE",
                "orders.customer_id = customers.id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )
    request = {
        "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
        "calculations": [
            {
                "name": "max_customer_credit",
                "kind": "metric",
                "expression": "MAX(customers.credit_limit)",
                "inputs": [
                    {
                        "model": "customers",
                        "field": "credit_limit",
                        "relationshipPath": ["orders_customer"],
                    }
                ],
            }
        ],
    }

    plan = plan_graph_query(graph, _empty_index(), request)
    metric = next(
        item
        for item in plan["relationalPlan"]["facts"][0]["metrics"]
        if item["name"] == "max_customer_credit"
    )
    assert metric["bindingModels"] == ["customers"]
    assert "LEFT JOIN `customers` AS j0" in plan["sql"]
    assert "MAX(j0.credit_limit) AS `max_customer_credit`" in plan["sql"]
    _parse_hive(plan)

    fanout_graph = deepcopy(graph)
    fanout_graph["edges"] = [
        _edge(
            "orders_customer",
            "orders",
            "customers",
            "ONE_TO_MANY",
            "orders.id = customers.customer_id",
        )
    ]
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(fanout_graph, _empty_index(), request)
    assert caught.value.code == "GRAPH_METRIC_CALCULATION_UNSAFE"
    assert caught.value.details["failures"][0]["code"] == (
        "GRAPH_REMOTE_METRIC_FANOUT_UNSAFE"
    )


def test_multi_fact_post_aggregate_calculation_runs_after_grain_merge() -> None:
    graph = _graph(
        nodes=[
            _node("fact_a", ["id", "amount"], "id"),
            _node("fact_b", ["id", "amount"], "id"),
        ],
        edges=[],
        metrics=[
            _metric("amount_a", "amount"),
            _metric("amount_b", "amount"),
        ],
        dimensions=[],
        metric_bindings=[
            _binding("amount_a", "fact_a"),
            _binding("amount_b", "fact_b"),
        ],
        dimension_bindings=[],
    )
    plan = plan_graph_query(
        graph,
        _empty_index(),
        {
            "facts": [
                {
                    "sourceModel": "fact_a",
                    "metrics": [{"name": "amount_a", "alias": "a_total"}],
                },
                {
                    "sourceModel": "fact_b",
                    "metrics": [{"name": "amount_b", "alias": "b_total"}],
                },
            ],
            "calculations": [
                {
                    "name": "a_to_b_ratio",
                    "kind": "post_metric",
                    "expression": "a_total / NULLIF(b_total, 0)",
                }
            ],
        },
    )

    assert plan["relationalPlan"]["outputMetrics"] == [
        "a_total",
        "b_total",
        "a_to_b_ratio",
    ]
    assert plan["graphExplain"]["postAggregate"][0]["referencedColumns"] == [
        "a_total",
        "b_total",
    ]
    assert "`post_aggregate` AS (" in plan["sql"]
    assert "q.a_total / NULLIF(q.b_total, 0) AS `a_to_b_ratio`" in plan["sql"]
    _parse_hive(plan)

    invalid = deepcopy(plan["request"])
    invalid["postCalculations"][0]["expression"] = "SUM(a_total)"
    invalid["calculations"] = invalid.pop("postCalculations")
    invalid["calculations"][0]["kind"] = "post_metric"
    invalid.pop("dimensionCalculations", None)
    invalid.pop("metricCalculations", None)
    invalid.pop("topMetrics", None)
    invalid.pop("metricsWildcard", None)
    invalid.pop("dimensionsWildcard", None)
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), invalid)
    assert caught.value.code == "GRAPH_POST_CALCULATION_REAGGREGATION_FORBIDDEN"

    injected = deepcopy(invalid)
    injected["calculations"][0]["expression"] = "a_total; DROP TABLE fact_a"
    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(graph, _empty_index(), injected)
    assert caught.value.code == "GRAPH_CALCULATION_QUERY_FORBIDDEN"


def test_discovery_lists_declared_m2m_leaf_but_query_still_rejects_it() -> None:
    graph = _graph(
        nodes=[
            _node("orders", ["id", "amount"], "id"),
            _node("tags", ["id", "label"], "id"),
        ],
        edges=[
            _edge(
                "orders_tags",
                "orders",
                "tags",
                "MANY_TO_MANY",
                "orders.id = tags.id",
            )
        ],
        metrics=[_metric("revenue", "amount")],
        dimensions=[],
        metric_bindings=[_binding("revenue", "orders")],
        dimension_bindings=[],
    )
    discovered = plan_graph_query(
        graph,
        _empty_index(),
        {"anchorModel": "orders", "includeReachable": True},
    )
    label = next(
        item
        for item in discovered["relationalPlan"]["virtualWideTable"]["schema"]
        if item["name"] == "tags.label"
    )
    assert label["reachability"] == "declared_only"
    assert label["projected"] is False

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(
            graph,
            _empty_index(),
            {
                "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
                "attributes": [
                    {
                        "model": "tags",
                        "field": "label",
                        "relationshipPath": ["orders_tags"],
                    }
                ],
            },
        )
    assert caught.value.code == "GRAPH_MANY_TO_MANY_POLICY_REQUIRED"
