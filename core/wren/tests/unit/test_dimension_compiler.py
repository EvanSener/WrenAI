"""Regression coverage for project-level dimension compilation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wren.context import build_json
from wren.dimension_compiler import (
    DimensionCompilationError,
    compile_cube_dimensions,
    load_dimensions,
)


def _dimension(name: str, expression: str, dimension_type: str = "STRING") -> dict:
    return {
        "name": name,
        "expression": expression,
        "type": dimension_type,
        "_source_file": f"{name}/metadata.yml",
    }


def _model(*fields: str) -> dict:
    return {
        "name": "fact_ads",
        "columns": [{"name": field, "type": "STRING"} for field in fields],
    }


def _compile(cube: dict, dimensions: list[dict], *fields: str) -> list[dict]:
    return compile_cube_dimensions(
        cubes=[cube],
        dimensions=dimensions,
        models=[_model(*fields)],
        views=[],
        data_source="postgres",
    )


def test_compile_expands_direct_field_dimension() -> None:
    compiled = _compile(
        {
            "name": "campaign_ads",
            "base_object": "fact_ads",
            "dimensions": ["campaign"],
        },
        [
            {
                **_dimension("campaign", "cam_pk_code"),
                "label": "广告活动",
                "description": "广告活动业务主键。",
                "synonyms": ["活动"],
            }
        ],
        "cam_pk_code",
    )

    assert compiled[0]["dimensions"] == [
        {
            "name": "campaign",
            "expression": "cam_pk_code",
            "type": "STRING",
            "label": "广告活动",
            "description": "广告活动业务主键。",
            "synonyms": ["活动"],
        }
    ]


def test_compile_expands_case_when_derived_dimension() -> None:
    compiled = _compile(
        {
            "name": "campaign_ads",
            "base_object": "fact_ads",
            "dimensions": ["campaign_state"],
        },
        [
            _dimension(
                "campaign_state",
                "CASE WHEN state = 'enabled' THEN '投放中' ELSE '其他' END",
            )
        ],
        "state",
    )

    assert compiled[0]["dimensions"][0]["name"] == "campaign_state"
    assert "CASE WHEN state" in compiled[0]["dimensions"][0]["expression"]


def test_compile_expands_global_time_dimension() -> None:
    compiled = _compile(
        {
            "name": "campaign_ads",
            "base_object": "fact_ads",
            "time_dimensions": ["event_date"],
        },
        [_dimension("event_date", "event_date", "DATE")],
        "event_date",
    )

    assert compiled[0]["time_dimensions"] == [
        {"name": "event_date", "expression": "event_date", "type": "DATE"}
    ]


def test_compile_rejects_missing_atomic_field() -> None:
    with pytest.raises(
        DimensionCompilationError,
        match="CUBE_DIMENSION_FIELD_MISSING.*state",
    ):
        _compile(
            {
                "name": "campaign_ads",
                "base_object": "fact_ads",
                "dimensions": ["campaign_state"],
            },
            [
                _dimension(
                    "campaign_state",
                    "CASE WHEN state = 'enabled' THEN '投放中' ELSE '其他' END",
                )
            ],
            "cam_pk_code",
        )


def test_compile_rejects_repeated_inline_dimension() -> None:
    inline = {"name": "campaign", "expression": "cam_pk_code", "type": "STRING"}
    cubes = [
        {
            "name": "campaign_ads",
            "base_object": "fact_ads",
            "dimensions": [inline],
            "_source_file": "campaign_ads/metadata.yml",
        },
        {
            "name": "search_ads",
            "base_object": "fact_ads",
            "dimensions": [inline],
            "_source_file": "search_ads/metadata.yml",
        },
    ]

    with pytest.raises(
        DimensionCompilationError,
        match="CUBE_DIMENSION_REPEATED_INLINE",
    ):
        compile_cube_dimensions(
            cubes=cubes,
            dimensions=[],
            models=[_model("cam_pk_code")],
            views=[],
            data_source="postgres",
        )


def test_compile_rejects_unknown_dimension_reference() -> None:
    with pytest.raises(DimensionCompilationError, match="CUBE_DIMENSION_NOT_FOUND"):
        _compile(
            {
                "name": "campaign_ads",
                "base_object": "fact_ads",
                "dimensions": ["missing_dimension"],
            },
            [],
            "cam_pk_code",
        )


def test_compile_rejects_dimension_role_conflict() -> None:
    with pytest.raises(DimensionCompilationError, match="CUBE_DIMENSION_ROLE_CONFLICT"):
        _compile(
            {
                "name": "campaign_ads",
                "base_object": "fact_ads",
                "dimensions": ["event_date"],
                "time_dimensions": ["event_date"],
            },
            [_dimension("event_date", "event_date", "DATE")],
            "event_date",
        )


def test_compile_reads_explicit_view_output_aliases() -> None:
    compiled = compile_cube_dimensions(
        cubes=[
            {
                "name": "campaign_ads",
                "base_object": "campaign_view",
                "dimensions": ["campaign"],
            }
        ],
        dimensions=[_dimension("campaign", "cam_pk_code")],
        models=[],
        views=[
            {
                "name": "campaign_view",
                "statement": "SELECT raw_campaign AS cam_pk_code FROM raw_ads",
            }
        ],
        data_source="postgres",
    )

    assert compiled[0]["dimensions"][0]["name"] == "campaign"


def test_load_dimensions_uses_directory_per_dimension(tmp_path: Path) -> None:
    dimension_dir = tmp_path / "dimensions" / "campaign"
    dimension_dir.mkdir(parents=True)
    (dimension_dir / "metadata.yml").write_text(
        "name: campaign\nexpression: cam_pk_code\ntype: STRING\n",
        encoding="utf-8",
    )

    dimensions = load_dimensions(tmp_path)

    assert dimensions == [
        {
            "name": "campaign",
            "expression": "cam_pk_code",
            "type": "STRING",
            "_source_file": "campaign/metadata.yml",
        }
    ]


def test_build_json_compiles_dimension_names_to_runtime_members(
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
        "  - {name: cam_pk_code, type: STRING}\n",
        encoding="utf-8",
    )
    dimension_dir = tmp_path / "dimensions" / "campaign"
    dimension_dir.mkdir(parents=True)
    (dimension_dir / "metadata.yml").write_text(
        "name: campaign\n"
        "expression: cam_pk_code\n"
        "type: STRING\n"
        "label: 广告活动\n"
        "master_model: fact_ads\n",
        encoding="utf-8",
    )
    cube_dir = tmp_path / "cubes" / "campaign_ads"
    cube_dir.mkdir(parents=True)
    (cube_dir / "metadata.yml").write_text(
        "name: campaign_ads\n"
        "base_object: fact_ads\n"
        "measures: [{name: row_count, expression: 'COUNT(*)', type: BIGINT}]\n"
        "dimensions: [campaign]\n",
        encoding="utf-8",
    )

    manifest = build_json(tmp_path)

    assert manifest["cubes"][0]["dimensions"] == [
        {
            "name": "campaign",
            "expression": "cam_pk_code",
            "type": "STRING",
            "label": "广告活动",
        }
    ]
    assert "master_model" not in manifest["cubes"][0]["dimensions"][0]
    assert "dimensions" not in manifest
