"""Tests for deferred ROM md5 hash patches landing on both asset stores.

The drone uploads the ROM inventory first without md5, then sends md5 in a
separate ``rom_hash_patch``. That patch must update both the jsonb
``overmind_device_assets`` store and the relational ``drone_roms`` table;
otherwise md5 stays NULL in ``drone_roms`` (master-list dedup, games count).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore


class _FakeCursor:
    def __init__(self, executed):
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, params):
        self._executed.append((sql, list(params)))

    def execute(self, sql, params=None):  # pragma: no cover - not expected here
        self._executed.append((sql, params))


class _FakeConn:
    def __init__(self, executed):
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._executed)


def _store_with_fake_conn(executed):
    store = PostgresMetadataStore()
    store.ensure_schema = lambda: None  # type: ignore[assignment]
    store.assets_enabled = lambda: True  # type: ignore[assignment]
    store._connect = lambda: _FakeConn(executed)  # type: ignore[assignment]
    return store


def test_update_rom_hashes_patches_both_asset_stores():
    executed: list = []
    store = _store_with_fake_conn(executed)

    store.update_rom_hashes(
        "device-internal-1",
        [{"system": "snes", "file_path": "Game One.zip", "rom_md5": "ABC123"}],
    )

    tables = {sql for sql, _ in executed}
    assert any("overmind_device_assets" in sql for sql in tables)
    assert any("drone_roms" in sql for sql in tables)

    drone_params = [params for sql, params in executed if "drone_roms" in sql][0]
    # (md5, drone_id, system_name, normalized_path) with a lowercased path.
    assert drone_params == [("ABC123", "device-internal-1", "snes", "game one.zip")]


def test_update_rom_hashes_skips_patches_without_md5():
    executed: list = []
    store = _store_with_fake_conn(executed)

    store.update_rom_hashes(
        "device-internal-1",
        [{"system": "snes", "file_path": "Game One.zip"}],
    )

    assert executed == []
