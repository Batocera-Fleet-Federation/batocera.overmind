"""Regression guard for the /api/admin/sync-actions IndeterminateDatatype 500.

list_all_sync_actions built a shared WHERE clause whose search guard was a bare
``%s IS NULL``. With no search term that binds an untyped NULL and Postgres raises
``IndeterminateDatatype: could not determine data type of parameter $1``. The guard
must use the typed ``%s::text IS NULL`` form (as every other conditional-NULL guard
in postgres_store.py already does). The in-memory test path never exercises this SQL,
so we assert on the generated query string with a fake cursor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return (0,)  # count(*) result

    def fetchall(self):
        return []  # empty page


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def _run(monkeypatch, search):
    store = PostgresMetadataStore()
    cursor = _FakeCursor()
    monkeypatch.setattr(store, "_core_connection", lambda *a, **k: _FakeConn(cursor))
    result = store.list_all_sync_actions(search=search, limit=20, offset=0)
    return result, cursor.executed


def test_sync_actions_count_uses_typed_null_guard_without_search(monkeypatch):
    result, executed = _run(monkeypatch, None)
    assert result == {"actions": [], "total": 0}
    count_sql = next(sql for sql in executed if "count(*)" in sql)
    # The typed cast is what lets Postgres plan the query with an empty search term.
    assert "%s::text IS NULL" in count_sql
    assert "%s IS NULL" not in count_sql.replace("%s::text IS NULL", "")


def test_sync_actions_search_path_uses_same_typed_guard(monkeypatch):
    _result, executed = _run(monkeypatch, "chrono")
    count_sql = next(sql for sql in executed if "count(*)" in sql)
    assert "%s::text IS NULL" in count_sql
