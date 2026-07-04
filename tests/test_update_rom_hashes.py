"""Tests for deferred ROM fingerprint hash patches landing on drone_games.

The drone uploads the ROM inventory first without a fingerprint, then sends the sampled
fingerprint in a separate ``rom_hash_patch``. That patch updates the ``drone_games`` row
keyed by (drone_id, system_name, gamelist_id); otherwise rom_fingerprint stays NULL there
(master-list dedup / P2P source selection).
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


def test_update_rom_hashes_patches_drone_games():
    executed: list = []
    store = _store_with_fake_conn(executed)

    store.update_rom_hashes(
        "device-internal-1",
        [{"system": "snes", "gamelist_game_id": "2144", "rom_fingerprint": "ABC123"}],
    )

    tables = {sql for sql, _ in executed}
    assert any("drone_games" in sql for sql in tables)
    assert not any("overmind_device_assets" in sql for sql in tables)
    assert not any("drone_roms" in sql for sql in tables)

    params = [params for sql, params in executed if "drone_games" in sql][0]
    # (fingerprint, drone_id, system_name, gamelist_id)
    assert params == [("ABC123", "device-internal-1", "snes", "2144")]


def test_update_rom_hashes_skips_patches_without_fingerprint():
    executed: list = []
    store = _store_with_fake_conn(executed)

    store.update_rom_hashes(
        "device-internal-1",
        [{"system": "snes", "file_path": "Game One.zip"}],
    )

    assert executed == []
