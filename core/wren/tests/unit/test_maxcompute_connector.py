from __future__ import annotations

import builtins
import sys
import types

import pandas as pd
import pytest
from pydantic import SecretStr

from wren.connector.maxcompute import MaxComputeConnector
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

    def open_reader(self, **kwargs):
        self.connection.open_reader_calls.append(kwargs)
        return _FakeReader(self.df)


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
            self.open_reader_calls = []
            self.closed = False
            connections.append(self)

        def execute_sql(self, sql, hints=None):
            self.execute_calls.append({"sql": sql, "hints": hints})
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
    }
    assert fake_odps.options.enable_schema is True
    assert fake_odps.options.quota_name == "quota-a"
    assert fake_odps.options.tunnel.use_instance_tunnel is True
    assert fake_odps.options.tunnel.limit_instance_tunnel is False


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
        "sql": "SELECT * FROM (SELECT id, name FROM orders) AS _wren_sub LIMIT 10",
        "hints": {"odps.sql.reducer.instances": "4"},
    }
    assert conn.open_reader_calls[-1] == {"tunnel": True, "limit": False}
    assert table.column("id").to_pylist() == [1, 2]
    assert table.column("name").to_pylist() == ["a", "b"]


def test_query_without_limit_strips_trailing_semicolon(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    connector.query("SELECT 1;")

    conn = fake_odps.connections[-1]
    assert conn.execute_calls[-1]["sql"] == "SELECT 1"


def test_dry_run_wraps_with_limit_zero(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    connector.dry_run("SELECT 1;")

    conn = fake_odps.connections[-1]
    assert conn.execute_calls[-1] == {
        "sql": "SELECT * FROM (SELECT 1) AS _wren_sub LIMIT 0",
        "hints": {"odps.sql.reducer.instances": "4"},
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


def test_close_delegates_when_available(fake_odps) -> None:
    connector = MaxComputeConnector(_info())

    connector.close()

    conn = fake_odps.connections[-1]
    assert conn.closed is True
    assert connector.connection is None
