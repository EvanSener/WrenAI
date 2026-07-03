from __future__ import annotations

from typing import Any

import pyarrow as pa
from loguru import logger

from wren.connector.base import ConnectorABC
from wren.model import MaxComputeConnectionInfo
from wren.model.error import DIALECT_SQL, ErrorCode, ErrorPhase, WrenError


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.rstrip().rstrip(";").rstrip()


def _wrap_with_limit(sql: str, limit: int | None) -> str:
    stripped = _strip_trailing_semicolon(sql)
    if limit is None:
        return stripped
    return f"SELECT * FROM ({stripped}) AS _wren_sub LIMIT {int(limit)}"


def _build_odps_kwargs(connection_info: MaxComputeConnectionInfo) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "project": connection_info.project,
        "endpoint": connection_info.endpoint,
    }
    if connection_info.schema_name:
        kwargs["schema"] = connection_info.schema_name
    if connection_info.tunnel_endpoint:
        kwargs["tunnel_endpoint"] = connection_info.tunnel_endpoint
    return kwargs


class MaxComputeConnector(ConnectorABC):
    def __init__(self, connection_info: MaxComputeConnectionInfo):
        try:
            from odps import ODPS, options  # noqa: PLC0415
        except ImportError as e:
            raise WrenError(
                ErrorCode.NOT_IMPLEMENTED,
                "Connector 'maxcompute' requires additional dependencies: "
                f"{e}. Install with: pip install 'wrenai[maxcompute]'",
            ) from e

        self.connection_info = connection_info
        options.enable_schema = bool(connection_info.schema_name)
        options.tunnel.use_instance_tunnel = connection_info.use_instance_tunnel
        options.tunnel.limit_instance_tunnel = connection_info.limit_instance_tunnel
        options.quota_name = connection_info.quota_name

        self.connection = ODPS(
            connection_info.access_id.get_secret_value(),
            connection_info.access_key.get_secret_value(),
            **_build_odps_kwargs(connection_info),
        )

    def _execute_sql(self, sql: str):
        return self.connection.execute_sql(
            sql,
            hints=self.connection_info.hints or None,
        )

    def query(self, sql: str, limit: int | None = None) -> pa.Table:
        statement = _wrap_with_limit(sql, limit)
        try:
            instance = self._execute_sql(statement)
            with instance.open_reader(
                tunnel=self.connection_info.use_instance_tunnel,
                limit=self.connection_info.limit_instance_tunnel,
            ) as reader:
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
        statement = _wrap_with_limit(sql, 0)
        try:
            self._execute_sql(statement)
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


def create_connector(connection_info) -> MaxComputeConnector:
    return MaxComputeConnector(connection_info)
