"""Regression coverage for project-level metric compilation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wren.context import build_json
from wren.metric_compiler import (
    MetricCompilationError,
    analyze_cube_metrics,
    compile_cube_metrics,
    load_metrics,
)


def _metric(name: str, expression: str, metric_type: str = "BIGINT") -> dict:
    return {
        "name": name,
        "expression": expression,
        "type": metric_type,
        "_source_file": f"{name}/metadata.yml",
    }


def _model(*fields: str) -> dict:
    return {
        "name": "fact_ads",
        "columns": [{"name": field, "type": "BIGINT"} for field in fields],
    }


def test_compile_expands_derived_metric_dependencies() -> None:
    metrics = [
        _metric("clicks_sum", "SUM(clicks)"),
        _metric("impressions_sum", "SUM(impressions)"),
        _metric(
            "ctr",
            "CAST(clicks_sum AS DOUBLE) / NULLIF(impressions_sum, 0)",
            "DOUBLE",
        ),
    ]
    cubes = [
        {
            "name": "ads",
            "base_object": "fact_ads",
            "measures": ["ctr"],
            "_source_file": "ads/metadata.yml",
        }
    ]

    compiled = compile_cube_metrics(
        cubes=cubes,
        metrics=metrics,
        models=[_model("clicks", "impressions")],
        views=[],
        data_source="maxcompute",
    )

    assert [measure["name"] for measure in compiled[0]["measures"]] == [
        "clicks_sum",
        "impressions_sum",
        "ctr",
    ]
    assert compiled[0]["measures"][0]["expression"] == "SUM(clicks)"


def test_compile_rejects_missing_atomic_field_with_dependency_path() -> None:
    metrics = [
        _metric("clicks_sum", "SUM(clicks)"),
        _metric("impressions_sum", "SUM(impressions)"),
        _metric("ctr", "clicks_sum / NULLIF(impressions_sum, 0)", "DOUBLE"),
    ]
    cubes = [
        {
            "name": "ads",
            "base_object": "fact_ads",
            "measures": ["ctr"],
            "_source_file": "ads/metadata.yml",
        }
    ]

    _, issues = analyze_cube_metrics(
        cubes=cubes,
        metrics=metrics,
        models=[_model("clicks")],
        views=[],
        data_source="postgres",
    )

    message = "\n".join(issue.message for issue in issues)
    assert "CUBE_METRIC_FIELD_MISSING" in message
    assert "impressions" in message
    assert "ctr -> impressions_sum -> impressions" in message


def test_compile_reads_explicit_view_output_aliases() -> None:
    compiled = compile_cube_metrics(
        cubes=[
            {
                "name": "ads",
                "base_object": "ads_view",
                "measures": ["clicks_sum"],
            }
        ],
        metrics=[_metric("clicks_sum", "SUM(clicks)")],
        models=[],
        views=[
            {
                "name": "ads_view",
                "statement": "SELECT SUM(raw_clicks) AS clicks FROM source_ads",
            }
        ],
        data_source="maxcompute",
    )

    assert compiled[0]["measures"][0]["name"] == "clicks_sum"


def test_compile_rejects_wildcard_view_when_fields_cannot_be_proven() -> None:
    with pytest.raises(MetricCompilationError, match="wildcard projections"):
        compile_cube_metrics(
            cubes=[
                {
                    "name": "ads",
                    "base_object": "ads_view",
                    "measures": ["clicks_sum"],
                }
            ],
            metrics=[_metric("clicks_sum", "SUM(clicks)")],
            models=[],
            views=[{"name": "ads_view", "statement": "SELECT * FROM source_ads"}],
            data_source="postgres",
        )


def test_count_star_metric_does_not_require_view_field_introspection() -> None:
    compiled = compile_cube_metrics(
        cubes=[
            {
                "name": "ads",
                "base_object": "ads_view",
                "measures": ["row_count"],
            }
        ],
        metrics=[_metric("row_count", "COUNT(*)")],
        models=[],
        views=[{"name": "ads_view", "statement": "SELECT * FROM source_ads"}],
        data_source="postgres",
    )

    assert compiled[0]["measures"][0]["name"] == "row_count"


def test_unused_global_metric_expression_is_still_parsed() -> None:
    with pytest.raises(MetricCompilationError, match="METRIC_EXPRESSION_INVALID"):
        compile_cube_metrics(
            cubes=[],
            metrics=[_metric("broken", "SUM(")],
            models=[],
            views=[],
            data_source="postgres",
        )


def test_repeated_inline_metric_must_move_to_global_catalog() -> None:
    cubes = [
        {
            "name": "campaign",
            "base_object": "fact_ads",
            "measures": [
                {
                    "name": "clicks_sum",
                    "expression": "SUM(clicks)",
                    "type": "BIGINT",
                }
            ],
            "_source_file": "campaign/metadata.yml",
        },
        {
            "name": "search_term",
            "base_object": "fact_ads",
            "measures": [
                {
                    "name": "clicks_sum",
                    "expression": "SUM(clicks)",
                    "type": "BIGINT",
                }
            ],
            "_source_file": "search_term/metadata.yml",
        },
    ]

    with pytest.raises(MetricCompilationError, match="CUBE_METRIC_REPEATED_INLINE"):
        compile_cube_metrics(
            cubes=cubes,
            metrics=[],
            models=[_model("clicks")],
            views=[],
            data_source="maxcompute",
        )


def test_load_metrics_uses_directory_per_metric(tmp_path: Path) -> None:
    metric_dir = tmp_path / "metrics" / "clicks_sum"
    metric_dir.mkdir(parents=True)
    (metric_dir / "metadata.yml").write_text(
        "name: clicks_sum\nexpression: SUM(clicks)\ntype: BIGINT\n",
        encoding="utf-8",
    )

    metrics = load_metrics(tmp_path)

    assert metrics == [
        {
            "name": "clicks_sum",
            "expression": "SUM(clicks)",
            "type": "BIGINT",
            "_source_file": "clicks_sum/metadata.yml",
        }
    ]


def test_build_json_compiles_metric_names_to_runtime_measure_objects(
    tmp_path: Path,
) -> None:
    (tmp_path / "wren_project.yml").write_text(
        "schema_version: 5\nname: ads\ndata_source: postgres\n",
        encoding="utf-8",
    )
    model_dir = tmp_path / "models" / "fact_ads"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.yml").write_text(
        "name: fact_ads\n"
        "table_reference: {table: fact_ads}\n"
        "columns:\n"
        "  - {name: clicks, type: BIGINT}\n",
        encoding="utf-8",
    )
    metric_dir = tmp_path / "metrics" / "clicks_sum"
    metric_dir.mkdir(parents=True)
    (metric_dir / "metadata.yml").write_text(
        "name: clicks_sum\nexpression: SUM(clicks)\ntype: BIGINT\nlabel: 点击量\n",
        encoding="utf-8",
    )
    cube_dir = tmp_path / "cubes" / "ads"
    cube_dir.mkdir(parents=True)
    (cube_dir / "metadata.yml").write_text(
        "name: ads\nbase_object: fact_ads\nmeasures: [clicks_sum]\n",
        encoding="utf-8",
    )

    measure = build_json(tmp_path)["cubes"][0]["measures"][0]

    assert measure == {
        "name": "clicks_sum",
        "expression": "SUM(clicks)",
        "type": "BIGINT",
        "label": "点击量",
    }
