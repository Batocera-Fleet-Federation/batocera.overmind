"""Unit tests for Postgres-only download-snapshot persistence.

Covers the sync_id round-trip: Overmind mints a sync_id when it queues a
sync_rom/sync_bios/sync_system action, the Drone echoes it in every download
job snapshot it pushes back, but download_items previously dropped it on
insert -- silently breaking correlation between a "pending" placeholder row
(written before the Drone claims the action) and the real job once it appears.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore


class _FakeCursor:
    def __init__(self, rowcount=1, fetchone_result=None, fetchall_results=None):
        self.executed = []
        self.rowcount = rowcount
        self.fetchone_result = fetchone_result
        # A list of results returned in order across successive fetchall() calls
        # (list_download_states issues two queries: snapshot ids, then items).
        self._fetchall_results = list(fetchall_results or [[]])

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        if len(self._fetchall_results) > 1:
            return self._fetchall_results.pop(0)
        return self._fetchall_results[0]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


class InsertDownloadSnapshotTests(unittest.TestCase):
    def test_persists_sync_id_for_each_item(self):
        store = PostgresMetadataStore()
        cursor = _FakeCursor(fetchone_result=[42])
        state = {
            "received_at": datetime.utcnow(),
            "concurrency": {"scope": "target_drone", "active_limit": 1},
            "active": [],
            "queued": [
                {
                    "job_id": "job-1",
                    "sync_id": "sync-abc",
                    "status": "pending",
                    "system": "snes",
                    "file_path": "Game.zip",
                }
            ],
            "recent": [],
        }

        store._insert_download_snapshot(cursor, "device-internal-id", state)

        insert_calls = [call for call in cursor.executed if "INSERT INTO download_items" in call[0]]
        self.assertEqual(len(insert_calls), 1)
        sql, params = insert_calls[0]
        self.assertIn("sync_id", sql)
        self.assertEqual(params[-1], "sync-abc")  # sync_id is the last bound param
        self.assertEqual(params[1], "job-1")  # job_id unaffected

    def test_missing_sync_id_persists_as_null(self):
        store = PostgresMetadataStore()
        cursor = _FakeCursor(fetchone_result=[42])
        state = {
            "received_at": datetime.utcnow(),
            "active": [{"job_id": "job-2", "status": "downloading", "system": "snes", "file_path": "Other.zip"}],
            "queued": [],
            "recent": [],
        }

        store._insert_download_snapshot(cursor, "device-internal-id", state)

        insert_calls = [call for call in cursor.executed if "INSERT INTO download_items" in call[0]]
        _, params = insert_calls[0]
        self.assertIsNone(params[-1])


class ListDownloadStatesTests(unittest.TestCase):
    def _store_with_devices(self, cursor, devices):
        store = PostgresMetadataStore()
        store.list_user_devices = lambda user_id: devices
        store.user_can_access_device = lambda user_id, device_id: (devices[0] if devices else None)
        store._core_connection = lambda ensure_schema=False: _FakeConn(cursor)
        return store

    def test_returns_sync_id_from_column(self):
        device = {"id": "internal-1", "device_id": "drone-a", "device_name": "Drone A"}
        # download_snapshots.target_drone_id actually stores the device's INTERNAL
        # id (it's an FK to drones(id)), not the Drone-reported device_id string.
        snapshot_rows = [("internal-1", 1, datetime.utcnow(), "target_drone", 1)]
        item_rows = [
            (1, "job-9", "queued", "rom", "pending", None, "snes", "Game.zip", None, None, None, 100, 0, 0.0, 0, 1, None, "sync-xyz"),
        ]
        cursor = _FakeCursor(fetchall_results=[snapshot_rows, item_rows])
        store = self._store_with_devices(cursor, [device])

        rows = store.list_download_states("user-1", device_id="drone-a")
        self.assertEqual(len(rows), 1)
        items = rows[0]["downloads"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sync_id"], "sync-xyz")

    def test_falls_back_to_job_id_when_sync_id_column_is_null(self):
        device = {"id": "internal-1", "device_id": "drone-a", "device_name": "Drone A"}
        snapshot_rows = [("internal-1", 1, datetime.utcnow(), "target_drone", 1)]
        item_rows = [
            (1, "job-legacy", "queued", "rom", "queued", None, "snes", "Game.zip", None, None, None, 100, 0, 0.0, 0, 1, None, None),
        ]
        cursor = _FakeCursor(fetchall_results=[snapshot_rows, item_rows])
        store = self._store_with_devices(cursor, [device])

        rows = store.list_download_states("user-1", device_id="drone-a")
        self.assertEqual(rows[0]["downloads"][0]["sync_id"], "job-legacy")


if __name__ == "__main__":
    unittest.main()
