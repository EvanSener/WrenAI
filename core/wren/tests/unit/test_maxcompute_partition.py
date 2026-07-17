"""Post-implementation coverage for model-aware MaxCompute partitions."""

from __future__ import annotations

import base64
import json

import pytest

from wren.maxcompute_partition import MaxComputePartitionRegistry
from wren.model.error import ErrorCode, WrenError


def _registry(*models: dict) -> MaxComputePartitionRegistry:
    encoded = base64.b64encode(json.dumps({"models": list(models)}).encode()).decode()
    return MaxComputePartitionRegistry.from_manifest_str(encoded)


def _model(name: str, partition_type: str) -> dict:
    columns = []
    if partition_type != "unpartitioned":
        properties = {"isPartition": True}
        if partition_type == "snapshot":
            properties["partitionDefault"] = "max_pt"
        columns.append({"name": "ds", "type": "STRING", "properties": properties})
    return {
        "name": name,
        "tableReference": {
            "table": name,
            "datePartitionType": partition_type,
        },
        "columns": columns,
    }


def test_snapshot_default_and_incremental_range_do_not_conflict() -> None:
    registry = _registry(
        _model("fact_daily", "incremental"),
        _model("dim_snapshot", "snapshot"),
    )

    rewritten = registry.rewrite_semantic_sql(
        "SELECT f.id FROM fact_daily f "
        "LEFT JOIN dim_snapshot d ON f.dim_id = d.id "
        "WHERE f.ds BETWEEN '20260101' AND '20260131'"
    )

    assert "f.ds BETWEEN '20260101' AND '20260131'" in rewritten
    assert "d.ds = MAX_PT('dim_snapshot')" in rewritten
    assert "ON f.dim_id = d.id AND d.ds = MAX_PT('dim_snapshot')" in rewritten
    assert "f.ds = MAX_PT('fact_daily')" not in rewritten


def test_partition_column_join_does_not_count_as_a_date_filter() -> None:
    registry = _registry(
        _model("left_snapshot", "snapshot"),
        _model("right_snapshot", "snapshot"),
    )

    rewritten = registry.rewrite_semantic_sql(
        "SELECT l.id FROM left_snapshot l "
        "LEFT JOIN right_snapshot r ON l.id = r.id AND l.ds = r.ds"
    )

    assert "l.ds = r.ds" in rewritten
    assert "l.ds = MAX_PT('left_snapshot')" in rewritten
    assert "r.ds = MAX_PT('right_snapshot')" in rewritten


def test_incremental_model_without_range_fails_closed() -> None:
    registry = _registry(_model("fact_daily", "incremental"))

    with pytest.raises(WrenError) as caught:
        registry.rewrite_semantic_sql("SELECT SUM(amount) FROM fact_daily")

    assert caught.value.error_code is ErrorCode.PARTITION_RANGE_REQUIRED
    assert caught.value.metadata["model"] == "fact_daily"


def test_snapshot_model_uses_latest_partition_by_default() -> None:
    registry = _registry(_model("dim_snapshot", "snapshot"))

    rewritten = registry.rewrite_semantic_sql("SELECT id FROM dim_snapshot")

    assert rewritten == (
        "SELECT id FROM dim_snapshot WHERE dim_snapshot.ds = MAX_PT('dim_snapshot')"
    )


def test_unpartitioned_model_never_receives_ds_filter() -> None:
    registry = _registry(_model("source_view", "unpartitioned"))

    assert registry.rewrite_semantic_sql("SELECT id FROM source_view") == (
        "SELECT id FROM source_view"
    )


def test_invalid_partition_date_is_rejected() -> None:
    registry = _registry(_model("fact_daily", "incremental"))

    with pytest.raises(WrenError) as caught:
        registry.rewrite_semantic_sql(
            "SELECT SUM(amount) FROM fact_daily WHERE ds = '2026-01-01'"
        )

    assert caught.value.error_code is ErrorCode.INVALID_PARTITION_FILTER
