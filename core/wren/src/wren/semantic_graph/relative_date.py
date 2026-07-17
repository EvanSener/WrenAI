"""Relative date-window support for MaxCompute semantic-graph queries."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlglot import exp

from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.partition import normalize_graph_date_range

_MAX_RELATIVE_DAYS = 3660
_RELATIVE_DATE_PATTERNS = (
    re.compile(r"(?:最近|近|过去)\s*(\d{1,4})\s*(?:天|日)"),
    re.compile(r"\b(?:last|past)\s+(\d{1,4})\s+days?\b", re.IGNORECASE),
)


def extract_relative_date_window(question: str) -> dict[str, Any] | None:
    """Return a latest-partition-relative day window declared by a question."""

    values = {
        int(match.group(1))
        for pattern in _RELATIVE_DATE_PATTERNS
        for match in pattern.finditer(question)
    }
    if not values:
        return None
    if len(values) > 1:
        raise GraphPlanningError(
            "GRAPH_QUESTION_DATE_RANGE_AMBIGUOUS",
            "question contains multiple relative date windows",
            details={"relativeDays": sorted(values)},
        )
    days = values.pop()
    if not 1 <= days <= _MAX_RELATIVE_DAYS:
        raise GraphPlanningError(
            "GRAPH_QUESTION_RELATIVE_DATE_INVALID",
            f"relative day window must be between 1 and {_MAX_RELATIVE_DAYS}",
            details={"days": days},
        )
    return {"days": days, "anchor": "latest_partition"}


def date_range_from_latest_partition(
    relative_window: dict[str, Any], latest_partition: object
) -> dict[str, str]:
    """Turn ``N`` days ending at one latest partition into a closed range."""

    days = relative_window.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise GraphPlanningError(
            "GRAPH_QUESTION_RELATIVE_DATE_INVALID",
            "relative date window must contain a positive integer days value",
            details={"relativeDateRange": relative_window},
        )
    latest = str(latest_partition)
    normalized = normalize_graph_date_range(
        {"start": latest, "end": latest},
        path="latestPartition",
    )
    assert normalized is not None
    end = datetime.strptime(normalized["end"], "%Y%m%d")
    start = end - timedelta(days=days - 1)
    return {"start": start.strftime("%Y%m%d"), "end": normalized["end"]}


def relative_partition_table(semantic_graph: dict[str, Any], source_model: str) -> str:
    """Return the trusted physical table used to anchor a relative window."""

    project = semantic_graph.get("project") or {}
    if str(project.get("dataSource", "")).casefold() != "maxcompute":
        raise GraphPlanningError(
            "GRAPH_RELATIVE_DATE_UNSUPPORTED",
            "latest-partition-relative dates currently require MaxCompute",
            details={"dataSource": project.get("dataSource")},
        )
    node = next(
        (
            item
            for item in semantic_graph.get("nodes") or []
            if item.get("name") == source_model
        ),
        None,
    )
    if not isinstance(node, dict):
        raise GraphPlanningError(
            "GRAPH_RELATIVE_DATE_ANCHOR_INVALID",
            f"relative date anchor model '{source_model}' does not exist",
        )
    policy = node.get("partitionPolicy") or {}
    if policy.get("type") != "incremental":
        raise GraphPlanningError(
            "GRAPH_RELATIVE_DATE_ANCHOR_INVALID",
            f"relative date anchor '{source_model}' is not an incremental relation",
            details={"sourceModel": source_model, "partitionPolicy": policy},
        )
    relation = node.get("relation") or {}
    reference = relation.get("tableReference") or {}
    table = reference.get("table")
    if relation.get("type") != "table" or not isinstance(table, str) or not table:
        raise GraphPlanningError(
            "GRAPH_RELATIVE_DATE_ANCHOR_INVALID",
            f"relative date anchor '{source_model}' has no physical table",
            details={"sourceModel": source_model},
        )
    return ".".join(
        str(value)
        for value in (
            reference.get("catalog"),
            reference.get("schema"),
            table,
        )
        if value not in (None, "")
    )


def max_partition_probe_sql(table: str) -> str:
    """Build an injection-safe MaxCompute ``max_pt`` probe."""

    return exp.select(
        exp.alias_(
            exp.Anonymous(
                this="max_pt",
                expressions=[exp.Literal.string(table)],
            ),
            "max_ds",
            quoted=False,
        )
    ).sql(dialect="hive")
