from __future__ import annotations

import builtins
import sys
import types

import pandas as pd
import pytest
from pydantic import SecretStr

from wren.connector.maxcompute import (
    MaxComputeConnector,
    _apply_latest_partition_filter,
)
from wren.maxcompute_partition import (
    MaxComputePartitionPolicy,
    MaxComputePartitionRegistry,
)
from wren.model import MaxComputeConnectionInfo
from wren.model.error import ErrorCode, ErrorPhase, WrenError

pytestmark = pytest.mark.unit


class _FakeReader:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def to_pandas(self) -> pd.DataFrame:
        return self.df


class _FakeInstance:
    def __init__(self, connection, df: pd.DataFrame):
        self.connection = connection
        self.df = df
        self.stopped = False

    def wait_for_success(self, timeout=None):
        self.connection.wait_for_success_calls.append({"timeout": timeout})
        if self.connection.next_wait_error is not None:
            raise self.connection.next_wait_error

    def open_reader(self, **kwargs):
        self.connection.open_reader_calls.append(kwargs)
        return _FakeReader(self.df)

    def stop(self):
        self.stopped = True
        self.connection.stop_calls.append(True)


@pytest.fixture
def fake_odps(monkeypatch):
    connections = []
    options = types.SimpleNamespace(
        tunnel=types.SimpleNamespace(
            use_instance_tunnel=None,
            limit_instance_tunnel=None,
        ),
        enable_schema=False,
        quota_name=None,
    )

    class ODPS:
        next_df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
        next_error: Exception | None = None

        def __init__(self, access_id, access_key, **kwargs):
            self.access_id = access_id
            self.access_key = access_key
            self.kwargs = kwargs
            self.execute_calls = []
            self.wait_for_success_calls = []
            self.open_reader_calls = []
            self.stop_calls = []
            self.closed = False
            self.next_wait_error = None
            connections.append(self)

        def execute_sql(self, sql, hints=None, quota_name=None, async_=False):
            self.execute_calls.append(
                {
                    "sql": sql,
                    "hints": hints,
                    "quota_name": quota_name,
                    "async_": async_,
                }
            )
            if self.next_error is not None:
                raise self.next_error
            return _FakeInstance(self, self.next_df)

        def close(self):
            self.closed = True

    odps_module = types.ModuleType("odps")
    odps_module.ODPS = ODPS
    odps_module.options = options
    monkeypatch.setitem(sys.modules, "odps", odps_module)

    return types.SimpleNamespace(ODPS=ODPS, options=options, connections=connections)


def _info(**overrides) -> MaxComputeConnectionInfo:
    data = {
        "access_id": SecretStr("ak-id"),
        "access_key": SecretStr("ak-secret"),
        "project": "wren_project",
        "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
        "schema": "analytics",
        "tunnel_endpoint": "https://dt.cn-shanghai.maxcompute.aliyun.com",
        "quota_name": "quota-a",
        "use_instance_tunnel": True,
        "limit_instance_tunnel": False,
        "hints": {"odps.sql.reducer.instances": "4"},
        "query_timeout_seconds": 180,
        "max_rows": 10_000,
        "enforce_read_only": True,
    }
    data.update(overrides)
    return MaxComputeConnectionInfo(**data)


def test_connector_builds_odps_client_and_options(fake_odps) -> None:
    MaxComputeConnector(_info())

    conn = fake_odps.connections[-1]
    assert conn.access_id == "ak-id"
    assert conn.access_key == "ak-secret"
    assert conn.kwargs == {
        "project": "wren_project",
        "endpoint": "https://service.cn-shanghai.maxcompute.aliyun.com/api",
        "schema": "analytics",
        "tunnel_endpoint": "https://dt.cn-shanghai.maxcompute.aliyun.com",
        "quota_name": "quota-a",
    }
    assert fake_odps.options.enable_schema is False
    assert fake_odps.options.quota_name is None
    assert fake_odps.options.tunnel.use_instance_tunnel is None
    assert fake_odps.options.tunnel.limit_instance_tunnel is None


def test_connector_import_error_points_to_maxcompute_extra(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "odps":
            raise ImportError("No module named 'odps'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(WrenError) as exc:
        MaxComputeConnector(_info())

    assert exc.value.error_code == ErrorCode.NOT_IMPLEMENTED
    assert "pip install 'wrenai[maxcompute]'" in str(exc.value)


def test_query_executes_with_hints_and_returns_arrow(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    table = connector.query("SELECT id, name FROM orders", limit=10)

    conn = fake_odps.connections[-1]
    assert conn.execute_calls[-1] == {
        "sql": "SELECT * FROM (SELECT id, name FROM orders WHERE orders.ds = MAX_PT('orders')) AS _wren_sub LIMIT 10",
        "hints": {
            "odps.sql.reducer.instances": "4",
            "odps.sql.allow.namespace.schema": "true",
            "odps.namespace.schema": "true",
            "odps.default.schema": "analytics",
        },
        "quota_name": "quota-a",
        "async_": True,
    }
    assert conn.wait_for_success_calls[-1] == {"timeout": 180}
    assert conn.open_reader_calls[-1] == {"tunnel": True, "limit": False}
    assert fake_odps.options.tunnel.use_instance_tunnel is None
    assert fake_odps.options.tunnel.limit_instance_tunnel is None
    assert table.column("id").to_pylist() == [1, 2]
    assert table.column("name").to_pylist() == ["a", "b"]


def test_connector_does_not_blindly_filter_managed_unpartitioned_model(
    fake_odps,
) -> None:
    registry = MaxComputePartitionRegistry(
        [
            MaxComputePartitionPolicy(
                model="source_view",
                physical_table="source_view",
                partition_type="unpartitioned",
                column=None,
                default=None,
                declared=True,
            )
        ]
    )
    connector = MaxComputeConnector(_info(), partition_registry=registry)

    connector.query("SELECT id FROM source_view", limit=1)

    statement = fake_odps.connections[-1].execute_calls[-1]["sql"]
    assert statement == (
        "SELECT * FROM (SELECT id FROM source_view) AS _wren_sub LIMIT 1"
    )
    assert "max_pt" not in statement.casefold()
    assert ".ds" not in statement.casefold()


def test_latest_partition_filter_preserves_explicit_ds() -> None:
    assert (
        _apply_latest_partition_filter(
            "SELECT id FROM orders o WHERE o.ds = '20260705'"
        )
        == "SELECT id FROM orders o WHERE o.ds = '20260705'"
    )


def test_latest_partition_filter_adds_each_join_table() -> None:
    assert (
        _apply_latest_partition_filter("SELECT * FROM a JOIN b ON a.id = b.id")
        == "SELECT * FROM a JOIN b ON a.id = b.id WHERE a.ds = MAX_PT('a') AND b.ds = MAX_PT('b')"
    )


def test_latest_partition_filter_rewrites_physical_table_inside_cte_only() -> None:
    assert (
        _apply_latest_partition_filter(
            "WITH c AS (SELECT * FROM orders) SELECT * FROM c"
        )
        == "WITH c AS (SELECT * FROM orders WHERE orders.ds = MAX_PT('orders')) SELECT * FROM c"
    )


def test_query_without_limit_strips_trailing_semicolon(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    connector.query("SELECT 1;")

    conn = fake_odps.connections[-1]
    assert (
        conn.execute_calls[-1]["sql"]
        == "SELECT * FROM (SELECT 1) AS _wren_sub LIMIT 10000"
    )


def test_query_can_disable_default_max_rows(fake_odps) -> None:
    connector = MaxComputeConnector(_info(max_rows=None))

    connector.query("SELECT 1;")

    conn = fake_odps.connections[-1]
    assert conn.execute_calls[-1]["sql"] == "SELECT 1"


def test_query_caps_requested_limit_to_max_rows(fake_odps) -> None:
    connector = MaxComputeConnector(_info(max_rows=5))

    connector.query("SELECT 1", limit=100)

    conn = fake_odps.connections[-1]
    assert (
        conn.execute_calls[-1]["sql"] == "SELECT * FROM (SELECT 1) AS _wren_sub LIMIT 5"
    )


def test_dry_run_wraps_with_limit_zero(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    connector.dry_run("SELECT 1;")

    conn = fake_odps.connections[-1]
    assert conn.execute_calls[-1] == {
        "sql": "SELECT * FROM (SELECT 1) AS _wren_sub LIMIT 0",
        "hints": {
            "odps.sql.reducer.instances": "4",
            "odps.sql.allow.namespace.schema": "true",
            "odps.namespace.schema": "true",
            "odps.default.schema": "analytics",
        },
        "quota_name": "quota-a",
        "async_": True,
    }


def test_query_wraps_driver_errors(fake_odps) -> None:
    fake_odps.ODPS.next_error = RuntimeError("boom")
    connector = MaxComputeConnector(_info())

    with pytest.raises(WrenError) as exc:
        connector.query("SELECT bad")

    assert exc.value.error_code == ErrorCode.INVALID_SQL
    assert exc.value.phase == ErrorPhase.SQL_EXECUTION


def test_dry_run_wraps_driver_errors(fake_odps) -> None:
    fake_odps.ODPS.next_error = RuntimeError("boom")
    connector = MaxComputeConnector(_info())

    with pytest.raises(WrenError) as exc:
        connector.dry_run("SELECT bad")

    assert exc.value.error_code == ErrorCode.INVALID_SQL
    assert exc.value.phase == ErrorPhase.SQL_DRY_RUN


def test_query_blocks_non_select_by_default(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    with pytest.raises(WrenError) as exc:
        connector.query("INSERT OVERWRITE TABLE t SELECT 1")

    assert exc.value.error_code == ErrorCode.INVALID_SQL
    assert exc.value.phase == ErrorPhase.SQL_POLICY_CHECK
    assert fake_odps.connections[-1].execute_calls == []


def test_query_blocks_multiple_statements_by_default(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    with pytest.raises(WrenError) as exc:
        connector.query("SELECT 1; DROP TABLE t")

    assert exc.value.error_code == ErrorCode.INVALID_SQL
    assert exc.value.phase == ErrorPhase.SQL_POLICY_CHECK
    assert fake_odps.connections[-1].execute_calls == []


def test_query_can_disable_read_only_guard(fake_odps) -> None:
    connector = MaxComputeConnector(_info(enforce_read_only=False, max_rows=None))

    connector.query("INSERT OVERWRITE TABLE t SELECT 1")

    conn = fake_odps.connections[-1]
    assert conn.execute_calls[-1]["sql"] == "INSERT OVERWRITE TABLE t SELECT 1"


def test_query_timeout_stops_instance(fake_odps) -> None:
    connector = MaxComputeConnector(_info(query_timeout_seconds=3))
    conn = fake_odps.connections[-1]
    conn.next_wait_error = TimeoutError("timeout")

    with pytest.raises(WrenError) as exc:
        connector.query("SELECT 1")

    assert exc.value.error_code == ErrorCode.DATABASE_TIMEOUT
    assert exc.value.phase == ErrorPhase.SQL_EXECUTION
    assert conn.wait_for_success_calls[-1] == {"timeout": 3}
    assert conn.stop_calls == [True]


def test_query_driver_timeout_class_stops_instance(fake_odps) -> None:
    class RequestsConnectTimeout(Exception):
        pass

    connector = MaxComputeConnector(_info(query_timeout_seconds=3))
    conn = fake_odps.connections[-1]
    conn.next_wait_error = RequestsConnectTimeout("timeout")

    with pytest.raises(WrenError) as exc:
        connector.query("SELECT 1")

    assert exc.value.error_code == ErrorCode.DATABASE_TIMEOUT
    assert exc.value.phase == ErrorPhase.SQL_EXECUTION
    assert conn.stop_calls == [True]


def test_close_delegates_when_available(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    connector.close()

    conn = fake_odps.connections[-1]
    assert conn.closed is True
    assert connector.connection is None
