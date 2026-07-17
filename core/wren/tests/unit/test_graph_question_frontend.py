"""Coverage for the natural-language semantic-graph frontend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlglot
from typer.testing import CliRunner

from wren.cli import app
from wren.semantic_graph.frontend import plan_frontend_query
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.question import (
    _extract_explicit_date_range,
    plan_graph_question,
    resolve_graph_question,
)
from wren.semantic_graph.relative_date import (
    date_range_from_latest_partition,
    extract_relative_date_window,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "统计 20260101 到 20260131 的销售额",
            {"start": "20260101", "end": "20260131"},
        ),
        (
            "统计 2026-01-01 当天销售额",
            {"start": "20260101", "end": "20260101"},
        ),
    ],
)
def test_question_normalizes_explicit_partition_dates(
    question: str, expected: dict[str, str]
) -> None:
    assert _extract_explicit_date_range(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "最近15天按客户统计销售额",
        "过去 15 日按客户统计销售额",
        "revenue by customer in the last 15 days",
    ],
)
def test_question_extracts_latest_partition_relative_window(question: str) -> None:
    relative = extract_relative_date_window(question)

    assert relative == {"days": 15, "anchor": "latest_partition"}
    assert date_range_from_latest_partition(relative, "20260717") == {
        "start": "20260703",
        "end": "20260717",
    }


def _node(name: str, fields: list[str], *, label: str | None = None) -> dict:
    return {
        "name": name,
        "label": label,
        "description": label,
        "primaryKey": ["id"],
        "grain": {"fields": ["id"], "source": "primary_key"},
        "attributes": [{"name": field, "type": "STRING"} for field in fields],
        "entities": [],
        "relation": {"type": "table", "tableReference": {"table": name}},
    }


def _edge(
    name: str,
    left: str,
    right: str,
    condition: str,
    *,
    cardinality: str = "MANY_TO_ONE",
) -> dict:
    safe = [[left, right]] if cardinality == "MANY_TO_ONE" else [[right, left]]
    return {
        "name": name,
        "declaredModels": [left, right],
        "cardinality": cardinality,
        "condition": condition,
        "safeDirections": safe,
        "cardinalityValidation": "verified",
        "role": None,
        "entity": None,
    }


def _graph(*, second_fact: bool = False, fanout: bool = False) -> dict:
    nodes = [
        _node("fact_a", ["id", "b_id", "amount"], label="事实A"),
        _node("bridge_b", ["id", "c_id"], label="中间B"),
        _node("leaf_c", ["id", "region"], label="叶子C"),
    ]
    edges = [
        _edge("a_b", "fact_a", "bridge_b", "fact_a.b_id = bridge_b.id"),
        _edge("b_c", "bridge_b", "leaf_c", "bridge_b.c_id = leaf_c.id"),
    ]
    metric_bindings = [{"id": "revenue@fact_a", "metric": "revenue", "model": "fact_a"}]
    if second_fact:
        nodes.append(_node("fact_x", ["id", "c_id", "amount"], label="事实X"))
        edges.append(_edge("x_c", "fact_x", "leaf_c", "fact_x.c_id = leaf_c.id"))
        metric_bindings.append(
            {"id": "revenue@fact_x", "metric": "revenue", "model": "fact_x"}
        )
    if fanout:
        nodes = [
            _node("fact_a", ["id", "amount"], label="事实A"),
            _node("leaf_c", ["id", "fact_id", "region"], label="叶子C"),
        ]
        edges = [
            _edge(
                "c_a",
                "leaf_c",
                "fact_a",
                "leaf_c.fact_id = fact_a.id",
                cardinality="MANY_TO_ONE",
            )
        ]
    return {
        "schemaVersion": 1,
        "project": {"name": "question", "dataSource": "maxcompute"},
        "nodes": nodes,
        "edges": edges,
        "metrics": [
            {
                "name": "revenue",
                "label": "销售额",
                "description": "事实销售金额",
                "synonyms": ["收入"],
                "expandedExpression": "SUM(amount)",
                "atomicFields": ["amount"],
                "additivity": "additive",
                "blockedDimensions": [],
                "additivitySource": "inferred",
            }
        ],
        "dimensions": [
            {
                "name": "leaf_region",
                "label": "叶子地区",
                "description": "叶子C所属地区",
                "synonyms": ["C地区"],
                "expression": "region",
                "type": "STRING",
                "masterModel": "leaf_c",
            }
        ],
        "metricBindings": metric_bindings,
        "dimensionBindings": [
            {
                "id": "leaf_region@leaf_c",
                "dimension": "leaf_region",
                "model": "leaf_c",
                "isMaster": True,
            }
        ],
    }


def _ontology(graph: dict) -> dict:
    nodes = [
        {
            "id": "metric:revenue",
            "type": "METRIC",
            "name": "revenue",
            "label": "销售额",
            "description": "事实销售金额",
            "synonyms": ["收入"],
            "properties": {},
        },
        {
            "id": "dimension:leaf_region",
            "type": "DIMENSION",
            "name": "leaf_region",
            "label": "叶子地区",
            "description": "叶子C所属地区",
            "synonyms": ["C地区"],
            "properties": {},
        },
    ]
    nodes.extend(
        {
            "id": f"dataset:{node['name']}",
            "type": "DATASET",
            "name": node["name"],
            "label": node.get("label"),
            "description": node.get("description"),
            "synonyms": [],
            "properties": {},
        }
        for node in graph["nodes"]
    )
    return {"schemaVersion": 1, "nodes": nodes, "edges": []}


def _index() -> dict:
    return {"schemaVersion": 1, "maxHops": 2, "bindings": []}


def test_question_compiles_ontology_members_over_safe_a_b_c_path() -> None:
    graph = _graph()

    plan = plan_graph_question(
        graph,
        _index(),
        "按叶子地区看事实A销售额",
        ontology_graph=_ontology(graph),
    )

    resolution = plan["frontendResolution"]
    assert resolution["catalogSource"] == "ontology_graph"
    assert [item["name"] for item in resolution["metrics"]] == ["revenue"]
    assert [item["name"] for item in resolution["dimensions"]] == ["leaf_region"]
    assert resolution["selectedAnchor"] == "fact_a"
    selector = plan["graphQuery"]["dimensions"][0]
    assert plan["graphQuery"]["schemaVersion"] == 1
    assert selector["relationshipPath"] == ["a_b", "b_c"]
    dimension = plan["relationalPlan"]["facts"][0]["dimensions"][0]
    assert [step["relationship"] for step in dimension["path"]] == ["a_b", "b_c"]
    assert plan["strategy"] == "SINGLE_FACT_SAFE"
    assert sqlglot.parse_one(plan["sql"], dialect="hive") is not None


def test_explicit_group_by_phrase_excludes_subject_dimensions() -> None:
    graph = _graph()
    graph["dimensions"].append(
        {
            "name": "fact_subject",
            "label": "事实A",
            "description": "事实A业务对象",
            "synonyms": [],
            "expression": "id",
            "type": "STRING",
            "masterModel": "fact_a",
        }
    )
    graph["dimensionBindings"].append(
        {
            "id": "fact_subject@fact_a",
            "dimension": "fact_subject",
            "model": "fact_a",
            "isMaster": True,
        }
    )
    ontology = _ontology(graph)
    ontology["nodes"].append(
        {
            "id": "dimension:fact_subject",
            "type": "DIMENSION",
            "name": "fact_subject",
            "label": "事实A",
            "description": "事实A业务对象",
            "synonyms": [],
            "properties": {},
        }
    )

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区统计事实A的销售额",
        ontology_graph=ontology,
    )

    assert resolution["status"] == "resolved"
    assert [item["name"] for item in resolution["dimensions"]] == ["leaf_region"]


def test_business_subject_outranks_generic_cube_metric_context() -> None:
    graph = _graph(second_fact=True)
    ontology = _ontology(graph)
    ontology["nodes"].append(
        {
            "id": "cube:generic_x",
            "type": "CUBE",
            "name": "generic_x",
            "label": "通用效果",
            "description": "销售额收入指标",
            "synonyms": [],
            "properties": {"priority": 100},
        }
    )
    ontology["edges"].extend(
        [
            {
                "type": "CUBE_BASE_DATASET",
                "sourceId": "cube:generic_x",
                "targetId": "dataset:fact_x",
            },
            {
                "type": "CUBE_METRIC",
                "sourceId": "cube:generic_x",
                "targetId": "metric:revenue",
            },
            {
                "type": "CUBE_DIMENSION",
                "sourceId": "cube:generic_x",
                "targetId": "dimension:leaf_region",
            },
        ]
    )

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区统计事实A销售额",
        ontology_graph=ontology,
    )

    assert resolution["status"] == "resolved"
    assert resolution["selectedAnchor"] == "fact_a"


def test_equal_source_evidence_is_reported_without_guessing() -> None:
    graph = _graph(second_fact=True)

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区看销售额",
        ontology_graph=_ontology(graph),
    )

    assert resolution["status"] == "ambiguous"
    assert {item["anchorModel"] for item in resolution["candidates"]} == {
        "fact_a",
        "fact_x",
    }


def test_master_metric_binding_drives_natural_language_source_and_explain() -> None:
    graph = _graph(second_fact=True)
    graph["metrics"][0]["masterModel"] = "fact_x"
    for binding in graph["metricBindings"]:
        binding["isMaster"] = binding["model"] == "fact_x"

    plan = plan_graph_question(
        graph,
        _index(),
        "按叶子地区看销售额",
        ontology_graph=_ontology(graph),
    )

    resolution = plan["frontendResolution"]
    assert resolution["status"] == "resolved"
    assert resolution["selectedAnchor"] == "fact_x"
    assert resolution["graphQuery"]["facts"] == [
        {"sourceModel": "fact_x", "metrics": ["revenue"]}
    ]
    assert resolution["selectionEvidence"]["masterMetricBindings"] == ["revenue"]

    explained_metric = plan["graphExplain"]["facts"][0]["metrics"][0]
    assert explained_metric["masterModel"] == "fact_x"
    assert explained_metric["isMaster"] is True
    metric_decision = next(
        item
        for item in plan["graphExplain"]["pathDecisions"]
        if item["memberKind"] == "metric" and item["member"] == "revenue"
    )
    assert metric_decision["bindingModel"] == "fact_x"
    assert metric_decision["decision"] == "masterDataBinding"

    non_master = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区看销售额",
        ontology_graph=_ontology(graph),
        anchor_model="fact_a",
    )
    assert non_master["status"] == "not_queryable"
    assert non_master["rejectedCandidates"][0]["code"] == (
        "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
    )

    with pytest.raises(GraphPlanningError) as caught:
        plan_graph_question(
            graph,
            _index(),
            "按叶子地区看销售额",
            ontology_graph=_ontology(graph),
            anchor_model="fact_a",
        )
    assert caught.value.code == "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN"
    assert caught.value.details["rejectedCandidates"][0]["details"] == {
        "memberKind": "metric",
        "member": "revenue",
        "masterModel": "fact_x",
        "requestedModel": "fact_a",
    }


def test_unknown_dimension_phrase_is_not_silently_dropped() -> None:
    graph = _graph()

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按用户状态看事实A销售额",
        ontology_graph=_ontology(graph),
    )

    assert resolution["status"] == "unresolved_dimension"
    assert resolution["dimensionPhrase"] == "用户状态"


def test_one_unknown_dimension_in_a_list_is_not_silently_dropped() -> None:
    graph = _graph()

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区和用户状态看事实A销售额",
        ontology_graph=_ontology(graph),
    )

    assert resolution["status"] == "unresolved_dimension"
    assert resolution["dimensionPhrases"] == ["叶子地区", "用户状态"]
    assert resolution["unresolvedDimensionPhrases"] == ["用户状态"]


def test_natural_language_frontend_rejects_implicit_fanout() -> None:
    graph = _graph(fanout=True)

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区看事实A销售额",
        ontology_graph=_ontology(graph),
    )

    assert resolution["status"] == "not_queryable"
    assert resolution["rejectedCandidates"][0]["code"] == (
        "GRAPH_QUESTION_FANOUT_REQUIRES_STRUCTURED_REQUEST"
    )


def test_natural_language_frontend_keeps_equal_shortest_paths_ambiguous() -> None:
    graph = _graph()
    graph["nodes"].append(_node("bridge_d", ["id", "c_id"], label="中间D"))
    graph["edges"].extend(
        [
            _edge("a_d", "fact_a", "bridge_d", "fact_a.b_id = bridge_d.id"),
            _edge("d_c", "bridge_d", "leaf_c", "bridge_d.c_id = leaf_c.id"),
        ]
    )

    resolution = resolve_graph_question(
        graph,
        _index(),
        "按叶子地区看事实A销售额",
        ontology_graph=_ontology(graph),
    )

    assert resolution["status"] == "not_queryable"
    assert resolution["rejectedCandidates"][0]["code"] == "GRAPH_PATH_AMBIGUOUS"


def test_custom_frontend_reuses_the_same_governed_planner() -> None:
    graph = _graph()

    class StaticFrontend:
        name = "static_test"

        def compile(self, payload, **_kwargs):
            return {
                "inputKind": "test",
                "request": {
                    "schemaVersion": 1,
                    "facts": [{"sourceModel": "fact_a", "metrics": ["revenue"]}],
                    "dimensions": [
                        {
                            "name": "leaf_region",
                            "relationshipPath": ["a_b", "b_c"],
                        }
                    ],
                },
                "resolution": {"payload": payload},
            }

    plan = plan_frontend_query(
        graph,
        _index(),
        StaticFrontend(),
        {"query": "anything"},
    )

    assert plan["queryFrontend"] == {"name": "static_test", "inputKind": "test"}
    assert plan["frontendResolution"] == {"payload": {"query": "anything"}}
    assert plan["strategy"] == "SINGLE_FACT_SAFE"


def _write_graph_project(path: Path, graph: dict) -> None:
    path.joinpath("wren_project.yml").write_text(
        "schema_version: 2\nname: graph_cli_test\ndata_source: maxcompute\n",
        encoding="utf-8",
    )
    target = path / "target"
    target.mkdir()
    target.joinpath("semantic_graph.json").write_text(
        json.dumps(graph), encoding="utf-8"
    )
    target.joinpath("queryability_index.json").write_text(
        json.dumps(_index()), encoding="utf-8"
    )
    target.joinpath("ontology_graph.json").write_text(
        json.dumps(_ontology(graph)), encoding="utf-8"
    )


def test_graph_cli_resolve_and_query_question_over_a_b_c(tmp_path: Path) -> None:
    graph = _graph()
    _write_graph_project(tmp_path, graph)
    runner = CliRunner()

    resolved = runner.invoke(
        app,
        [
            "graph",
            "resolve",
            "按叶子地区看事实A销售额",
            "--path",
            str(tmp_path),
            "--output",
            "json",
        ],
    )
    assert resolved.exit_code == 0, resolved.output
    resolution = json.loads(resolved.stdout)
    assert resolution["selectedAnchor"] == "fact_a"
    assert resolution["graphQuery"]["dimensions"][0]["relationshipPath"] == [
        "a_b",
        "b_c",
    ]

    queried = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--question",
            "按叶子地区看事实A销售额",
            "--path",
            str(tmp_path),
        ],
    )
    assert queried.exit_code == 0, queried.output
    assert queried.stdout.count("LEFT JOIN") == 2
    assert "`fact_a`" in queried.stdout
    assert "`bridge_b`" in queried.stdout
    assert "`leaf_c`" in queried.stdout
    assert sqlglot.parse_one(queried.stdout, dialect="hive") is not None


def test_graph_cli_query_timings_keep_sql_stdout_clean(tmp_path: Path) -> None:
    graph = _graph()
    _write_graph_project(tmp_path, graph)
    runner = CliRunner()

    queried = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--question",
            "按叶子地区看事实A销售额",
            "--path",
            str(tmp_path),
            "--timings",
        ],
    )

    assert queried.exit_code == 0, queried.output
    assert sqlglot.parse_one(queried.stdout, dialect="hive") is not None
    timing_lines = queried.stderr.splitlines()
    assert len(timing_lines) == 1
    payload = json.loads(timing_lines[0])
    assert payload["schemaVersion"] == 1
    assert payload["kind"] == "GRAPH_QUERY_TIMINGS"
    assert payload["status"] == "success"
    assert {
        "requestPreparation",
        "projectDiscovery",
        "securityPolicy",
        "artifactLoading",
        "graphPlanning",
        "resultRendering",
    }.issubset(payload["stagesMs"])


def test_graph_cli_query_error_is_compact_and_timings_survive_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _graph()
    _write_graph_project(tmp_path, graph)
    runner = CliRunner()

    def fail_planning(*_args, **_kwargs):
        raise GraphPlanningError(
            "GRAPH_NO_SAFE_PLAN",
            "no safe plan exists\nserver-side planner stack",
            details={"rejectedCandidates": [{"path": "fact_a -> bridge_b"}]},
        )

    monkeypatch.setattr("wren.graph_cli.plan_graph_question", fail_planning)
    compact = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--question",
            "按叶子地区看事实A销售额",
            "--path",
            str(tmp_path),
            "--timings",
        ],
    )

    assert compact.exit_code == 1
    assert compact.stdout == ""
    assert "Error [GRAPH_NO_SAFE_PLAN]: no safe plan exists" in compact.stderr
    assert "server-side planner stack" not in compact.stderr
    assert "rejectedCandidates" not in compact.stderr
    payload = json.loads(compact.stderr.splitlines()[-1])
    assert payload["status"] == "failure"
    assert payload["failedStage"] == "graphPlanning"
    assert payload["errorCode"] == "GRAPH_NO_SAFE_PLAN"

    verbose = runner.invoke(
        app,
        [
            "graph",
            "query",
            "--question",
            "按叶子地区看事实A销售额",
            "--path",
            str(tmp_path),
            "--verbose-errors",
        ],
    )
    assert verbose.exit_code == 1
    assert "server-side planner stack" in verbose.stderr
    assert "rejectedCandidates" in verbose.stderr
