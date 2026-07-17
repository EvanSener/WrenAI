"""Plan and render MaxCompute date partitions for semantic graph relations."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlglot import exp

from wren.semantic_graph.model import GraphPlanningError

_DATE_LITERAL = re.compile(r"^\d{8}$")


def normalize_graph_date_range(raw: Any, *, path: str) -> dict[str, str] | None:
    """Normalize one closed ``yyyyMMdd`` range from a GraphQueryRequest."""

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GraphPlanningError(
            "GRAPH_PARTITION_DATE_RANGE_INVALID",
            f"{path} must be an object with start and end",
        )
    unknown = sorted(set(raw) - {"start", "end"})
    if unknown:
        raise GraphPlanningError(
            "GRAPH_PARTITION_DATE_RANGE_INVALID",
            f"{path} contains unsupported field(s): {', '.join(unknown)}",
            details={"path": path, "unknownFields": unknown},
        )
    values: dict[str, str] = {}
    for key in ("start", "end"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not _valid_date(value):
            raise GraphPlanningError(
                "GRAPH_PARTITION_DATE_INVALID",
                f"{path}.{key} must be a valid 8-digit yyyyMMdd date, "
                "for example '20260101'",
                details={"path": f"{path}.{key}", "value": value},
            )
        values[key] = value
    if values["start"] > values["end"]:
        raise GraphPlanningError(
            "GRAPH_PARTITION_DATE_RANGE_INVALID",
            f"{path}.start must not be later than {path}.end",
            details={"path": path, **values},
        )
    return values


def plan_relation_partitions(
    nodes: dict[str, dict[str, Any]],
    models: set[str] | list[str] | tuple[str, ...],
    *,
    date_range: dict[str, str] | None,
    source_model: str,
) -> dict[str, dict[str, Any]]:
    """Create deterministic partition decisions for every relation in a fact."""

    result: dict[str, dict[str, Any]] = {}
    for model in sorted(set(models)):
        node = nodes.get(model)
        if node is None:
            continue
        policy = node.get("partitionPolicy")
        if not isinstance(policy, dict):
            if date_range is not None and model == source_model:
                raise GraphPlanningError(
                    "GRAPH_PARTITION_POLICY_MISSING",
                    f"fact '{source_model}' received dateRange but has no managed "
                    "date partition policy",
                    details={"sourceModel": source_model, "dateRange": date_range},
                )
            continue
        partition_type = policy.get("type")
        if partition_type == "unpartitioned":
            if date_range is not None and model == source_model:
                raise GraphPlanningError(
                    "GRAPH_PARTITION_NOT_SUPPORTED",
                    f"fact '{source_model}' is unpartitioned and cannot apply dateRange",
                    details={"sourceModel": source_model, "dateRange": date_range},
                )
            result[model] = {
                **deepcopy(policy),
                "model": model,
                "mode": "none",
                "dateRange": None,
            }
            continue
        if partition_type == "snapshot":
            effective_range = date_range if model == source_model else None
            if effective_range is None:
                mode = "latest"
            elif effective_range["start"] == effective_range["end"]:
                mode = "single_day"
            else:
                raise GraphPlanningError(
                    "GRAPH_SNAPSHOT_RANGE_INVALID",
                    f"snapshot relation '{model}' only accepts one ds date or max_pt",
                    details={
                        "sourceModel": source_model,
                        "model": model,
                        "dateRange": effective_range,
                    },
                )
        elif partition_type == "incremental":
            if date_range is None:
                raise GraphPlanningError(
                    "GRAPH_PARTITION_RANGE_REQUIRED",
                    f"incremental relation '{model}' requires an explicit dateRange",
                    details={
                        "sourceModel": source_model,
                        "model": model,
                        "partitionType": partition_type,
                        "hint": (
                            "provide dateRange.start/end in yyyyMMdd, for example "
                            "20260101 through 20260131"
                        ),
                    },
                )
            mode = (
                "single_day"
                if date_range["start"] == date_range["end"]
                else "closed_range"
            )
        else:
            raise GraphPlanningError(
                "GRAPH_PARTITION_POLICY_INVALID",
                f"relation '{model}' has unsupported partition type '{partition_type}'",
                details={"model": model, "partitionPolicy": policy},
            )
        result[model] = {
            **deepcopy(policy),
            "model": model,
            "mode": mode,
            "dateRange": deepcopy(
                effective_range if partition_type == "snapshot" else date_range
            ),
        }
    return result


def render_partitioned_relation(
    relation_sql: str,
    node: dict[str, Any],
    partition_plan: dict[str, Any] | None,
    *,
    dialect: str | None,
) -> str:
    """Wrap a physical relation with its planned predicate.

    Filtering each relation in its own subquery keeps right-side snapshot
    predicates out of the outer ``WHERE`` and therefore preserves LEFT JOIN
    semantics for both direct and fanout plans.
    """

    if not partition_plan or partition_plan.get("mode") == "none":
        return relation_sql
    column = exp.column(str(partition_plan.get("column") or "ds"))
    mode = partition_plan.get("mode")
    date_range = partition_plan.get("dateRange") or {}
    if mode == "latest":
        predicate: exp.Expression = exp.EQ(
            this=column,
            expression=exp.Anonymous(
                this="max_pt",
                expressions=[exp.Literal.string(_physical_table_name(node))],
            ),
        )
    elif mode == "single_day":
        predicate = exp.EQ(
            this=column,
            expression=exp.Literal.string(str(date_range["start"])),
        )
    elif mode == "closed_range":
        predicate = exp.Between(
            this=column,
            low=exp.Literal.string(str(date_range["start"])),
            high=exp.Literal.string(str(date_range["end"])),
        )
    else:
        raise GraphPlanningError(
            "GRAPH_PARTITION_PLAN_INVALID",
            f"relation '{node.get('name')}' has unsupported partition mode '{mode}'",
        )
    return f"(SELECT * FROM {relation_sql} WHERE {predicate.sql(dialect=dialect)})"


def _physical_table_name(node: dict[str, Any]) -> str:
    relation = node.get("relation") or {}
    reference = relation.get("tableReference") or {}
    table = reference.get("table")
    if not isinstance(table, str) or not table:
        raise GraphPlanningError(
            "GRAPH_PARTITION_TABLE_REFERENCE_INVALID",
            f"partitioned relation '{node.get('name')}' has no physical table name",
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


def _valid_date(value: str) -> bool:
    if not _DATE_LITERAL.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True
