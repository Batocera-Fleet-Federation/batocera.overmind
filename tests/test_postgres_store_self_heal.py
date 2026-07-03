"""Tests for PostgresMetadataStore schema-drift self-heal on device reads.

A column the deployed database is missing (schema drift after a deploy whose
migration has not landed) must not blank the fleet: the read should re-run
migrations once and retry instead of propagating the failure.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore, _is_missing_column_error


class UndefinedColumn(Exception):
    """Stand-in for psycopg.errors.UndefinedColumn (matched by class name)."""


class _SqlStateError(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _MigrationCursor:
    def __init__(self, fetchone_result=None):
        self.executed = []
        self.fetchone_result = fetchone_result

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return self.fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _MigrationConn:
    def __init__(self, fetchone_result=None):
        self.autocommit = False
        self.closed = False
        self.cursor_obj = _MigrationCursor(fetchone_result=fetchone_result)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_is_missing_column_error_by_sqlstate():
    assert _is_missing_column_error(_SqlStateError("42703")) is True
    assert _is_missing_column_error(_SqlStateError("23505")) is False


def test_is_missing_column_error_by_class_name():
    assert _is_missing_column_error(UndefinedColumn("column d.x does not exist")) is True
    assert _is_missing_column_error(ValueError("nope")) is False


def test_self_heal_retries_once_after_rerunning_migrations():
    store = PostgresMetadataStore()
    calls = {"query": 0, "schema": 0}

    def fake_ensure_schema():
        calls["schema"] += 1
    store.ensure_schema = fake_ensure_schema  # type: ignore[assignment]
    store._ready = True

    def flaky():
        calls["query"] += 1
        if calls["query"] == 1:
            raise UndefinedColumn("column d.rom_inventory_fingerprint does not exist")
        return ["device"]

    result = store._with_schema_self_heal("list_user_devices", flaky)

    assert result == ["device"]
    assert calls["query"] == 2          # original + one retry
    assert calls["schema"] == 1         # migrations re-run between attempts
    assert store._ready is False        # forced a fresh schema check


def test_self_heal_does_not_swallow_unrelated_errors():
    store = PostgresMetadataStore()
    store.ensure_schema = lambda: None  # type: ignore[assignment]

    def boom():
        raise ValueError("unrelated failure")

    with pytest.raises(ValueError):
        store._with_schema_self_heal("get_device", boom)


def test_self_heal_propagates_when_retry_still_fails():
    store = PostgresMetadataStore()
    store.ensure_schema = lambda: None  # type: ignore[assignment]
    attempts = {"n": 0}

    def always_missing():
        attempts["n"] += 1
        raise UndefinedColumn("still missing")

    with pytest.raises(UndefinedColumn):
        store._with_schema_self_heal("get_device", always_missing)
    assert attempts["n"] == 2  # tried once, retried once, then gave up


def test_no_transaction_migration_runs_with_autocommit(tmp_path):
    migration = tmp_path / "0017.concurrent_index.sql"
    migration.write_text(
        """
        -- depends: 0016.transfer_sessions
        -- no-transaction
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test ON some_table (id);
        -- rollback
        DROP INDEX CONCURRENTLY IF EXISTS idx_test;
        """,
        encoding="utf-8",
    )
    store = PostgresMetadataStore()
    check_conn = _MigrationConn(fetchone_result=None)
    apply_conn = _MigrationConn()
    mark_conn = _MigrationConn()
    connections = iter([check_conn, apply_conn, mark_conn])
    store._connect = lambda: next(connections)  # type: ignore[assignment]

    store._run_migrations(_MigrationConn(), [migration])

    assert apply_conn.autocommit is True
    assert apply_conn.closed is True
    applied_sql = apply_conn.cursor_obj.executed[0][0]
    assert "CREATE INDEX CONCURRENTLY" in applied_sql
    assert "DROP INDEX" not in applied_sql
    assert mark_conn.cursor_obj.executed[0][0].startswith(
        "INSERT INTO _overmind_migrations"
    )
