"""Model-aware MaxCompute ``ds`` partition semantics.

The raw project MDL is the only place that still contains Wren's extended
``tableReference`` and column ``properties`` metadata.  Build this registry
before the Rust ManifestExtractor narrows the manifest, then use it both while
planning semantic SQL and while deciding which physical tables the connector
must not rewrite a second time.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

from wren.model.error import DIALECT_SQL, ErrorCode, ErrorPhase, WrenError

DATE_PARTITION_TYPES = frozenset({"snapshot", "incremental", "unpartitioned"})
_DATE_LITERAL = re.compile(r"^\d{8}$")
_COMPARISONS = (
    exp.EQ,
    exp.In,
    exp.Between,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.NEQ,
)


@dataclass(frozen=True)
class MaxComputePartitionPolicy:
    model: str
    physical_table: str
    partition_type: str
    column: str | None
    default: str | None
    declared: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.partition_type,
            "column": self.column,
            "default": self.default,
            "declared": self.declared,
        }


class MaxComputePartitionRegistry:
    """Partition policies indexed by semantic Model and physical table."""

    def __init__(self, policies: list[MaxComputePartitionPolicy]):
        self.policies = tuple(policies)
        self.by_model = {policy.model.casefold(): policy for policy in policies}
        self.by_physical: dict[str, MaxComputePartitionPolicy] = {}
        for policy in policies:
            full = policy.physical_table.casefold()
            self.by_physical[full] = policy
            self.by_physical.setdefault(full.rsplit(".", 1)[-1], policy)

    @classmethod
    def from_manifest_str(cls, manifest_str: str) -> MaxComputePartitionRegistry:
        try:
            manifest = json.loads(base64.b64decode(manifest_str))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise WrenError(
                ErrorCode.INVALID_MDL,
                f"cannot read MaxCompute partition metadata from MDL: {exc}",
                phase=ErrorPhase.MDL_EXTRACTION,
            ) from exc
        policies: list[MaxComputePartitionPolicy] = []
        for model in manifest.get("models") or []:
            if not isinstance(model, dict):
                continue
            validation_errors = validate_date_partition_model(model)
            if validation_errors:
                name = model.get("name") or "?"
                raise WrenError(
                    ErrorCode.INVALID_MDL,
                    f"invalid date partition policy for Model '{name}': "
                    + "; ".join(validation_errors),
                    phase=ErrorPhase.MDL_EXTRACTION,
                )
            policy = _policy_from_model(model)
            if policy is not None:
                policies.append(policy)
        return cls(policies)

    @property
    def managed_physical_tables(self) -> frozenset[str]:
        return frozenset(self.by_physical)

    def rewrite_semantic_sql(self, sql: str) -> str:
        """Validate/inject partition predicates against semantic Model names."""

        if not self.by_model:
            return sql
        try:
            expressions = parse(sql, read="hive")
        except SqlglotError as exc:
            raise WrenError(
                ErrorCode.INVALID_SQL,
                f"MaxCompute partition policy requires parseable SQL: {exc}",
                phase=ErrorPhase.SQL_POLICY_CHECK,
                metadata={DIALECT_SQL: sql},
            ) from exc
        if len(expressions) != 1:
            return sql
        expression = expressions[0]
        if not isinstance(expression, (exp.Select, exp.Union)):
            return sql

        cte_names = {
            cte.alias_or_name.casefold()
            for cte in expression.find_all(exp.CTE)
            if cte.alias_or_name
        }
        changed = False
        for select in expression.find_all(exp.Select):
            sources = [
                (table, join)
                for table, join in _scope_table_sources(select)
                if table.name and table.name.casefold() not in cte_names
            ]
            scope_table_count = len(sources)
            for table, join in sources:
                policy = self.by_model.get(table.name.casefold())
                if policy is None or policy.partition_type == "unpartitioned":
                    continue
                filters = _partition_filters(
                    select,
                    table,
                    scope_table_count=scope_table_count,
                    partition_column=policy.column or "ds",
                )
                if filters:
                    for node in filters:
                        _validate_filter(node, table, policy, sql)
                    continue
                if policy.partition_type == "incremental":
                    raise WrenError(
                        ErrorCode.PARTITION_RANGE_REQUIRED,
                        f"incremental Model '{policy.model}' requires an explicit "
                        "ds = 'yyyyMMdd' or ds BETWEEN 'yyyyMMdd' AND 'yyyyMMdd' "
                        "predicate; confirm the user's date range before execution",
                        phase=ErrorPhase.SQL_POLICY_CHECK,
                        metadata={
                            DIALECT_SQL: sql,
                            "model": policy.model,
                            "physicalTable": policy.physical_table,
                            "partitionType": policy.partition_type,
                            "partitionColumn": policy.column,
                        },
                    )
                predicate = _latest_partition_predicate(table, policy)
                _append_predicate(select, join, predicate)
                changed = True
        return expression.sql(dialect="hive") if changed else sql


def validate_date_partition_model(model: dict[str, Any]) -> list[str]:
    """Validate an explicitly declared ``date_partition_type`` contract."""

    table_ref = _table_reference(model)
    raw_type = _value(table_ref, "datePartitionType", "date_partition_type")
    if raw_type is None:
        return []
    if not isinstance(raw_type, str) or raw_type not in DATE_PARTITION_TYPES:
        return [
            "table_reference.date_partition_type must be one of: "
            + ", ".join(sorted(DATE_PARTITION_TYPES))
        ]
    partition_columns = _partition_columns(model)
    ds = next((item for item in partition_columns if item[0].casefold() == "ds"), None)
    if raw_type in {"snapshot", "incremental"} and ds is None:
        return [
            f"date partition type '{raw_type}' requires column 'ds' with "
            "properties.is_partition: true"
        ]
    if raw_type == "unpartitioned":
        if partition_columns:
            names = ", ".join(item[0] for item in partition_columns)
            return [
                "date partition type 'unpartitioned' cannot declare partition "
                f"columns ({names})"
            ]
        return []
    assert ds is not None
    default = ds[1]
    if raw_type == "snapshot" and default != "max_pt":
        return [
            "snapshot Model column 'ds' must declare "
            "properties.partition_default: max_pt"
        ]
    if raw_type == "incremental" and default is not None:
        return [
            "incremental Model column 'ds' must not declare partition_default; "
            "the query must provide its date range"
        ]
    return []


def partition_policy_for_model(model: dict[str, Any]) -> dict[str, Any] | None:
    """Return the public Graph policy for one raw Model definition."""

    policy = _policy_from_model(model)
    return policy.to_dict() if policy is not None else None


def _policy_from_model(model: dict[str, Any]) -> MaxComputePartitionPolicy | None:
    name = model.get("name")
    table_ref = _table_reference(model)
    table = _value(table_ref, "table")
    if not isinstance(name, str) or not name or not isinstance(table, str) or not table:
        return None
    raw_type = _value(table_ref, "datePartitionType", "date_partition_type")
    declared = isinstance(raw_type, str) and bool(raw_type)
    partition_columns = _partition_columns(model)
    ds = next((item for item in partition_columns if item[0].casefold() == "ds"), None)
    if declared:
        partition_type = str(raw_type)
    elif ds is not None and ds[1] == "max_pt":
        partition_type = "snapshot"
    else:
        return None
    physical = ".".join(
        str(value)
        for value in (
            _value(table_ref, "catalog"),
            _value(table_ref, "schema", "schemaName", "schema_name"),
            table,
        )
        if value not in (None, "")
    )
    return MaxComputePartitionPolicy(
        model=name,
        physical_table=physical,
        partition_type=partition_type,
        column=ds[0] if ds is not None else None,
        default=ds[1] if ds is not None else None,
        declared=declared,
    )


def _table_reference(model: dict[str, Any]) -> dict[str, Any]:
    value = model.get("tableReference") or model.get("table_reference") or {}
    return value if isinstance(value, dict) else {}


def _partition_columns(model: dict[str, Any]) -> list[tuple[str, str | None]]:
    result: list[tuple[str, str | None]] = []
    for column in model.get("columns") or []:
        if not isinstance(column, dict) or not isinstance(column.get("name"), str):
            continue
        properties = column.get("properties") or {}
        if not isinstance(properties, dict):
            continue
        is_partition = _value(properties, "isPartition", "is_partition")
        if is_partition is not True:
            continue
        default = _value(properties, "partitionDefault", "partition_default")
        result.append((column["name"], str(default) if default is not None else None))
    return result


def _value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _scope_table_sources(
    select: exp.Select,
) -> list[tuple[exp.Table, exp.Join | None]]:
    result: list[tuple[exp.Table, exp.Join | None]] = []
    from_ = select.args.get("from_")
    if from_:
        for source in (from_.this, *from_.expressions):
            if isinstance(source, exp.Table):
                result.append((source, None))
    for join in select.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            result.append((join.this, join))
    return result


def _partition_filters(
    select: exp.Select,
    table: exp.Table,
    *,
    scope_table_count: int,
    partition_column: str,
) -> list[exp.Expression]:
    predicates: list[exp.Expression] = []
    if where := select.args.get("where"):
        predicates.append(where.this)
    for join in select.args.get("joins") or []:
        if on := join.args.get("on"):
            predicates.append(on)
    result: list[exp.Expression] = []
    seen: set[int] = set()
    for predicate in predicates:
        for node in predicate.walk():
            if not isinstance(node, _COMPARISONS):
                continue
            # A relationship may align two partition columns (for example
            # ``fact.ds = dimension.ds``).  That is a join key, not a bounded
            # partition predicate, so it must not satisfy or invalidate either
            # model's own date policy.
            if (
                isinstance(node, exp.EQ)
                and isinstance(node.this, exp.Column)
                and isinstance(node.expression, exp.Column)
            ):
                continue
            if not any(
                _partition_column_matches(
                    column,
                    table,
                    scope_table_count=scope_table_count,
                    partition_column=partition_column,
                )
                for column in node.find_all(exp.Column)
            ):
                continue
            if id(node) not in seen:
                result.append(node)
                seen.add(id(node))
    return result


def _partition_column_matches(
    column: exp.Column,
    table: exp.Table,
    *,
    scope_table_count: int,
    partition_column: str,
) -> bool:
    if column.name.casefold() != partition_column.casefold():
        return False
    qualifier = (table.alias_or_name or table.name).casefold()
    column_table = (column.table or "").casefold()
    if column_table:
        return column_table in {qualifier, table.name.casefold()}
    return scope_table_count == 1


def _validate_filter(
    node: exp.Expression,
    table: exp.Table,
    policy: MaxComputePartitionPolicy,
    sql: str,
) -> None:
    if policy.partition_type == "snapshot":
        if not isinstance(node, exp.EQ) or not _valid_equality_value(
            node, table, policy
        ):
            _raise_invalid_filter(
                policy,
                sql,
                "snapshot tables only allow ds = max_pt('table') or ds = 'yyyyMMdd'",
            )
        return
    if isinstance(node, exp.EQ) and _valid_equality_value(node, table, policy):
        return
    if isinstance(node, exp.Between):
        if (
            _column_is_partition(node.this, table, policy)
            and _is_date_value(node.args.get("low"))
            and _is_date_value(node.args.get("high"))
        ):
            return
    if isinstance(node, exp.In):
        if (
            _column_is_partition(node.this, table, policy)
            and node.expressions
            and all(_is_date_value(value) for value in node.expressions)
        ):
            return
    _raise_invalid_filter(
        policy,
        sql,
        "incremental tables require ds = 'yyyyMMdd', ds = max_pt('table'), "
        "ds IN ('yyyyMMdd', ...) or ds BETWEEN 'yyyyMMdd' AND 'yyyyMMdd'",
    )


def _valid_equality_value(
    node: exp.EQ,
    table: exp.Table,
    policy: MaxComputePartitionPolicy,
) -> bool:
    left, right = node.this, node.expression
    if _column_is_partition(left, table, policy):
        return _is_date_value(right) or _is_max_pt(right)
    if _column_is_partition(right, table, policy):
        return _is_date_value(left) or _is_max_pt(left)
    return False


def _column_is_partition(
    value: exp.Expression | None,
    table: exp.Table,
    policy: MaxComputePartitionPolicy,
) -> bool:
    if not isinstance(value, exp.Column):
        return False
    if value.name.casefold() != (policy.column or "ds").casefold():
        return False
    if not value.table:
        return True
    return value.table.casefold() in {
        (table.alias_or_name or table.name).casefold(),
        table.name.casefold(),
    }


def _is_date_value(value: exp.Expression | None) -> bool:
    if not isinstance(value, exp.Literal):
        return False
    raw = str(value.this)
    if not _DATE_LITERAL.fullmatch(raw):
        return False
    try:
        datetime.strptime(raw, "%Y%m%d")
    except ValueError:
        return False
    return True


def _is_max_pt(value: exp.Expression | None) -> bool:
    return isinstance(value, exp.Anonymous) and value.name.casefold() == "max_pt"


def _raise_invalid_filter(
    policy: MaxComputePartitionPolicy,
    sql: str,
    hint: str,
) -> None:
    raise WrenError(
        ErrorCode.INVALID_PARTITION_FILTER,
        f"invalid ds filter for {policy.partition_type} Model "
        f"'{policy.model}': {hint}; date literals use 8-digit yyyyMMdd, "
        "for example '20260101'",
        phase=ErrorPhase.SQL_POLICY_CHECK,
        metadata={
            DIALECT_SQL: sql,
            "model": policy.model,
            "physicalTable": policy.physical_table,
            "partitionType": policy.partition_type,
            "partitionColumn": policy.column,
        },
    )


def _latest_partition_predicate(
    table: exp.Table, policy: MaxComputePartitionPolicy
) -> exp.EQ:
    return exp.EQ(
        this=exp.column(policy.column or "ds", table=table.alias_or_name or table.name),
        expression=exp.Anonymous(
            this="max_pt",
            expressions=[exp.Literal.string(policy.physical_table)],
        ),
    )


def _append_predicate(
    select: exp.Select,
    join: exp.Join | None,
    predicate: exp.Expression,
) -> None:
    if join is not None:
        current = join.args.get("on")
        join.set("on", exp.and_(current, predicate) if current else predicate)
        return
    where = select.args.get("where")
    if where:
        select.set("where", exp.Where(this=exp.and_(where.this, predicate)))
    else:
        select.set("where", exp.Where(this=predicate))
