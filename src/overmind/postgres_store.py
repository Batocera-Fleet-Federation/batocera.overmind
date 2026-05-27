"""Optional PostgreSQL storage for collected Drone metadata."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Iterable, Optional


def database_url() -> Optional[str]:
    value = os.getenv("OVERMIND_DATABASE_URL") or os.getenv("DATABASE_URL")
    if value:
        return value
    host = os.getenv("OVERMIND_POSTGRES_HOST")
    if not host:
        return None
    user = os.getenv("OVERMIND_POSTGRES_USER", "overmind")
    password = os.getenv("OVERMIND_POSTGRES_PASSWORD", "overmind")
    database = os.getenv("OVERMIND_POSTGRES_DB", "overmind")
    port = os.getenv("OVERMIND_POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


class PostgresMetadataStore:
    """Tiny optional store; the app still works when PostgreSQL is absent."""

    def __init__(self) -> None:
        self.url = database_url()
        self._ready = False

    def _connect(self):
        if not self.url:
            return None
        try:
            import psycopg
        except Exception:
            return None
        return psycopg.connect(self.url)

    def ensure_schema(self) -> None:
        if self._ready or not self.url:
            return
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS drone_action_results (
                        id BIGSERIAL PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        action_id TEXT,
                        result_type TEXT,
                        result JSONB NOT NULL,
                        received_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS overmind_app_state (
                        id TEXT PRIMARY KEY,
                        state JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS overmind_device_assets (
                        device_internal_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        asset_type TEXT NOT NULL,
                        item_key TEXT NOT NULL,
                        system_name TEXT,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (device_internal_id, asset_type, item_key)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_overmind_device_assets_device_type
                    ON overmind_device_assets (device_internal_id, asset_type)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_overmind_device_assets_type_system
                    ON overmind_device_assets (asset_type, system_name)
                    """
                )
                cur.execute(
                    """
                    UPDATE overmind_app_state
                    SET state = state - 'roms' - 'bios' - 'artwork'
                    WHERE id = 'default'
                      AND (state ? 'roms' OR state ? 'bios' OR state ? 'artwork')
                    """
                )
        self._ready = True

    def assets_enabled(self) -> bool:
        if not self.url:
            return False
        self.ensure_schema()
        return self._ready

    def store_action_result(self, device_id: str, action_id: str, result: dict) -> None:
        if not self.url:
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO drone_action_results (device_id, action_id, result_type, result)
                    VALUES (%s, %s, %s, %s::jsonb)
                    """,
                    (device_id, action_id, result.get("type"), json.dumps(result, default=str)),
                )

    def load_app_state(self) -> Optional[dict]:
        if not self.url:
            return None
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM overmind_app_state WHERE id = %s", ("default",))
                row = cur.fetchone()
        if not row:
            return None
        return _decode_state(row[0])

    def store_app_state(self, state: dict) -> None:
        if not self.url:
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        encoded = json.dumps(_encode_state(state))
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO overmind_app_state (id, state, updated_at)
                    VALUES (%s, %s::jsonb, now())
                    ON CONFLICT (id)
                    DO UPDATE SET state = EXCLUDED.state, updated_at = now()
                    """,
                    ("default", encoded),
                )

    def clear_device_assets(self, device_internal_id: str, asset_type: Optional[str] = None) -> None:
        if not self.assets_enabled():
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                if asset_type:
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s AND asset_type = %s",
                        (device_internal_id, asset_type),
                    )
                else:
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s",
                        (device_internal_id,),
                    )

    def upsert_device_assets(
        self,
        device_internal_id: str,
        device_id: str,
        asset_type: str,
        rows: Iterable[dict],
        *,
        replace: bool = False,
        replace_system: Optional[str] = None,
    ) -> list[str]:
        if not self.assets_enabled():
            return []
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return []
        prepared = []
        row_ids = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_key = _asset_key(asset_type, row)
            if not item_key:
                continue
            system_name = str(row.get("system_name") or row.get("system") or "").strip() or None
            row_id = str(row.get("id") or item_key)
            payload = {**row, "id": row_id, "device_id": row.get("device_id") or device_id}
            prepared.append((device_internal_id, device_id, asset_type, item_key, system_name, json.dumps(_encode_state(payload))))
            row_ids.append(row_id)
        with conn:
            with conn.cursor() as cur:
                if replace_system:
                    cur.execute(
                        """
                        DELETE FROM overmind_device_assets
                        WHERE device_internal_id = %s AND asset_type = %s AND lower(coalesce(system_name, '')) = lower(%s)
                        """,
                        (device_internal_id, asset_type, replace_system),
                    )
                elif replace:
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s AND asset_type = %s",
                        (device_internal_id, asset_type),
                    )
                if prepared:
                    cur.executemany(
                        """
                        INSERT INTO overmind_device_assets (device_internal_id, device_id, asset_type, item_key, system_name, payload, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                        ON CONFLICT (device_internal_id, asset_type, item_key)
                        DO UPDATE SET device_id = EXCLUDED.device_id,
                                      system_name = EXCLUDED.system_name,
                                      payload = EXCLUDED.payload,
                                      updated_at = now()
                        """,
                        prepared,
                    )
        return row_ids

    def list_device_assets(self, device_internal_id: str, asset_type: str, system_name: Optional[str] = None) -> list[dict]:
        if not self.assets_enabled():
            return []
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return []
        sql = "SELECT payload FROM overmind_device_assets WHERE device_internal_id = %s AND asset_type = %s"
        params = [device_internal_id, asset_type]
        if system_name:
            sql += " AND lower(coalesce(system_name, '')) = lower(%s)"
            params.append(system_name)
        sql += " ORDER BY system_name NULLS LAST, item_key"
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [_decode_state(row[0]) for row in rows]

    def list_assets_for_devices(self, device_internal_ids: Iterable[str], asset_type: str) -> list[dict]:
        ids = [str(value) for value in device_internal_ids if value]
        if not ids or not self.assets_enabled():
            return []
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return []
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT device_internal_id, payload
                    FROM overmind_device_assets
                    WHERE device_internal_id = ANY(%s) AND asset_type = %s
                    ORDER BY system_name NULLS LAST, item_key
                    """,
                    (ids, asset_type),
                )
                rows = cur.fetchall()
        output = []
        for internal_id, payload in rows:
            decoded = _decode_state(payload)
            if isinstance(decoded, dict):
                decoded["_device_internal_id"] = internal_id
                output.append(decoded)
        return output

    def update_rom_hashes(self, device_internal_id: str, patches: Iterable[dict]) -> None:
        if not self.assets_enabled():
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        updates = []
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            item_key = _asset_key("rom", patch)
            md5_value = patch.get("rom_md5") or patch.get("md5") or patch.get("hash")
            if item_key and md5_value:
                updates.append((str(md5_value), device_internal_id, item_key))
        if not updates:
            return
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE overmind_device_assets
                    SET payload = jsonb_set(jsonb_set(payload, '{rom_md5}', to_jsonb(%s::text), true), '{md5}', to_jsonb(%s::text), true),
                        updated_at = now()
                    WHERE device_internal_id = %s AND asset_type = 'rom' AND item_key = %s
                    """,
                    [(md5, md5, internal_id, key) for md5, internal_id, key in updates],
                )


def _encode_state(value):
    if isinstance(value, datetime):
        return {"__overmind_type": "datetime", "value": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): _encode_state(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_state(item) for item in value]
    return value


def _decode_state(value):
    if isinstance(value, dict):
        if value.get("__overmind_type") == "datetime" and isinstance(value.get("value"), str):
            try:
                return datetime.fromisoformat(value["value"])
            except ValueError:
                return value["value"]
        return {key: _decode_state(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_state(item) for item in value]
    return value


def _asset_key(asset_type: str, row: dict) -> str:
    if asset_type == "rom":
        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
        path = str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_file") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return f"{system}:{path}" if system and path else ""
    if asset_type == "bios":
        md5 = str(row.get("bios_md5") or row.get("md5") or row.get("hash") or "").strip().lower()
        path = str(row.get("file_path") or row.get("relative_path") or row.get("path") or row.get("bios_name") or row.get("name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return f"md5:{md5}" if md5 else f"path:{path}" if path else ""
    if asset_type == "artwork":
        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
        path = str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        types = row.get("artwork_types") if isinstance(row.get("artwork_types"), list) else []
        type_key = ",".join(sorted(str(value).strip().lower() for value in types if str(value).strip()))
        return f"{system}:{path}:{type_key}" if system and path and type_key else ""
    return ""


postgres_store = PostgresMetadataStore()
