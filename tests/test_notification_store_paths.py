"""Unit tests for Postgres-only notification/reachability code paths.

These cover two regressions fixed together:
- #4: load_admin_overview_state must include per-user notification_settings, or the
  digest delivery job filters every recipient out and no channel messages send.
- #10: the full-state mirror must not silently reset a Drone's public_resolvable
  when the snapshot carries no fresh probe data, which previously re-fired the
  "Drone became resolvable" notification on a loop.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore


class _FakeCursor:
    """Minimal cursor that returns canned rows keyed by SQL content."""

    def __init__(self, responses):
        self._responses = responses
        self._last = None
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = sql

    def fetchall(self):
        for needle, rows in self._responses:
            if needle in self._last:
                return rows
        return []


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def test_load_admin_overview_state_includes_notification_settings(monkeypatch):
    store = PostgresMetadataStore()
    cursor = _FakeCursor([
        # users join user_notification_settings: NULL settings -> defaults
        (
            "FROM users u",
            [(
                "user-1", "owner@example.com", "hash", True, True, "password",
                "owner", "Owner", None, datetime.utcnow(),
                None, None, None, None, None, None,
            )],
        ),
        ("user_notification_type_settings", [("user-1", "drone_reachability", True)]),
        ("FROM swarms", []),
        ("FROM swarm_memberships", []),
        ("FROM drones d", []),
    ])
    monkeypatch.setattr(store, "_core_connection", lambda ensure_schema=False: _FakeConn(cursor))

    state = store.load_admin_overview_state()
    user = state["users"]["user-1"]
    settings = user["notification_settings"]
    assert settings["notify_email"] is True  # default when NULL
    assert settings["notify_slack"] is False
    assert settings["types"]["drone_reachability"] is True


def test_mirror_device_details_preserves_reachability_without_probe_data():
    store = PostgresMetadataStore()
    cursor = _FakeCursor([])
    # Snapshot device WITHOUT probe data (no checked_at): the mirror passes NULL for the
    # probe-owned columns. A fresh INSERT must default public_resolvable to false (the column
    # is NOT NULL) while a conflicting UPDATE preserves the existing value via COALESCE on the
    # re-passed bind param — so the mirror never resets reachability and never NULL-violates.
    store._mirror_device_details(cursor, {"id": "drone-1", "public_reachability": {}})
    insert_sql, params = cursor.executed[0]
    assert "drone_network_state" in insert_sql
    assert "COALESCE(%s::boolean, false)" in insert_sql  # INSERT default avoids NOT NULL violation
    assert "COALESCE(%s::boolean, drone_network_state.public_resolvable)" in insert_sql  # UPDATE preserves
    # params order: drone_id, api_port, scheme, reachable_url, public_resolvable, public_ip,
    # checked_at, public_resolvable (re-passed for the ON CONFLICT clause)
    assert params[4] is None  # public_resolvable (VALUES) -> COALESCE(NULL, false) = false on insert
    assert params[5] is None  # public_ip
    assert params[6] is None  # checked_at
    assert params[7] is None  # public_resolvable (UPDATE) -> COALESCE(NULL, existing) preserves


def test_mirror_device_details_writes_fresh_probe_data():
    store = PostgresMetadataStore()
    cursor = _FakeCursor([])
    checked_at = datetime.utcnow()
    store._mirror_device_details(
        cursor,
        {
            "id": "drone-1",
            "public_reachability": {"resolvable": True, "public_ip": "8.8.8.8", "checked_at": checked_at},
        },
    )
    _, params = cursor.executed[0]
    assert params[4] is True       # public_resolvable
    assert params[5] == "8.8.8.8"  # public_ip
    assert params[6] is not None   # checked_at preserved


def test_expire_stale_device_actions_fails_claimed_actions(monkeypatch):
    store = PostgresMetadataStore()
    cursor = _FakeCursor([])
    cursor.rowcount = 3
    monkeypatch.setattr(store, "_core_connection", lambda ensure_schema=False: _FakeConn(cursor))

    count = store.expire_stale_device_actions(600)

    assert count == 3
    sql, params = cursor.executed[-1]
    assert "UPDATE drone_actions" in sql
    assert "status = 'failed'" in sql
    assert "status IN ('claimed', 'in_progress')" in sql
    assert params[1] == 600  # timeout seconds bound into the interval


def test_insert_audit_event_writes_row(monkeypatch):
    store = PostgresMetadataStore()
    cursor = _FakeCursor([])
    monkeypatch.setattr(store, "_core_connection", lambda ensure_schema=False: _FakeConn(cursor))

    ok = store.insert_audit_event({
        "id": "evt-1", "event_type": "user_registered", "summary": "New user registered: a@b.c",
        "actor_user_id": "u1", "actor_email": "a@b.c", "target_type": "user",
        "target_id": "u1", "target_label": "a@b.c", "details": {"x": 1},
        "created_at": None,
    })

    assert ok is True
    sql, params = cursor.executed[-1]
    assert "INSERT INTO admin_audit_log" in sql
    assert params[0] == "evt-1" and params[1] == "user_registered"


def test_list_audit_events_returns_page(monkeypatch):
    store = PostgresMetadataStore()
    cursor = _FakeCursor([
        ("count(*)", [(2,)]),
        ("FROM admin_audit_log", [
            ("evt-2", "drone_registered", "New Drone registered: Cab", "a@b.c", "drone", "d1", "Cab", None, "2026-06-13T00:00:00Z"),
            ("evt-1", "user_registered", "New user registered: a@b.c", "a@b.c", "user", "u1", "a@b.c", '{"x": 1}', "2026-06-12T00:00:00Z"),
        ]),
    ])
    # fetchone() must return the count tuple for the COUNT query.
    cursor.fetchone = lambda: (2,)  # type: ignore[assignment]
    monkeypatch.setattr(store, "_core_connection", lambda ensure_schema=False: _FakeConn(cursor))

    page = store.list_audit_events(limit=20, offset=0)
    assert page["total"] == 2
    assert len(page["events"]) == 2
    assert page["events"][1]["details"] == {"x": 1}


def test_mirror_device_details_routes_boolean_metric_to_text():
    # Regression: bool is a subclass of int, so a boolean performance metric used to be
    # sent to the numeric metric_value column and fail with DatatypeMismatch, aborting the
    # whole state persist. Booleans must route to metric_text instead.
    store = PostgresMetadataStore()
    cursor = _FakeCursor([])
    store._mirror_device_details(cursor, {
        "id": "drone-1",
        "system_info": {"performance": {"cpu": {"throttled": True, "load": 1.5}}},
    })
    metric_params = {
        params[2]: params
        for sql, params in cursor.executed
        if "INSERT INTO drone_performance_metrics" in sql
    }
    assert metric_params["throttled"][3] is None       # metric_value (numeric) not set
    assert metric_params["throttled"][4] == "True"      # boolean stored as text
    assert metric_params["load"][3] == 1.5              # real number still numeric
    assert metric_params["load"][4] is None


def test_heartbeat_persists_performance_metrics_and_pixen(monkeypatch):
    store = PostgresMetadataStore()
    cursor = _FakeCursor([])
    monkeypatch.setattr(store, "_core_connection", lambda ensure_schema=False: _FakeConn(cursor))

    ok = store.update_device_heartbeat_data(
        "drone-1",
        system_info={
            "hostname": "cab",
            "pixen_installed": True,
            "performance": {"cpu": {"throttled": False, "load": 2.5}},
        },
    )

    assert ok is True
    system_params = next(params for sql, params in cursor.executed if "INSERT INTO drone_system_info" in sql)
    assert system_params[-2] is True  # pixen_installed, before container
    metric_params = {
        params[2]: params
        for sql, params in cursor.executed
        if "INSERT INTO drone_performance_metrics" in sql
    }
    assert metric_params["throttled"][3] is None
    assert metric_params["throttled"][4] == "False"
    assert metric_params["load"][3] == 2.5
    assert metric_params["load"][4] is None
