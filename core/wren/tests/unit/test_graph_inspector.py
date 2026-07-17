"""Post-implementation coverage for the read-only graph inspector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wren.semantic_graph.inspector import GraphInspectionError, inspect_graph


@pytest.fixture
def semantic_graph() -> dict:
    return {
        "edgeSource": "relationships.yml",
        "metricBindings": [],
        "nodes": [
            {
                "name": "customers",
                "kind": "model",
                "description": "客户主数据",
                "properties": {"domain": "crm"},
            },
            {"name": "orders", "kind": "model", "description": "订单事实"},
            {"name": "returns", "kind": "view", "description": "退货事实"},
        ],
        "edges": [
            {
                "name": "orders_customer",
                "declaredModels": ["orders", "customers"],
                "cardinality": "MANY_TO_ONE",
                "cardinalityValidation": "verified",
            },
            {
                "name": "returns_customer",
                "declaredModels": ["returns", "customers"],
                "cardinality": "MANY_TO_ONE",
                "cardinalityValidation": "verified",
            },
        ],
    }


@pytest.fixture
def ontology_graph() -> dict:
    return {
        "kind": "ontology_graph",
        "nodes": [
            {
                "id": "metric:clicks",
                "type": "METRIC",
                "name": "clicks_sum",
                "label": "点击量",
                "synonyms": ["点击次数", "Clicks"],
                "properties": {"public": True},
            },
            {
                "id": "dataset:ads",
                "type": "DATASET",
                "name": "fact_ads",
                "label": "广告事实",
                "synonyms": [],
                "properties": {},
            },
        ],
        "edges": [
            {
                "id": "edge:ads:clicks",
                "type": "HAS_METRIC",
                "sourceId": "dataset:ads",
                "targetId": "metric:clicks",
                "properties": {"ordinal": 1},
            }
        ],
    }


def test_node_filter_parameters_order_and_limit_are_deterministic(
    semantic_graph: dict,
) -> None:
    result = inspect_graph(
        semantic_graph,
        "MATCH (n) WHERE n.description CONTAINS $text "
        "RETURN n.name AS name ORDER BY name DESC LIMIT $limit",
        {"text": "事实", "limit": 1},
    )

    assert result["rows"] == [{"name": "returns"}]
    assert result["explain"] == {
        "readOnly": True,
        "graphKind": "semantic_graph",
        "pattern": {
            "kind": "NODE",
            "direction": "node",
            "left": {"variable": "n", "label": None, "properties": {}},
        },
        "filters": [
            {"property": "n.description", "operator": "CONTAINS", "value": "事实"}
        ],
        "returns": [{"expression": "n.name", "alias": "name"}],
        "orderBy": [{"expression": "name", "direction": "DESC"}],
        "limit": 1,
        "scanned": {"nodes": 3, "edges": 2, "candidates": 3},
        "matched": 2,
        "returned": 1,
        "truncated": True,
    }


def test_semantic_single_hop_supports_direction_and_virtual_endpoints(
    semantic_graph: dict,
) -> None:
    outgoing = inspect_graph(
        semantic_graph,
        "MATCH (fact)-[r:MANY_TO_ONE]->(master:model) "
        "WHERE r.cardinalityValidation = 'verified' "
        "RETURN fact.name AS fact, r.sourceId AS source, master.name AS master "
        "ORDER BY fact",
    )
    incoming = inspect_graph(
        semantic_graph,
        "MATCH (master:model)<--(fact) "
        "RETURN master.name AS master, fact.name AS fact ORDER BY fact",
    )

    assert outgoing["rows"] == [
        {"fact": "orders", "source": "orders", "master": "customers"},
        {"fact": "returns", "source": "returns", "master": "customers"},
    ]
    assert incoming["rows"] == [
        {"master": "customers", "fact": "orders"},
        {"master": "customers", "fact": "returns"},
    ]
    assert outgoing["explain"]["pattern"]["direction"] == "outgoing"
    assert incoming["explain"]["pattern"]["direction"] == "incoming"


def test_ontology_types_properties_synonyms_and_typed_edges(
    ontology_graph: dict,
) -> None:
    metric = inspect_graph(
        ontology_graph,
        "MATCH (n:METRIC {public: true}) "
        "WHERE n.synonyms CONTAINS $synonym "
        "RETURN n.id AS id, n.label AS label",
        {"synonym": "点击次数"},
    )
    edge = inspect_graph(
        ontology_graph,
        "MATCH (d:DATASET)-[r:HAS_METRIC]->(m:METRIC) "
        "RETURN d.name AS dataset, r.type AS edge_type, m.name AS metric",
    )

    assert metric["rows"] == [{"id": "metric:clicks", "label": "点击量"}]
    assert edge["rows"] == [
        {
            "dataset": "fact_ads",
            "edge_type": "HAS_METRIC",
            "metric": "clicks_sum",
        }
    ]
    assert metric["explain"]["graphKind"] == "ontology_graph"


@pytest.mark.parametrize(
    ("query", "word"),
    [
        ("CREATE (n)", "CREATE"),
        ("MATCH (n) MERGE (m) RETURN n", "MERGE"),
        ("MATCH (n) DELETE n RETURN n", "DELETE"),
        ("MATCH (n) SET n.name = 'x' RETURN n", "SET"),
        ("CALL db.labels()", "CALL"),
        ("LOAD CSV FROM 'x' AS row RETURN row", "LOAD"),
    ],
)
def test_mutating_and_executable_clauses_are_rejected_before_graph_access(
    query: str, word: str
) -> None:
    with pytest.raises(GraphInspectionError) as caught:
        inspect_graph(Path("/path/that/must/not/be/opened.json"), query)

    assert caught.value.code == "READ_ONLY_VIOLATION"
    assert word in str(caught.value)


def test_comments_multiple_statements_and_multi_hop_queries_are_rejected(
    semantic_graph: dict,
) -> None:
    cases = {
        "MATCH (n) // hidden\n RETURN n": "QUERY_COMMENTS_FORBIDDEN",
        "MATCH (n) RETURN n; MATCH (m) RETURN m": "MULTIPLE_STATEMENTS_FORBIDDEN",
        "MATCH (a)-[r]->(b)-[s]->(c) RETURN c": "QUERY_SYNTAX_ERROR",
        "MATCH (n) RETURN *, n.name": "QUERY_SYNTAX_ERROR",
    }
    for query, code in cases.items():
        with pytest.raises(GraphInspectionError) as caught:
            inspect_graph(semantic_graph, query)
        assert caught.value.code == code


def test_missing_unused_and_invalid_limit_parameters_are_structured(
    semantic_graph: dict,
) -> None:
    with pytest.raises(GraphInspectionError) as missing:
        inspect_graph(
            semantic_graph,
            "MATCH (n) WHERE n.name = $name RETURN n.name",
        )
    with pytest.raises(GraphInspectionError) as unused:
        inspect_graph(semantic_graph, "MATCH (n) RETURN n.name", {"name": "orders"})
    with pytest.raises(GraphInspectionError) as invalid_limit:
        inspect_graph(
            semantic_graph,
            "MATCH (n) RETURN n.name LIMIT $limit",
            {"limit": "one"},
        )
    with pytest.raises(GraphInspectionError) as invalid_parameters:
        inspect_graph(semantic_graph, "MATCH (n) RETURN n.name", [])  # type: ignore[arg-type]

    assert missing.value.code == "PARAMETER_MISSING"
    assert unused.value.code == "UNUSED_PARAMETERS"
    assert invalid_limit.value.code == "INVALID_LIMIT"
    assert invalid_parameters.value.code == "INVALID_PARAMETERS"


def test_whole_records_and_default_row_order_are_stable(
    ontology_graph: dict,
) -> None:
    reversed_graph = {
        **ontology_graph,
        "nodes": list(reversed(ontology_graph["nodes"])),
        "edges": list(reversed(ontology_graph["edges"])),
    }
    query = "MATCH (n) RETURN n"

    first = inspect_graph(ontology_graph, query)
    second = inspect_graph(reversed_graph, query)

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert list(first["rows"][0]["n"]) == sorted(first["rows"][0]["n"])


def test_property_named_like_forbidden_clause_is_allowed_when_qualified(
    semantic_graph: dict,
) -> None:
    semantic_graph["nodes"][0]["set"] = "canonical"

    result = inspect_graph(
        semantic_graph,
        "MATCH (n:model) WHERE n.set = 'canonical' RETURN n.set AS value",
    )

    assert result["rows"] == [{"value": "canonical"}]


def test_boolean_property_does_not_equal_numeric_one(ontology_graph: dict) -> None:
    result = inspect_graph(
        ontology_graph,
        "MATCH (n:METRIC {public: 1}) RETURN n.name",
    )

    assert result["rows"] == []
