"""Schema drift planning, staging, and atomic application coverage."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from wren.schema_sync import (
    SchemaSyncExecution,
    SchemaSyncIssue,
    SchemaSyncPlan,
    apply_candidate_project,
    diff_model_schema,
    execute_schema_sync,
    plan_schema_sync,
    watch_schema_sync,
)
from wren.table_scaffold import IntrospectedColumn, IntrospectedTable


def _write_project(tmp_path: Path, *, with_cube: bool = False) -> Path:
    (tmp_path / "wren_project.yml").write_text(
        "schema_version: 5\n"
        "name: schema_sync_test\n"
        "catalog: wren\n"
        "schema: public\n"
        "data_source: maxcompute\n",
        encoding="utf-8",
    )
    (tmp_path / "relationships.yml").write_text("relationships: []\n", encoding="utf-8")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "knowledge.yml").write_text("schema_version: 1\n", encoding="utf-8")
    model_dir = tmp_path / "models" / "fact_ads"
    model_dir.mkdir(parents=True)
    (model_dir / "metadata.yml").write_text(
        "name: fact_ads\n"
        "properties:\n"
        "  description: 人工维护的广告事实表。\n"
        "table_reference:\n"
        "  table: fact_ads\n"
        "  description: 旧源注释\n"
        "columns:\n"
        "  - name: id\n"
        "    type: STRING\n"
        "    not_null: true\n"
        "    properties:\n"
        "      description: 人工维护的主键。\n"
        "      is_row_unique_id: true\n"
        "  - name: amount\n"
        "    type: DECIMAL(18,2)\n"
        "    properties:\n"
        "      description: 人工维护的金额。\n"
        "  - name: legacy_field\n"
        "    type: STRING\n"
        "    properties:\n"
        "      description: 可能被物理表删除的字段。\n"
        "  - name: amount_band\n"
        "    type: STRING\n"
        "    is_calculated: true\n"
        "    expression: CASE WHEN amount >= 100 THEN 'HIGH' ELSE 'LOW' END\n"
        "    properties:\n"
        "      description: 金额分层。\n"
        "primary_key: id\n"
        "cached: false\n",
        encoding="utf-8",
    )
    if with_cube:
        dimension_dir = tmp_path / "dimensions" / "legacy_field"
        dimension_dir.mkdir(parents=True)
        (dimension_dir / "metadata.yml").write_text(
            "name: legacy_field\n"
            "expression: legacy_field\n"
            "type: STRING\n"
            "description: 旧字段维度。\n",
            encoding="utf-8",
        )
        cube_dir = tmp_path / "cubes" / "ads"
        cube_dir.mkdir(parents=True)
        (cube_dir / "metadata.yml").write_text(
            "name: ads\n"
            "base_object: fact_ads\n"
            "description: 广告分析。\n"
            "measures:\n"
            "  - name: row_count\n"
            "    expression: COUNT(*)\n"
            "    type: BIGINT\n"
            "    description: 行数。\n"
            "dimensions: [legacy_field]\n",
            encoding="utf-8",
        )
    return tmp_path


def _table(*columns: IntrospectedColumn, comment: str = "最新源注释"):
    return IntrospectedTable(
        physical_table="fact_ads",
        columns=list(columns),
        comment=comment,
    )


def _introspector(table: IntrospectedTable):
    def introspect(
        table_name: str,
        *,
        table_schema: str | None = None,
        table_catalog: str | None = None,
    ) -> IntrospectedTable:
        assert table_name == "fact_ads"
        assert table_schema is None
        assert table_catalog is None
        return table

    return introspect


def test_diff_marks_additions_safe_and_structural_removals_breaking() -> None:
    existing = {
        "table_reference": {"table": "fact_ads", "description": "old"},
        "columns": [
            {"name": "id", "type": "STRING"},
            {"name": "removed", "type": "STRING"},
            {
                "name": "derived",
                "type": "STRING",
                "is_calculated": True,
                "expression": "id",
            },
        ],
    }
    candidate = {
        "table_reference": {"table": "fact_ads", "description": "new"},
        "columns": [
            {
                "name": "id",
                "type": "BIGINT",
                "properties": {"is_partition": True},
            },
            {"name": "added", "type": "STRING"},
            {
                "name": "derived",
                "type": "STRING",
                "is_calculated": True,
                "expression": "id",
            },
        ],
    }

    changes = diff_model_schema(existing, candidate)
    by_kind = {change.kind: change for change in changes}

    assert by_kind["column_added"].column == "added"
    assert by_kind["column_added"].breaking is False
    assert by_kind["column_removed"].column == "removed"
    assert by_kind["column_removed"].breaking is True
    assert by_kind["column_type_changed"].column == "id"
    assert by_kind["column_type_changed"].breaking is True
    assert by_kind["partition_changed"].column == "id"
    assert by_kind["partition_changed"].breaking is True
    assert "derived" not in {change.column for change in changes}
    assert by_kind["table_comment_changed"].breaking is False


def test_check_only_builds_candidate_without_writing(tmp_path: Path) -> None:
    _write_project(tmp_path)
    metadata_path = tmp_path / "models" / "fact_ads" / "metadata.yml"
    before = metadata_path.read_text(encoding="utf-8")
    live = _table(
        IntrospectedColumn("id", "STRING", "源主键"),
        IntrospectedColumn("amount", "DECIMAL(18,2)", "源金额"),
        IntrospectedColumn("legacy_field", "STRING", "旧字段"),
        IntrospectedColumn("new_field", "BIGINT", "新增字段"),
    )

    result = execute_schema_sync(
        tmp_path,
        _introspector(live),
        apply_additive=False,
        reindex_memory=False,
    )

    assert result.applied is False
    assert result.blocked is False
    assert result.candidate is not None
    assert result.candidate.errors == ()
    assert result.candidate.manifest is not None
    assert metadata_path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "target" / "mdl.json").exists()


def test_apply_additive_preserves_semantics_and_rebuilds_target(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)
    live = _table(
        IntrospectedColumn("id", "STRING", "源主键"),
        IntrospectedColumn("amount", "DECIMAL(18,2)", "源金额"),
        IntrospectedColumn("legacy_field", "STRING", "旧字段"),
        IntrospectedColumn("new_field", "BIGINT", "新增字段"),
    )

    result = execute_schema_sync(
        tmp_path,
        _introspector(live),
        apply_additive=True,
        reindex_memory=False,
    )

    assert result.applied is True
    metadata = yaml.safe_load(
        (tmp_path / "models" / "fact_ads" / "metadata.yml").read_text()
    )
    assert metadata["properties"]["description"] == "人工维护的广告事实表。"
    assert metadata["table_reference"]["description"] == "旧源注释"
    assert metadata["primary_key"] == "id"
    by_name = {column["name"]: column for column in metadata["columns"]}
    assert by_name["id"]["not_null"] is True
    assert by_name["id"]["properties"]["description"] == "人工维护的主键。"
    assert by_name["new_field"]["type"] == "BIGINT"
    assert by_name["amount_band"]["is_calculated"] is True
    manifest = json.loads((tmp_path / "target" / "mdl.json").read_text())
    model = manifest["models"][0]
    assert any(column["name"] == "new_field" for column in model["columns"])
    assert any(column["name"] == "amount_band" for column in model["columns"])


def test_memory_reindex_failure_does_not_roll_back_valid_schema_apply(
    tmp_path: Path, monkeypatch
) -> None:
    _write_project(tmp_path)
    live = _table(
        IntrospectedColumn("id", "STRING"),
        IntrospectedColumn("amount", "DECIMAL(18,2)"),
        IntrospectedColumn("legacy_field", "STRING"),
        IntrospectedColumn("new_field", "STRING"),
    )
    import wren.schema_sync as schema_sync  # noqa: PLC0415

    def fail_memory(_project_path, _manifest):
        raise RuntimeError("memory backend unavailable")

    monkeypatch.setattr(schema_sync, "reindex_project_memory", fail_memory)

    result = execute_schema_sync(
        tmp_path,
        _introspector(live),
        apply_additive=True,
        reindex_memory=True,
    )

    assert result.applied is True
    assert result.memory_error == "memory backend unavailable"
    assert (tmp_path / "target" / "mdl.json").exists()


def test_breaking_removal_blocks_all_writes_and_reports_cube_impact(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, with_cube=True)
    metadata_path = tmp_path / "models" / "fact_ads" / "metadata.yml"
    before = metadata_path.read_text(encoding="utf-8")
    live = _table(
        IntrospectedColumn("id", "STRING"),
        IntrospectedColumn("amount", "DECIMAL(18,2)"),
    )

    result = execute_schema_sync(
        tmp_path,
        _introspector(live),
        apply_additive=True,
        reindex_memory=False,
    )

    assert result.applied is False
    assert result.blocked is True
    assert any(
        change.kind == "column_removed" and change.column == "legacy_field"
        for change in result.plan.breaking_changes
    )
    assert result.candidate is not None
    assert any(
        "CUBE_DIMENSION_FIELD_MISSING" in error for error in result.candidate.errors
    )
    assert metadata_path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "target" / "mdl.json").exists()


def test_ref_sql_models_are_skipped(tmp_path: Path) -> None:
    _write_project(tmp_path)
    sql_model = tmp_path / "models" / "sql_summary"
    sql_model.mkdir()
    (sql_model / "metadata.yml").write_text(
        "name: sql_summary\ncolumns: [{name: total, type: BIGINT}]\n",
        encoding="utf-8",
    )
    (sql_model / "ref_sql.sql").write_text(
        "SELECT COUNT(*) AS total FROM fact_ads", encoding="utf-8"
    )
    live = _table(
        IntrospectedColumn("id", "STRING"),
        IntrospectedColumn("amount", "DECIMAL(18,2)"),
        IntrospectedColumn("legacy_field", "STRING"),
    )

    plan = plan_schema_sync(tmp_path, _introspector(live))

    assert len(plan.models) == 1
    assert [(item.model, item.reason) for item in plan.skipped] == [
        ("sql_summary", "ref_sql model")
    ]


def test_apply_candidate_rolls_back_when_a_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    _write_project(tmp_path)
    live = _table(
        IntrospectedColumn("id", "STRING"),
        IntrospectedColumn("amount", "DECIMAL(18,2)"),
        IntrospectedColumn("legacy_field", "STRING"),
        IntrospectedColumn("new_field", "STRING"),
    )
    plan = plan_schema_sync(tmp_path, _introspector(live))
    from wren.schema_sync import build_candidate_project  # noqa: PLC0415

    candidate = build_candidate_project(tmp_path, plan)
    assert candidate.manifest is not None
    metadata_path = tmp_path / "models" / "fact_ads" / "metadata.yml"
    before = metadata_path.read_bytes()

    import wren.schema_sync as schema_sync  # noqa: PLC0415

    real_replace = schema_sync.os.replace
    calls = 0

    def fail_target(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated target replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(schema_sync.os, "replace", fail_target)

    try:
        apply_candidate_project(tmp_path, plan, candidate.manifest)
    except OSError as exc:
        assert "simulated" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected the simulated commit failure")

    assert metadata_path.read_bytes() == before
    assert not (tmp_path / "target" / "mdl.json").exists()


def test_watch_counts_applied_blocked_and_errors_without_real_sleep(
    tmp_path: Path,
) -> None:
    clean_plan = SchemaSyncPlan(project_path=tmp_path)
    applied = SchemaSyncExecution(plan=clean_plan, applied=True)
    blocked_plan = SchemaSyncPlan(project_path=tmp_path)
    blocked_plan.issues.append(
        # A remote failure is blocked but does not crash the watcher.
        SchemaSyncIssue("fact_ads", "fact_ads", "remote error")
    )
    blocked = SchemaSyncExecution(plan=blocked_plan)
    queue = iter([applied, blocked])

    state = watch_schema_sync(
        lambda: next(queue),
        max_polls=2,
        interval=1,
        sleep=lambda _seconds: None,
    )

    assert state.polls == 2
    assert state.applied == 1
    assert state.blocked == 1
    assert state.errors == 0
