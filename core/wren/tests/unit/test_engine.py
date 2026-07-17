"""Unit tests for WrenEngine — no database required.

transpile() and dry_plan() exercise the wren-core MDL planning + sqlglot
transpile path without connecting to any data source.
"""

from __future__ import annotations

import base64
import json

import orjson
import pytest

from wren import WrenEngine
from wren.config import WrenConfig
from wren.model.data_source import DataSource
from wren.model.error import ErrorCode, WrenError
from wren.security import ProjectSecurityPolicy

pytestmark = pytest.mark.unit

# Minimal manifest with a single model.  No real DB needed for planning.
_MANIFEST = {
    "catalog": "wren",
    "schema": "public",
    "models": [
        {
            "name": "orders",
            "tableReference": {"schema": "main", "table": "orders"},
            "columns": [
                {"name": "o_orderkey", "type": "integer"},
                {"name": "o_custkey", "type": "integer"},
                {"name": "o_orderstatus", "type": "varchar"},
                {
                    "name": "order_cust_key",
                    "type": "varchar",
                    "expression": "concat(cast(o_orderkey as varchar), '_', cast(o_custkey as varchar))",
                },
            ],
            "primaryKey": "o_orderkey",
        }
    ],
}
_MANIFEST_STR = base64.b64encode(orjson.dumps(_MANIFEST)).decode()


@pytest.fixture(scope="module")
def duckdb_engine(tmp_path_factory):
    """A WrenEngine pointed at a temporary DuckDB file (not queried by unit tests)."""
    db_dir = tmp_path_factory.mktemp("unit_duckdb")
    conn_info = {"url": str(db_dir), "format": "duckdb"}
    with WrenEngine(_MANIFEST_STR, DataSource.duckdb, conn_info, fallback=False) as e:
        yield e


@pytest.fixture(scope="module")
def pg_engine():
    """A WrenEngine configured for Postgres (no real connection opened for planning)."""
    conn_info = {
        "host": "localhost",
        "port": 5432,
        "database": "test",
        "user": "test",
        "password": "test",
    }
    with WrenEngine(_MANIFEST_STR, DataSource.postgres, conn_info, fallback=False) as e:
        yield e


# ------------------------------------------------------------------
# dry_plan (no DB access)
# ------------------------------------------------------------------


def test_dry_plan_returns_string(duckdb_engine: WrenEngine) -> None:
    sql = duckdb_engine.dry_plan('SELECT o_orderkey FROM "orders" LIMIT 1')
    assert isinstance(sql, str)
    assert len(sql) > 0


def test_dry_plan_postgres_dialect(pg_engine: WrenEngine) -> None:
    """dry_plan should produce Postgres-flavoured SQL (no backtick quoting, etc.)."""
    sql = pg_engine.dry_plan('SELECT o_orderkey FROM "orders" LIMIT 1')
    assert isinstance(sql, str)
    # sqlglot Postgres output uses double-quote identifiers, not backticks
    assert "`" not in sql


def test_dry_plan_calculated_field(duckdb_engine: WrenEngine) -> None:
    sql = duckdb_engine.dry_plan('SELECT order_cust_key FROM "orders" LIMIT 1')
    assert isinstance(sql, str)
    # The calculated column expression should be expanded in the SQL
    assert "concat" in sql.lower() or "||" in sql.lower()


def test_dry_plan_invalid_sql_raises(duckdb_engine: WrenEngine) -> None:
    with pytest.raises(WrenError):
        duckdb_engine.dry_plan("SELECT * FROM not_a_model_in_manifest")


# ------------------------------------------------------------------
# Context manager
# ------------------------------------------------------------------


def test_context_manager_closes_connector() -> None:
    conn_info = {"url": "/tmp", "format": "duckdb"}
    with WrenEngine(_MANIFEST_STR, DataSource.duckdb, conn_info, fallback=False) as e:
        assert e._connector is None  # connector is lazily initialized

    # After __exit__, internal state is cleaned up
    assert e._connector is None


# ------------------------------------------------------------------
# Strict mode (no DB access)
# ------------------------------------------------------------------

_STRICT_CONFIG = WrenConfig(strict_mode=True)
_BLACKLIST_CONFIG = WrenConfig(denied_functions=frozenset(["pg_read_file"]))


def test_strict_mode_blocks_unknown_table():
    conn_info = {"url": "/tmp", "format": "duckdb"}
    with WrenEngine(
        _MANIFEST_STR,
        DataSource.duckdb,
        conn_info,
        fallback=False,
        config=_STRICT_CONFIG,
    ) as engine:
        with pytest.raises(WrenError) as exc_info:
            engine.dry_plan("SELECT * FROM secret_table")
        assert exc_info.value.error_code == ErrorCode.MODEL_NOT_FOUND


def test_strict_mode_allows_mdl_table():
    conn_info = {"url": "/tmp", "format": "duckdb"}
    with WrenEngine(
        _MANIFEST_STR,
        DataSource.duckdb,
        conn_info,
        fallback=False,
        config=_STRICT_CONFIG,
    ) as engine:
        sql = engine.dry_plan('SELECT o_orderkey FROM "orders" LIMIT 1')
        assert isinstance(sql, str)
        assert len(sql) > 0


def test_strict_mode_blocks_denied_function():
    conn_info = {"url": "/tmp", "format": "duckdb"}
    with WrenEngine(
        _MANIFEST_STR,
        DataSource.duckdb,
        conn_info,
        fallback=False,
        config=_BLACKLIST_CONFIG,
    ) as engine:
        with pytest.raises(WrenError) as exc_info:
            engine.dry_plan("SELECT pg_read_file('/etc/passwd')")
        assert exc_info.value.error_code == ErrorCode.BLOCKED_FUNCTION


def test_non_strict_mode_allows_unknown_table(duckdb_engine: WrenEngine):
    # Default config (no strict mode) — non-MDL tables should not be blocked
    # by policy (may still fail during planning, but not with MODEL_NOT_FOUND)
    try:
        duckdb_engine.dry_plan("SELECT * FROM unknown_table")
    except WrenError as e:
        assert e.error_code != ErrorCode.MODEL_NOT_FOUND


def test_project_security_blocks_multi_statement_before_connector_and_audits(tmp_path):
    audit_log = tmp_path / "security.jsonl"
    policy = ProjectSecurityPolicy(enabled=True, audit_log=audit_log)
    config = WrenConfig(strict_mode=True, read_only=True)
    with WrenEngine(
        _MANIFEST_STR,
        DataSource.duckdb,
        {"url": str(tmp_path), "format": "duckdb"},
        fallback=False,
        config=config,
        security_policy=policy,
    ) as engine:
        with pytest.raises(WrenError) as exc_info:
            engine.dry_plan("SELECT * FROM orders; DROP TABLE orders")
        assert exc_info.value.error_code == ErrorCode.SECURITY_POLICY_VIOLATION
        assert engine._connector is None

    event = json.loads(audit_log.read_text(encoding="utf-8"))
    assert event["entrypoint"] == "sql.policy"
    assert event["decision"] == "deny"
    assert "DROP TABLE" not in audit_log.read_text(encoding="utf-8")


def test_project_security_keeps_sql_policy_when_audit_is_unavailable(tmp_path, caplog):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    policy = ProjectSecurityPolicy(
        enabled=True,
        audit_log=blocked_parent / "security.jsonl",
    )
    config = WrenConfig(strict_mode=True, read_only=True)

    with caplog.at_level("WARNING", logger="wren.security"):
        with WrenEngine(
            _MANIFEST_STR,
            DataSource.duckdb,
            {"url": str(tmp_path), "format": "duckdb"},
            fallback=False,
            config=config,
            security_policy=policy,
        ) as engine:
            planned = engine.dry_plan('SELECT o_orderkey FROM "orders" LIMIT 1')
            with pytest.raises(WrenError) as exc_info:
                engine.dry_plan("SELECT * FROM orders; DROP TABLE orders")

    assert isinstance(planned, str)
    assert exc_info.value.error_code == ErrorCode.SECURITY_POLICY_VIOLATION
    assert "policy enforcement remains active" in caplog.text
