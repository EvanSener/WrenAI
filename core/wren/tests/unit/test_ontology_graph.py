"""Post-implementation coverage for ontology and Apache Ossie interchange."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from wren.semantic_graph.ontology import (
    EDGE_TYPES,
    NODE_TYPES,
    ONTOLOGY_KIND,
    OntologyInterchangeError,
    compile_ontology_graph,
    export_ontology_to_osi,
    export_ontology_to_osi_file,
    import_osi_ontology,
    load_ontology_graph,
    save_ontology_graph,
)


def _semantic_graph() -> dict:
    return {
        "schemaVersion": 1,
        "project": {
            "name": "shop",
            "version": "1.0",
            "dataSource": "maxcompute",
        },
        "nodes": [
            {
                "name": "orders",
                "kind": "model",
                "label": "订单",
                "description": "订单事实表",
                "primaryKey": ["order_id"],
                "grain": {"fields": ["order_id"], "source": "primary_key"},
                "attributes": [
                    {"name": "order_id", "type": "BIGINT", "description": "订单主键"},
                    {"name": "customer_id", "type": "BIGINT"},
                    {"name": "amount", "type": "DECIMAL(18,2)"},
                    {"name": "ds", "type": "DATE"},
                ],
                "entities": [
                    {"name": "order", "type": "primary", "fields": ["order_id"]}
                ],
                "relation": {
                    "type": "table",
                    "tableReference": {
                        "catalog": "shop",
                        "schema": "public",
                        "table": "orders",
                    },
                },
            },
            {
                "name": "customers",
                "kind": "model",
                "description": "客户主数据",
                "primaryKey": ["customer_id"],
                "grain": {
                    "fields": ["customer_id"],
                    "source": "primary_key",
                },
                "attributes": [
                    {"name": "customer_id", "type": "BIGINT"},
                    {"name": "country", "type": "STRING"},
                ],
                "entities": [
                    {
                        "name": "customer",
                        "type": "primary",
                        "fields": ["customer_id"],
                    }
                ],
                "relation": {
                    "type": "table",
                    "tableReference": {
                        "catalog": "shop",
                        "schema": "public",
                        "table": "customers",
                    },
                },
            },
        ],
        "edges": [
            {
                "name": "orders_customer",
                "declaredModels": ["orders", "customers"],
                "cardinality": "MANY_TO_ONE",
                "condition": "orders.customer_id = customers.customer_id",
                "conditionColumns": {
                    "orders": ["customer_id"],
                    "customers": ["customer_id"],
                },
                "role": "buyer",
                "entity": "customer",
                "cardinalityValidation": "verified",
            },
            {
                "name": "orders_customers_bridge",
                "declaredModels": ["orders", "customers"],
                "cardinality": "MANY_TO_MANY",
                "condition": "orders.customer_id = customers.customer_id",
                "conditionColumns": {
                    "orders": ["customer_id"],
                    "customers": ["customer_id"],
                },
                "cardinalityValidation": "disabled",
            },
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": "SUM(amount)",
                "expandedExpression": "SUM(amount)",
                "type": "DECIMAL(38,2)",
                "label": "销售额",
                "description": "订单销售额",
                "synonyms": ["收入", "GMV"],
                "atomicFields": ["amount"],
                "additivity": "additive",
            }
        ],
        "dimensions": [
            {
                "name": "customer",
                "expression": "customer_id",
                "type": "BIGINT",
                "label": "客户",
                "description": "客户业务主键",
                "synonyms": ["买家"],
                "atomicFields": ["customer_id"],
                "masterModel": "customers",
            },
            {
                "name": "ds",
                "expression": "ds",
                "type": "DATE",
                "label": "日期",
                "synonyms": ["业务日期"],
                "atomicFields": ["ds"],
                "masterModel": None,
            },
        ],
        "metricBindings": [
            {
                "id": "revenue@orders",
                "metric": "revenue",
                "model": "orders",
                "requiredFields": ["amount"],
                "grain": {"fields": ["order_id"], "source": "primary_key"},
            }
        ],
        "dimensionBindings": [
            {
                "id": "customer@orders",
                "dimension": "customer",
                "model": "orders",
                "requiredFields": ["customer_id"],
                "isMaster": False,
            },
            {
                "id": "customer@customers",
                "dimension": "customer",
                "model": "customers",
                "requiredFields": ["customer_id"],
                "isMaster": True,
            },
            {
                "id": "ds@orders",
                "dimension": "ds",
                "model": "orders",
                "requiredFields": ["ds"],
                "isMaster": False,
            },
        ],
    }


def _cubes() -> list[dict]:
    return [
        {
            "name": "order_performance",
            "base_object": "orders",
            "label": "订单效果",
            "description": "按客户和日期分析订单销售额",
            "synonyms": ["订单表现"],
            "priority": 100,
            "measures": ["revenue"],
            "dimensions": ["customer"],
            "time_dimensions": ["ds"],
            "hierarchies": {
                "customer_drill": ["customer", "ds"],
            },
        }
    ]


def _by_id(graph: dict, item_id: str) -> dict:
    return next(item for item in graph["nodes"] if item["id"] == item_id)


def test_compile_wren_ontology_has_stable_types_and_hierarchy() -> None:
    graph = compile_ontology_graph(_semantic_graph(), cubes=_cubes())

    assert graph["kind"] == ONTOLOGY_KIND
    assert graph["readOnly"] is False
    assert graph["nodeTypes"] == sorted(NODE_TYPES)
    assert graph["edgeTypes"] == sorted(EDGE_TYPES)
    assert _by_id(graph, "metric:revenue")["synonyms"] == ["收入", "GMV"]
    assert _by_id(graph, "dimension:customer")["label"] == "客户"
    cube = _by_id(graph, "cube:order_performance")
    assert cube["label"] == "订单效果"
    assert cube["properties"] == {"baseObject": "orders", "priority": 100}
    hierarchy = _by_id(graph, "hierarchy:order_performance:customer_drill")
    assert hierarchy["properties"]["levels"] == ["customer", "ds"]

    levels = [edge for edge in graph["edges"] if edge["type"] == "HIERARCHY_LEVEL"]
    assert [(edge["targetId"], edge["properties"]["ordinal"]) for edge in levels] == [
        ("dimension:customer", 0),
        ("dimension:ds", 1),
    ]
    assert all("sourceId" in edge and "targetId" in edge for edge in graph["edges"])


def test_ontology_save_and_load_use_project_target(tmp_path: Path) -> None:
    graph = compile_ontology_graph(_semantic_graph(), cubes=_cubes())

    output = save_ontology_graph(graph, tmp_path)

    assert output == tmp_path / "target" / "ontology_graph.json"
    assert load_ontology_graph(tmp_path) == graph


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_import_ossie_yaml_and_json_is_read_only_and_lossless(
    tmp_path: Path, suffix: str
) -> None:
    document = {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "shop",
                "description": "零售语义模型",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "shop.public.orders",
                        "future_dataset_field": {"kept": True},
                        "fields": [
                            {
                                "name": "order_date",
                                "expression": {
                                    "dialects": [
                                        {
                                            "dialect": "ANSI_SQL",
                                            "expression": "order_date",
                                        }
                                    ]
                                },
                                "label": "订单日期",
                                "description": "下单日期",
                                "dimension": {"is_time": True},
                                "ai_context": {"synonyms": ["购买日期"]},
                                "custom_extensions": [
                                    {
                                        "vendor_name": "DBT",
                                        "data": '{"dimension_group":"order_date"}',
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    source = tmp_path / f"semantic_model{suffix}"
    if suffix == ".json":
        source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    else:
        source.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    graph = import_osi_ontology(source)

    assert graph["readOnly"] is True
    assert graph["source"]["format"] == "apache-ossie"
    assert graph["extensions"]["osi"]["sourceDocument"] == document
    dimension = _by_id(graph, "dimension:orders:order_date")
    assert dimension["label"] == "订单日期"
    assert dimension["synonyms"] == ["购买日期"]
    dataset = _by_id(graph, "dataset:orders")
    assert dataset["extensions"]["osi"]["unmapped"] == {
        "future_dataset_field": {"kept": True}
    }


def test_import_ossie_reuses_explicit_semantic_model_selection() -> None:
    document = {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {"name": "a", "datasets": [{"name": "a1", "source": "a1"}]},
            {"name": "b", "datasets": [{"name": "b1", "source": "b1"}]},
        ],
    }

    with pytest.raises(OntologyInterchangeError):
        import_osi_ontology(document)

    graph = import_osi_ontology(document, semantic_model="b")
    assert graph["source"]["semanticModel"] == "b"
    assert _by_id(graph, "dataset:b1")["name"] == "b1"
    assert any(
        issue["code"] == "OSSIE_IMPORT_MODEL_SELECTED" for issue in graph["diagnostics"]
    )


def test_export_ossie_core_projection_and_lossless_wren_extension() -> None:
    graph = compile_ontology_graph(_semantic_graph(), cubes=_cubes())

    document, diagnostics = export_ontology_to_osi(graph)

    assert document["version"] == "0.2.0.dev0"
    semantic_model = document["semantic_model"][0]
    assert semantic_model["name"] == "shop"
    assert {dataset["name"] for dataset in semantic_model["datasets"]} == {
        "orders",
        "customers",
    }
    orders = next(
        dataset for dataset in semantic_model["datasets"] if dataset["name"] == "orders"
    )
    assert orders["source"] == "shop.public.orders"
    assert orders["primary_key"] == ["order_id"]
    customer = next(field for field in orders["fields"] if field["name"] == "customer")
    assert customer["expression"]["dialects"][0]["expression"] == "customer_id"
    assert customer["label"] == "客户"
    assert customer["ai_context"]["synonyms"] == ["买家"]

    metric = semantic_model["metrics"][0]
    assert metric["name"] == "revenue"
    assert metric["description"] == "订单销售额"
    assert metric["ai_context"]["synonyms"] == ["收入", "GMV"]
    assert [item["name"] for item in semantic_model["relationships"]] == [
        "orders_customer"
    ]
    assert any(
        issue["code"] == "OSSIE_EXPORT_MANY_TO_MANY_EXTENSION_ONLY"
        for issue in diagnostics
    )
    assert any(
        issue["code"] == "OSSIE_EXPORT_ONTOLOGY_EXTENSION" for issue in diagnostics
    )

    wren_extension = next(
        item
        for item in semantic_model["custom_extensions"]
        if item["vendor_name"] == "WREN"
    )
    payload = json.loads(wren_extension["data"])
    assert payload["ontology_graph"] == graph
    assert payload["export_diagnostics"] == diagnostics


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_export_ossie_file_can_be_imported_again(tmp_path: Path, suffix: str) -> None:
    graph = compile_ontology_graph(_semantic_graph(), cubes=_cubes())
    output = tmp_path / f"shop{suffix}"

    path, diagnostics = export_ontology_to_osi_file(graph, output)
    imported = import_osi_ontology(path)

    assert path == output
    assert diagnostics
    assert imported["readOnly"] is True
    assert imported["source"]["semanticModel"] == "shop"
    assert _by_id(imported, "metric:revenue")["synonyms"] == ["收入", "GMV"]
