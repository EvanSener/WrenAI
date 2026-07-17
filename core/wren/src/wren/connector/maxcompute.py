from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any

import pyarrow as pa
from loguru import logger
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

from wren.connector.base import ConnectorABC
from wren.maxcompute_partition import MaxComputePartitionRegistry
from wren.model import MaxComputeConnectionInfo
from wren.model.error import DIALECT_SQL, ErrorCode, ErrorPhase, WrenError

_ODPS_OPTIONS_LOCK = threading.RLock()
_LATEST_PARTITION_COLUMN = "ds"


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.rstrip().rstrip(";").rstrip()


def _wrap_with_limit(sql: str, limit: int | None) -> str:
    stripped = _strip_trailing_semicolon(sql)
    if limit is None:
        return stripped
    return f"SELECT * FROM ({stripped}) AS _wren_sub LIMIT {int(limit)}"


def _effective_limit(
    requested: int | None,
    max_rows: int | None,
) -> int | None:
    if requested is None:
        return max_rows
    if max_rows is None:
        return requested
    return min(requested, max_rows)


def _ensure_read_only_select(sql: str) -> None:
    try:
        expressions = parse(sql, read="hive")
    except SqlglotError as e:
        raise WrenError(
            ErrorCode.INVALID_SQL,
            f"MaxCompute only accepts parseable read-only SELECT queries: {e}",
            phase=ErrorPhase.SQL_POLICY_CHECK,
            metadata={DIALECT_SQL: sql},
        ) from e

    if len(expressions) != 1:
        raise WrenError(
            ErrorCode.INVALID_SQL,
            "MaxCompute connector accepts exactly one SQL statement.",
            phase=ErrorPhase.SQL_POLICY_CHECK,
            metadata={DIALECT_SQL: sql},
        )

    expression = expressions[0]
    if not isinstance(expression, (exp.Select, exp.Union)):
        raise WrenError(
            ErrorCode.INVALID_SQL,
            "MaxCompute connector only accepts read-only SELECT queries.",
            phase=ErrorPhase.SQL_POLICY_CHECK,
            metadata={DIALECT_SQL: sql},
        )


def _collect_cte_names(expression: exp.Expression) -> set[str]:
    return {
        cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _scope_tables(select: exp.Select) -> list[exp.Table]:
    tables: list[exp.Table] = []
    from_ = select.args.get("from_")
    if from_:
        from_sources = [from_.this, *from_.expressions]
        tables.extend(
            source for source in from_sources if isinstance(source, exp.Table)
        )
    for join in select.args.get("joins") or []:
        source = join.this
        if isinstance(source, exp.Table):
            tables.append(source)
    return tables


def _table_identifier(table: exp.Table) -> str:
    parts = [part for part in (table.catalog, table.db, table.name) if part]
    return ".".join(parts)


def _partition_qualifier(table: exp.Table) -> str:
    return table.alias_or_name or table.name


def _partition_column_matches(
    column: exp.Column,
    table: exp.Table,
    *,
    scope_table_count: int,
    partition_column: str,
) -> bool:
    if column.name.lower() != partition_column.lower():
        return False
    qualifier = _partition_qualifier(table).lower()
    table_name = table.name.lower()
    column_table = (column.table or "").lower()
    if column_table:
        return column_table in {qualifier, table_name}
    return scope_table_count == 1


def _has_explicit_partition_filter(
    select: exp.Select,
    table: exp.Table,
    *,
    scope_table_count: int,
    partition_column: str,
) -> bool:
    predicates: list[exp.Expression] = []
    if where := select.args.get("where"):
        predicates.append(where.this)
    for join in select.args.get("joins") or []:
        if join_on := join.args.get("on"):
            predicates.append(join_on)

    for predicate in predicates:
        for node in predicate.walk():
            if not isinstance(node, (exp.EQ, exp.In, exp.Between)):
                continue
            for column in node.find_all(exp.Column):
                if not _partition_column_matches(
                    column,
                    table,
                    scope_table_count=scope_table_count,
                    partition_column=partition_column,
                ):
                    continue
                if isinstance(node, exp.EQ):
                    left = node.this
                    right = node.expression
                    other = right if column is left else left
                    if isinstance(other, exp.Column):
                        continue
                return True
    return False


def _latest_partition_predicate(
    table: exp.Table,
    *,
    partition_column: str,
) -> exp.EQ:
    return exp.EQ(
        this=exp.column(partition_column, table=_partition_qualifier(table)),
        expression=exp.Anonymous(
            this="max_pt",
            expressions=[exp.Literal.string(_table_identifier(table))],
        ),
    )


def _append_where(select: exp.Select, predicate: exp.Expression) -> None:
    where = select.args.get("where")
    if where:
        select.set("where", exp.Where(this=exp.and_(where.this, predicate)))
    else:
        select.set("where", exp.Where(this=predicate))


def _apply_latest_partition_filter(
    sql: str,
    *,
    skip_tables: frozenset[str] | set[str] | None = None,
) -> str:
    """Add ``ds = max_pt('<table>')`` for MaxCompute tables missing ds filters."""
    try:
        expressions = parse(sql, read="hive")
    except SqlglotError as e:
        raise WrenError(
            ErrorCode.INVALID_SQL,
            f"MaxCompute partition rewrite requires parseable SQL: {e}",
            phase=ErrorPhase.SQL_POLICY_CHECK,
            metadata={DIALECT_SQL: sql},
        ) from e

    if len(expressions) != 1:
        return sql

    expression = expressions[0]
    if not isinstance(expression, (exp.Select, exp.Union)):
        return sql

    cte_names = _collect_cte_names(expression)
    skipped = {name.casefold() for name in (skip_tables or ())}
    changed = False
    for select in expression.find_all(exp.Select):
        tables = [
            table
            for table in _scope_tables(select)
            if table.name and table.name.lower() not in cte_names
        ]
        if not tables:
            continue
        scope_table_count = len(tables)
        for table in tables:
            identifier = _table_identifier(table).casefold()
            if identifier in skipped or table.name.casefold() in skipped:
                continue
            if _has_explicit_partition_filter(
                select,
                table,
                scope_table_count=scope_table_count,
                partition_column=_LATEST_PARTITION_COLUMN,
            ):
                continue
            _append_where(
                select,
                _latest_partition_predicate(
                    table,
                    partition_column=_LATEST_PARTITION_COLUMN,
                ),
            )
            changed = True

    return expression.sql(dialect="hive") if changed else sql


def _build_odps_kwargs(connection_info: MaxComputeConnectionInfo) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "project": connection_info.project,
        "endpoint": connection_info.endpoint,
    }
    if connection_info.schema_name:
        kwargs["schema"] = connection_info.schema_name
    if connection_info.tunnel_endpoint:
        kwargs["tunnel_endpoint"] = connection_info.tunnel_endpoint
    if connection_info.quota_name:
        kwargs["quota_name"] = connection_info.quota_name
    return kwargs


def _query_hints(connection_info: MaxComputeConnectionInfo) -> dict[str, str] | None:
    hints = dict(connection_info.hints or {})
    if connection_info.schema_name:
        hints.setdefault("odps.sql.allow.namespace.schema", "true")
        hints.setdefault("odps.namespace.schema", "true")
        hints.setdefault("odps.default.schema", connection_info.schema_name)
    return hints or None


def _is_timeout_error(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or "Timeout" in type(error).__name__


class MaxComputeConnector(ConnectorABC):
    def __init__(
        self,
        connection_info: MaxComputeConnectionInfo,
        *,
        partition_registry: MaxComputePartitionRegistry | None = None,
    ):
        try:
            from odps import ODPS, options  # noqa: PLC0415
        except ImportError as e:
            raise WrenError(
                ErrorCode.NOT_IMPLEMENTED,
                "Connector 'maxcompute' requires additional dependencies: "
                f"{e}. Install with: pip install 'wrenai[maxcompute]'",
            ) from e

        self.connection_info = connection_info
        self.partition_registry = partition_registry
        self._options = options

        self.connection = ODPS(
            connection_info.access_id.get_secret_value(),
            connection_info.access_key.get_secret_value(),
            **_build_odps_kwargs(connection_info),
        )

    def _execute_sql(self, sql: str, *, phase: ErrorPhase):
        instance = self.connection.execute_sql(
            sql,
            hints=_query_hints(self.connection_info),
            quota_name=self.connection_info.quota_name,
            async_=True,
        )
        try:
            instance.wait_for_success(
                timeout=self.connection_info.query_timeout_seconds
            )
        except Exception as e:
            if not _is_timeout_error(e):
                raise
            self._stop_instance(instance)
            raise WrenError(
                ErrorCode.DATABASE_TIMEOUT,
                "MaxCompute query exceeded "
                f"{self.connection_info.query_timeout_seconds}s and was stopped.",
                phase=phase,
                metadata={DIALECT_SQL: sql},
            ) from e
        return instance

    def _stop_instance(self, instance) -> None:
        try:
            instance.stop()
        except Exception as e:
            logger.warning(f"Error stopping timed out MaxCompute instance: {e}")

    @contextmanager
    def _open_reader(self, instance):
        options = self._options
        with _ODPS_OPTIONS_LOCK:
            previous_use_instance_tunnel = getattr(
                options.tunnel, "use_instance_tunnel", None
            )
            previous_limit_instance_tunnel = getattr(
                options.tunnel, "limit_instance_tunnel", None
            )
            try:
                options.tunnel.use_instance_tunnel = (
                    self.connection_info.use_instance_tunnel
                )
                options.tunnel.limit_instance_tunnel = (
                    self.connection_info.limit_instance_tunnel
                )
                with instance.open_reader(
                    tunnel=self.connection_info.use_instance_tunnel,
                    limit=self.connection_info.limit_instance_tunnel,
                ) as reader:
                    yield reader
            finally:
                options.tunnel.use_instance_tunnel = previous_use_instance_tunnel
                options.tunnel.limit_instance_tunnel = previous_limit_instance_tunnel

    def query(self, sql: str, limit: int | None = None) -> pa.Table:
        if self.connection_info.enforce_read_only:
            _ensure_read_only_select(sql)
        sql = _apply_latest_partition_filter(
            sql,
            skip_tables=(
                self.partition_registry.managed_physical_tables
                if self.partition_registry is not None
                else None
            ),
        )
        statement = _wrap_with_limit(
            sql,
            _effective_limit(limit, self.connection_info.max_rows),
        )
        try:
            instance = self._execute_sql(statement, phase=ErrorPhase.SQL_EXECUTION)
            with self._open_reader(instance) as reader:
                df = reader.to_pandas()
            return pa.Table.from_pandas(df, preserve_index=False)
        except (WrenError, TimeoutError):
            raise
        except Exception as e:
            raise WrenError(
                ErrorCode.INVALID_SQL,
                str(e),
                phase=ErrorPhase.SQL_EXECUTION,
                metadata={DIALECT_SQL: sql},
            ) from e

    def dry_run(self, sql: str) -> None:
        if self.connection_info.enforce_read_only:
            _ensure_read_only_select(sql)
        sql = _apply_latest_partition_filter(
            sql,
            skip_tables=(
                self.partition_registry.managed_physical_tables
                if self.partition_registry is not None
                else None
            ),
        )
        statement = _wrap_with_limit(sql, 0)
        try:
            self._execute_sql(statement, phase=ErrorPhase.SQL_DRY_RUN)
        except (WrenError, TimeoutError):
            raise
        except Exception as e:
            raise WrenError(
                ErrorCode.INVALID_SQL,
                str(e),
                phase=ErrorPhase.SQL_DRY_RUN,
                metadata={DIALECT_SQL: sql},
            ) from e

    def close(self) -> None:
        if self.connection is None:
            return
        try:
            close = getattr(self.connection, "close", None)
            if close:
                close()
        except Exception as e:
            logger.warning(f"Error closing MaxCompute connection: {e}")
        finally:
            self.connection = None


def create_connector(
    connection_info,
    *,
    partition_registry: MaxComputePartitionRegistry | None = None,
) -> MaxComputeConnector:
    return MaxComputeConnector(
        connection_info,
        partition_registry=partition_registry,
    )
