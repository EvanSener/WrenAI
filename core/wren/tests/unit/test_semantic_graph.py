"""Post-implementation coverage for the additive semantic model graph."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest
import yaml
from typer.testing import CliRunner

from wren.cli import app
from wren.context import load_relationships
from wren.semantic_graph import (
    GraphCompilationError,
    GraphPlanningError,
    compile_graph_bundle,
    plan_virtual_cube,
)
from wren.semantic_graph.advanced_planner import plan_graph_query
from wren.semantic_graph.queryability import build_queryability_index

runner = CliRunner()


def _write_metadata(root: Path, collection: str, name: str, data: dict) -> None:
    directory = root / collection / name
    directory.mkdir(parents=True)
    (directory / "metadata.yml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def _write_model(
    root: Path,
    name: str,
    fields: list[str],
    *,
    primary_key: str | None,
) -> None:
    data = {
        "name": name,
        "table_reference": {"table": name},
        "columns": [
            {"name": field, "type": "STRING", "properties": {}} for field in fields
        ],
    }
    if primary_key is not None:
        data["primary_key"] = primary_key
    _write_metadata(root, "models", name, data)


def _make_project(
    root: Path,
    *,
    customer_primary_key: str | None = "customer_id",
    relationships: list[dict] | None = None,
) -> Path:
    (root / "wren_project.yml").write_text(
        "schema_version: 5\n"
        "name: graph_test\n"
        "version: '1.0'\n"
        "catalog: wren\n"
        "schema: public\n"
        "data_source: maxcompute\n",
        encoding="utf-8",
    )
    _write_model(
        root,
        "orders",
        ["order_id", "customer_id", "amount", "ds"],
        primary_key="order_id",
    )
    customer_fields = ["customer_id", "country"]
    if customer_primary_key not in {None, "customer_id"}:
        customer_fields.append(customer_primary_key)
    _write_model(
        root,
        "customers",
        customer_fields,
        primary_key=customer_primary_key,
    )
    _write_metadata(
        root,
        "metrics",
        "revenue",
        {
            "name": "revenue",
            "expression": "SUM(amount)",
            "type": "DECIMAL(38, 6)",
            "label": "销售额",
        },
    )
    _write_metadata(
        root,
        "dimensions",
        "customer",
        {
            "name": "customer",
            "expression": "customer_id",
            "type": "STRING",
            "label": "客户",
        },
    )
    _write_metadata(
        root,
        "dimensions",
        "ds",
        {
            "name": "ds",
            "expression": "ds",
            "type": "STRING",
            "label": "日期",
        },
    )
    if relationships is None:
        relationships = [
            {
                "name": "orders_customer",
                "models": ["orders", "customers"],
                "join_type": "MANY_TO_ONE",
                "condition": "orders.customer_id = customers.customer_id",
            }
        ]
    document = {
        "graph": {
            "max_hops": 2,
            "master_data": {"attributes": {"customer": "customers"}},
        },
        "relationships": relationships,
    }
    (root / "relationships.yml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return root


def _binding(index: dict, metric: str, source: str) -> dict:
    return next(
        item
        for item in index["bindings"]
        if item["metric"] == metric and item["sourceModel"] == source
    )


def _update_metadata(root: Path, collection: str, name: str, **updates: object) -> None:
    path = root / collection / name / "metadata.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(updates)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _remove_legacy_master_data(root: Path) -> None:
    path = root / "relationships.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["graph"].pop("master_data", None)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_compiler_and_planner_build_safe_master_data_sql(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    bundle = compile_graph_bundle(project)

    assert bundle.semantic_graph["edgeSource"] == "relationships.yml"
    assert bundle.semantic_graph["edges"][0]["safeDirections"] == [
        ["orders", "customers"]
    ]
    assert load_relationships(project) == [
        {
            "name": "orders_customer",
            "models": ["orders", "customers"],
            "join_type": "MANY_TO_ONE",
            "condition": "orders.customer_id = customers.customer_id",
        }
    ]

    binding = _binding(bundle.queryability_index, "revenue", "orders")
    valid = {item["name"]: item for item in binding["validDimensions"]}
    assert valid["customer"]["bindingModel"] == "customers"
    assert valid["customer"]["isMaster"] is True
    assert valid["customer"]["hops"] == 1
    assert valid["ds"]["hops"] == 0

    plan = plan_virtual_cube(
        bundle.semantic_graph,
        bundle.queryability_index,
        source_model="orders",
        metrics=["revenue"],
        dimensions=["customer", "ds"],
    )
    assert plan["kind"] == "SINGLE_FACT_VIRTUAL_CUBE"
    assert [join["relationship"] for join in plan["joins"]] == ["orders_customer"]
    assert "LEFT JOIN `customers` AS g1" in plan["sql"]
    assert "g0.customer_id = g1.customer_id" in plan["sql"]
    assert "SUM(g0.amount) AS `revenue`" in plan["sql"]


def test_graph_query_compiles_incremental_fact_and_snapshot_dimension_partitions(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    orders_path = project / "models" / "orders" / "metadata.yml"
    orders = yaml.safe_load(orders_path.read_text(encoding="utf-8"))
    orders["table_reference"]["date_partition_type"] = "incremental"
    next(column for column in orders["columns"] if column["name"] == "ds")[
        "properties"
    ]["is_partition"] = True
    orders_path.write_text(yaml.safe_dump(orders, sort_keys=False), encoding="utf-8")

    customers_path = project / "models" / "customers" / "metadata.yml"
    customers = yaml.safe_load(customers_path.read_text(encoding="utf-8"))
    customers["table_reference"]["date_partition_type"] = "snapshot"
    customers["columns"].append(
        {
            "name": "ds",
            "type": "STRING",
            "properties": {
                "is_partition": True,
                "partition_default": "max_pt",
            },
        }
    )
    customers_path.write_text(
        yaml.safe_dump(customers, sort_keys=False), encoding="utf-8"
    )

    bundle = compile_graph_bundle(project)
    policies = {
        node["name"]: node.get("partitionPolicy")
        for node in bundle.semantic_graph["nodes"]
    }
    assert policies["orders"]["type"] == "incremental"
    assert policies["customers"]["type"] == "snapshot"

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_query(
            bundle.semantic_graph,
            bundle.queryability_index,
            {
                "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
                "dimensions": ["customer"],
            },
        )
    assert caught.value.code == "GRAPH_PARTITION_RANGE_REQUIRED"

    plan = plan_graph_query(
        bundle.semantic_graph,
        bundle.queryability_index,
        {
            "dateRange": {"start": "20260101", "end": "20260131"},
            "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
            "dimensions": ["customer"],
        },
    )
    sql = plan["sql"]
    assert (
        "FROM (SELECT * FROM `orders` WHERE ds BETWEEN '20260101' AND '20260131') AS s"
        in sql
    )
    assert "(SELECT * FROM `customers` WHERE ds = MAX_PT('customers')) AS j0" in sql
    assert "orders')" not in sql
    fact = plan["relationalPlan"]["facts"][0]
    assert fact["relationPartitions"]["orders"]["mode"] == "closed_range"
    assert fact["relationPartitions"]["customers"]["mode"] == "latest"

    per_fact_plan = plan_graph_query(
        bundle.semantic_graph,
        bundle.queryability_index,
        {
            "facts": [
                {
                    "sourceModel": "orders",
                    "metrics": ["revenue"],
                    "dateRange": {"start": "20260201", "end": "20260201"},
                }
            ],
            "dimensions": ["customer"],
        },
    )
    assert "orders` WHERE ds = '20260201'" in per_fact_plan["sql"]


def test_definition_master_models_mark_bindings_and_filter_queryability(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    _update_metadata(project, "metrics", "revenue", master_model="orders")
    _update_metadata(project, "dimensions", "customer", master_model="customers")

    customers_path = project / "models" / "customers" / "metadata.yml"
    customers = yaml.safe_load(customers_path.read_text(encoding="utf-8"))
    customers["columns"].append(
        {"name": "amount", "type": "DECIMAL(38, 6)", "properties": {}}
    )
    customers_path.write_text(
        yaml.safe_dump(customers, sort_keys=False), encoding="utf-8"
    )

    bundle = compile_graph_bundle(project)
    graph = bundle.semantic_graph

    revenue = next(item for item in graph["metrics"] if item["name"] == "revenue")
    customer = next(item for item in graph["dimensions"] if item["name"] == "customer")
    assert revenue["masterModel"] == "orders"
    assert customer["masterModel"] == "customers"

    metric_bindings = {
        item["model"]: item["isMaster"]
        for item in graph["metricBindings"]
        if item["metric"] == "revenue"
    }
    dimension_bindings = {
        item["model"]: item["isMaster"]
        for item in graph["dimensionBindings"]
        if item["dimension"] == "customer"
    }
    assert metric_bindings == {"customers": False, "orders": True}
    assert dimension_bindings == {"customers": True, "orders": False}

    conflicts = {
        (item["kind"], item["member"]): item for item in graph["bindingConflicts"]
    }
    assert conflicts[("metric", "revenue")]["resolution"] == "master_data"
    assert conflicts[("metric", "revenue")]["masterModel"] == "orders"
    assert conflicts[("dimension", "customer")]["masterModel"] == "customers"

    revenue_sources = [
        item["sourceModel"]
        for item in bundle.queryability_index["bindings"]
        if item["metric"] == "revenue"
    ]
    assert revenue_sources == ["orders"]

    with pytest.raises(GraphPlanningError) as caught:
        plan_virtual_cube(
            graph,
            bundle.queryability_index,
            source_model="customers",
            metrics=["revenue"],
            dimensions=[],
        )
    assert caught.value.code == "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
    assert caught.value.details == {
        "memberKind": "metric",
        "member": "revenue",
        "masterModel": "orders",
        "requestedModel": "customers",
    }


def test_simple_planner_rechecks_dimension_master_against_stale_index(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    _update_metadata(project, "dimensions", "customer", master_model="customers")
    bundle = compile_graph_bundle(project)
    stale_index = json.loads(json.dumps(bundle.queryability_index))
    binding = _binding(stale_index, "revenue", "orders")
    customer = next(
        item for item in binding["validDimensions"] if item["name"] == "customer"
    )
    customer["bindingModel"] = "orders"
    customer["isMaster"] = False

    with pytest.raises(GraphPlanningError) as caught:
        plan_virtual_cube(
            bundle.semantic_graph,
            stale_index,
            source_model="orders",
            metrics=["revenue"],
            dimensions=["customer"],
        )

    assert caught.value.code == "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
    assert caught.value.details["memberKind"] == "dimension"
    assert caught.value.details["masterModel"] == "customers"
    assert caught.value.details["requestedModel"] == "orders"


@pytest.mark.parametrize(
    ("collection", "member"),
    [("metrics", "revenue"), ("dimensions", "customer")],
)
def test_definition_master_model_must_reference_a_graph_node(
    tmp_path: Path, collection: str, member: str
) -> None:
    project = _make_project(tmp_path)
    _remove_legacy_master_data(project)
    _update_metadata(project, collection, member, master_model="missing_model")

    with pytest.raises(GraphCompilationError) as caught:
        compile_graph_bundle(project)

    assert any(
        issue.code == "GRAPH_MASTER_NODE_MISSING" for issue in caught.value.issues
    )
    assert any(
        issue.path == f"{collection}/{member}/metadata.yml > master_model"
        for issue in caught.value.issues
    )


@pytest.mark.parametrize(
    ("collection", "member", "error_code"),
    [
        ("metrics", "revenue", "METRIC_MASTER_MODEL_INVALID"),
        ("dimensions", "customer", "DIMENSION_MASTER_MODEL_INVALID"),
    ],
)
def test_invalid_master_model_keeps_a_structured_error_code(
    tmp_path: Path,
    collection: str,
    member: str,
    error_code: str,
) -> None:
    project = _make_project(tmp_path)
    _update_metadata(project, collection, member, master_model=42)

    with pytest.raises(GraphCompilationError) as caught:
        compile_graph_bundle(project)

    assert any(issue.code == error_code for issue in caught.value.issues)


@pytest.mark.parametrize(
    ("collection", "member", "master_model"),
    [
        ("metrics", "revenue", "customers"),
        ("dimensions", "ds", "customers"),
    ],
)
def test_definition_master_model_must_expose_member_atomic_fields(
    tmp_path: Path, collection: str, member: str, master_model: str
) -> None:
    project = _make_project(tmp_path)
    _update_metadata(project, collection, member, master_model=master_model)

    with pytest.raises(GraphCompilationError) as caught:
        compile_graph_bundle(project)

    assert any(
        issue.code == "GRAPH_MASTER_BINDING_INVALID" for issue in caught.value.issues
    )


def test_definition_and_legacy_master_models_must_not_conflict(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    _update_metadata(project, "dimensions", "customer", master_model="orders")

    with pytest.raises(GraphCompilationError) as caught:
        compile_graph_bundle(project)

    conflict = next(
        issue
        for issue in caught.value.issues
        if issue.code == "GRAPH_MASTER_MODEL_CONFLICT"
    )
    assert conflict.path == "dimensions/customer/metadata.yml > master_model"
    assert "relationships.yml declares 'customers'" in conflict.message


def test_unverified_cardinality_is_fail_closed(tmp_path: Path) -> None:
    project = _make_project(tmp_path, customer_primary_key=None)

    bundle = compile_graph_bundle(project)

    edge = bundle.semantic_graph["edges"][0]
    assert edge["cardinalityValidation"] == "unverified"
    assert edge["declaredDirections"] == [["orders", "customers"]]
    assert edge["safeDirections"] == []
    assert any(issue.code == "GRAPH_CARDINALITY_UNVERIFIED" for issue in bundle.issues)
    binding = _binding(bundle.queryability_index, "revenue", "orders")
    invalid = {item["name"]: item for item in binding["invalidDimensions"]}
    assert invalid["customer"]["code"] == "MASTER_DATA_UNREACHABLE"

    edge["safeDirections"] = [["orders", "customers"]]
    rebuilt = build_queryability_index(bundle.semantic_graph, max_hops=2)
    rebuilt_binding = _binding(rebuilt, "revenue", "orders")
    assert not any(
        item["name"] == "customer" for item in rebuilt_binding["validDimensions"]
    )


def test_wrong_one_side_primary_key_rejects_build(tmp_path: Path) -> None:
    project = _make_project(tmp_path, customer_primary_key="id")

    with pytest.raises(GraphCompilationError) as caught:
        compile_graph_bundle(project)

    assert any(
        issue.code == "GRAPH_CARDINALITY_KEY_MISMATCH" for issue in caught.value.issues
    )


def test_duplicate_model_pair_requires_relationship_roles(tmp_path: Path) -> None:
    relationships = [
        {
            "name": name,
            "models": ["orders", "customers"],
            "join_type": "MANY_TO_ONE",
            "condition": "orders.customer_id = customers.customer_id",
        }
        for name in ("billing_customer", "shipping_customer")
    ]
    project = _make_project(tmp_path, relationships=relationships)

    with pytest.raises(GraphCompilationError) as caught:
        compile_graph_bundle(project)

    assert (
        sum(issue.code == "GRAPH_ROLE_REQUIRED" for issue in caught.value.issues) == 2
    )


def test_equal_length_safe_paths_are_reported_as_ambiguous() -> None:
    edges = []
    for name, source, target in (
        ("fact_a", "fact", "a"),
        ("fact_b", "fact", "b"),
        ("a_dimension", "a", "dimension"),
        ("b_dimension", "b", "dimension"),
    ):
        edges.append(
            {
                "name": name,
                "cardinality": "MANY_TO_ONE",
                "condition": f"{source}.id = {target}.id",
                "cardinalityValidation": "verified",
                "safeDirections": [[source, target]],
            }
        )
    graph = {
        "schemaVersion": 1,
        "edges": edges,
        "metrics": [{"name": "revenue"}],
        "dimensions": [{"name": "region", "masterModel": "dimension"}],
        "metricBindings": [
            {"id": "revenue@fact", "metric": "revenue", "model": "fact"}
        ],
        "dimensionBindings": [
            {"id": "region@dimension", "dimension": "region", "model": "dimension"}
        ],
    }

    index = build_queryability_index(graph, max_hops=2)

    binding = _binding(index, "revenue", "fact")
    invalid = {item["name"]: item for item in binding["invalidDimensions"]}
    assert invalid["region"]["code"] == "AMBIGUOUS_SAFE_PATH"
    assert len(invalid["region"]["candidatePaths"]) == 2


def test_metric_additivity_policy_is_compiled(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    relationship_path = project / "relationships.yml"
    document = yaml.safe_load(relationship_path.read_text(encoding="utf-8"))
    document["graph"]["metric_policies"] = {
        "revenue": {
            "additivity": "semi_additive",
            "blocked_dimensions": ["ds"],
        }
    }
    relationship_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    bundle = compile_graph_bundle(project)

    metric = next(
        item for item in bundle.semantic_graph["metrics"] if item["name"] == "revenue"
    )
    assert metric["additivity"] == "semi_additive"
    assert metric["blockedDimensions"] == ["ds"]
    assert metric["additivitySource"] == "configured"


def test_many_to_many_bridge_and_allocation_are_verified(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_model(
        project,
        "order_customer_bridge",
        ["bridge_id", "order_id", "customer_id", "allocation_weight"],
        primary_key="bridge_id",
    )
    relationships = [
        {
            "name": "orders_customers_many_to_many",
            "models": ["orders", "customers"],
            "join_type": "MANY_TO_MANY",
            "condition": "orders.customer_id = customers.customer_id",
        },
        {
            "name": "bridge_orders",
            "models": ["order_customer_bridge", "orders"],
            "join_type": "MANY_TO_ONE",
            "condition": "order_customer_bridge.order_id = orders.order_id",
        },
        {
            "name": "bridge_customers",
            "models": ["order_customer_bridge", "customers"],
            "join_type": "MANY_TO_ONE",
            "condition": "order_customer_bridge.customer_id = customers.customer_id",
        },
    ]
    document = {
        "graph": {
            "max_hops": 2,
            "master_data": {"attributes": {"customer": "customers"}},
            "bridges": {
                "orders_customers_many_to_many": {
                    "model": "order_customer_bridge",
                    "source_relationship": "bridge_orders",
                    "target_relationship": "bridge_customers",
                    "allocation_expression": "allocation_weight",
                    "allocation_mode": "weighted",
                }
            },
        },
        "relationships": relationships,
    }
    (project / "relationships.yml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    bundle = compile_graph_bundle(project)

    edge = next(
        item
        for item in bundle.semantic_graph["edges"]
        if item["name"] == "orders_customers_many_to_many"
    )
    assert edge["cardinalityValidation"] == "bridge_verified"
    assert edge["safeDirections"] == []
    assert edge["bridgePolicy"] == {
        "model": "order_customer_bridge",
        "sourceRelationship": "bridge_orders",
        "targetRelationship": "bridge_customers",
        "allocationExpression": "allocation_weight",
        "allocationMode": "weighted",
    }


def test_graph_cli_build_explain_and_query(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    built = runner.invoke(app, ["graph", "build", "--path", str(project), "--json"])
    assert built.exit_code == 0, built.output
    summary = json.loads(built.output)
    assert summary["nodes"] == 2
    assert summary["edges"] == 1
    assert (project / "target" / "semantic_graph.json").exists()
    assert summary["ontologyNodes"] > 0
    assert (project / "target" / "ontology_graph.json").exists()

    explained = runner.invoke(
        app,
        [
            "graph",
            "explain",
            "--path",
            str(project),
            "--source",
            "orders",
            "--metrics",
            "revenue",
            "--dimensions",
            "customer,ds",
        ],
    )
    assert explained.exit_code == 0, explained.output
    assert "orders -> customers via orders_customer" in explained.output

    queried = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--path",
            str(project),
            "--source",
            "orders",
            "--metrics",
            "revenue",
            "--dimensions",
            "customer,ds",
        ],
    )
    assert queried.exit_code == 0, queried.output
    assert "LEFT JOIN `customers` AS g1" in queried.output


def test_graph_cli_query_execute_uses_same_plan_and_project_mdl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    built = runner.invoke(app, ["graph", "build", "--path", str(project)])
    assert built.exit_code == 0, built.output

    captured: dict[str, object] = {}

    class FakeEngine:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def query(self, sql: str, *, limit: int | None = None):
            captured["sql"] = sql
            captured["limit"] = limit
            return pa.table({"revenue": [42]})

    def fake_build_engine(
        mdl, connection_info, connection_file, *, verbose_errors=False
    ):
        captured["mdl"] = mdl
        captured["connection_info"] = connection_info
        captured["connection_file"] = connection_file
        captured["verbose_errors"] = verbose_errors
        return FakeEngine()

    monkeypatch.setattr("wren.cli._build_engine", fake_build_engine)
    executed = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--path",
            str(project),
            "--source",
            "orders",
            "--metrics",
            "revenue",
            "--dimensions",
            "customer,ds",
            "--execute",
            "--result-output",
            "json",
            "--limit",
            "7",
        ],
    )

    assert executed.exit_code == 0, executed.output
    assert '"revenue":42' in executed.output
    assert captured["mdl"] == str(project / "target" / "mdl.json")
    assert captured["limit"] == 7
    assert captured["verbose_errors"] is False
    assert "LEFT JOIN `customers` AS g1" in str(captured["sql"])


def test_graph_cli_query_execute_resolves_recent_days_from_latest_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    orders_path = project / "models" / "orders" / "metadata.yml"
    orders = yaml.safe_load(orders_path.read_text(encoding="utf-8"))
    orders["table_reference"]["date_partition_type"] = "incremental"
    next(column for column in orders["columns"] if column["name"] == "ds")[
        "properties"
    ]["is_partition"] = True
    orders_path.write_text(yaml.safe_dump(orders, sort_keys=False), encoding="utf-8")
    built = runner.invoke(app, ["graph", "build", "--path", str(project)])
    assert built.exit_code == 0, built.output

    captured_sql: list[str] = []

    class FakeEngine:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def query(self, sql: str, *, limit: int | None = None):
            captured_sql.append(sql)
            if "MAX_PT" in sql.upper():
                return pa.table({"max_ds": ["20260717"]})
            assert limit == 7
            return pa.table({"customer": ["c1"], "revenue": [42]})

    monkeypatch.setattr(
        "wren.cli._build_engine",
        lambda *_args, **_kwargs: FakeEngine(),
    )
    executed = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--path",
            str(project),
            "--question",
            "最近15天按客户统计销售额",
            "--execute",
            "--result-output",
            "json",
            "--limit",
            "7",
        ],
    )

    assert executed.exit_code == 0, executed.output
    assert '"revenue":42' in executed.output
    assert len(captured_sql) == 2
    assert captured_sql[0] == "SELECT MAX_PT('orders') AS max_ds"
    assert "orders` WHERE ds BETWEEN '20260703' AND '20260717'" in captured_sql[1]
    assert "JOIN `customers`" not in captured_sql[1]


def test_graph_ontology_and_inspection_cli(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    built = runner.invoke(app, ["graph", "build", "--path", str(project)])
    assert built.exit_code == 0, built.output

    inspected = runner.invoke(
        app,
        [
            "graph",
            "inspect",
            "--path",
            str(project),
            "--artifact",
            "ontology",
            "--query",
            "MATCH (m:METRIC)-[r:METRIC_BINDING]->(d:DATASET) "
            "RETURN m.name AS metric, d.name AS dataset ORDER BY dataset LIMIT 2",
            "--output",
            "rows",
        ],
    )
    assert inspected.exit_code == 0, inspected.output
    rows = json.loads(inspected.output)
    assert rows == [
        {"metric": "revenue", "dataset": "orders"},
    ]

    osi_path = project / "target" / "ontology.osi.yml"
    exported = runner.invoke(
        app,
        [
            "graph",
            "ontology",
            "export-osi",
            "--path",
            str(project),
            "--output",
            str(osi_path),
        ],
    )
    assert exported.exit_code == 0, exported.output
    assert osi_path.exists()

    imported_path = project / "target" / "ontology_imported.json"
    imported = runner.invoke(
        app,
        [
            "graph",
            "ontology",
            "import-osi",
            str(osi_path),
            "--path",
            str(project),
            "--output",
            str(imported_path),
        ],
    )
    assert imported.exit_code == 0, imported.output
    imported_graph = json.loads(imported_path.read_text(encoding="utf-8"))
    assert imported_graph["readOnly"] is True


def test_graph_dynamic_request_and_discovery_cli(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    built = runner.invoke(app, ["graph", "build", "--path", str(project)])
    assert built.exit_code == 0, built.output

    request_path = project / "dynamic_request.yml"
    request_path.write_text(
        yaml.safe_dump(
            {
                "anchorModel": "orders",
                "facts": [{"sourceModel": "orders", "metrics": ["revenue"]}],
                "dimensions": ["customer", "ds"],
                "attributes": [
                    {
                        "model": "customers",
                        "field": "country",
                        "alias": "country",
                        "relationshipPath": ["orders_customer"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    queried = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--path",
            str(project),
            "--request",
            str(request_path),
            "--output",
            "json",
        ],
    )
    assert queried.exit_code == 0, queried.output
    plan = json.loads(queried.output)
    assert plan["kind"] == "DYNAMIC_VIRTUAL_CUBE"
    assert plan["relationalPlan"]["virtualWideTable"]["schema"]

    discovered = runner.invoke(
        app,
        [
            "graph",
            "discover",
            "--path",
            str(project),
            "--anchor",
            "orders",
        ],
    )
    assert discovered.exit_code == 0, discovered.output
    virtual = json.loads(discovered.output)
    attribute_names = {
        item["name"] for item in virtual["schema"] if item["kind"] == "attribute"
    }
    assert {"orders.amount", "customers.country"}.issubset(attribute_names)
