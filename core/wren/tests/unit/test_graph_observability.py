"""Contract tests for compact Graph CLI diagnostics and timings."""

from __future__ import annotations

import json

import pytest

from wren.graph_observability import GraphQueryTimings, format_cli_error
from wren.model.error import ErrorCode, ErrorPhase, WrenError
from wren.semantic_graph import GraphPlanningError


def test_graph_planning_error_is_compact_by_default() -> None:
    exc = GraphPlanningError(
        "GRAPH_NO_SAFE_PLAN",
        "no safe plan exists\ninternal planner diagnostics",
        details={"candidatePaths": ["orders -> customers"]},
    )

    lines = format_cli_error(exc)

    assert lines == ["Error [GRAPH_NO_SAFE_PLAN]: no safe plan exists"]


def test_graph_planning_error_verbose_includes_full_diagnostics() -> None:
    exc = GraphPlanningError(
        "GRAPH_NO_SAFE_PLAN",
        "no safe plan exists\ninternal planner diagnostics",
        details={"candidatePaths": ["orders -> customers"]},
    )

    text = "\n".join(format_cli_error(exc, verbose=True))

    assert "internal planner diagnostics" in text
    assert '"candidatePaths"' in text


def test_wren_error_is_one_line_with_stable_code_and_phase() -> None:
    exc = WrenError(
        ErrorCode.INVALID_SQL,
        "invalid query\nparser implementation details",
        phase=ErrorPhase.SQL_PARSING,
    )

    lines = format_cli_error(exc)

    assert lines == ["Error [INVALID_SQL]: invalid query phase=SQL_PARSING"]


def test_unknown_error_does_not_expose_type_or_server_stack_by_default() -> None:
    exc = RuntimeError(
        "OTSAuthFailed: request failed\n"
        "Server stack trace:\n"
        "com.aliyun.odps.SomeInternalClass"
    )

    lines = format_cli_error(exc)

    assert lines == ["Error [UNEXPECTED_ERROR]: OTSAuthFailed: request failed"]
    assert "RuntimeError" not in lines[0]
    assert "Server stack trace" not in lines[0]


def test_unknown_error_verbose_includes_full_exception_text() -> None:
    exc = RuntimeError(
        "OTSAuthFailed: request failed\n"
        "Server stack trace:\n"
        "com.aliyun.odps.SomeInternalClass"
    )

    text = "\n".join(format_cli_error(exc, verbose=True))

    assert "Server stack trace" in text
    assert "com.aliyun.odps.SomeInternalClass" in text


def test_empty_unknown_error_does_not_expose_exception_type() -> None:
    lines = format_cli_error(RuntimeError())

    assert lines == ["Error [UNEXPECTED_ERROR]: unexpected error"]


def test_timings_success_emits_stable_single_line_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    timings = GraphQueryTimings(enabled=True)
    with timings.measure("graphPlanning"):
        pass

    timings.emit(status="success")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    payload = json.loads(captured.err)
    assert payload["schemaVersion"] == 1
    assert payload["kind"] == "GRAPH_QUERY_TIMINGS"
    assert payload["status"] == "success"
    assert set(payload["stagesMs"]) == {"graphPlanning"}
    assert payload["totalMs"] >= 0
    assert payload["overheadMs"] >= 0
    assert "failedStage" not in payload
    assert "errorCode" not in payload


def test_timings_failure_includes_failed_stage_and_stable_error_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    timings = GraphQueryTimings(enabled=True)
    with pytest.raises(GraphPlanningError):
        with timings.measure("graphPlanning"):
            raise GraphPlanningError("GRAPH_NO_SAFE_PLAN", "no safe plan exists")

    timings.emit(status="failure")

    captured = capsys.readouterr()
    assert len(captured.err.splitlines()) == 1
    payload = json.loads(captured.err)
    assert payload["status"] == "failure"
    assert payload["failedStage"] == "graphPlanning"
    assert payload["errorCode"] == "GRAPH_NO_SAFE_PLAN"
