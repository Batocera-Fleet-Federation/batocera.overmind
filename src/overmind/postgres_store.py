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

    def refresh_from_environment(self) -> None:
        """Refresh the connection URL after runtime secrets are applied."""
        updated = database_url()
        if updated != self.url:
            self.url = updated
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
                    CREATE TABLE IF NOT EXISTS overmind_device_asset_staging (
                        device_internal_id TEXT NOT NULL,
                        inventory_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        asset_type TEXT NOT NULL,
                        item_key TEXT NOT NULL,
                        system_name TEXT,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (device_internal_id, inventory_id, asset_type, item_key)
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
                    CREATE INDEX IF NOT EXISTS idx_overmind_device_asset_staging_inventory
                    ON overmind_device_asset_staging (device_internal_id, inventory_id, asset_type)
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

    def begin_device_asset_inventory(self, device_internal_id: str, inventory_id: str) -> None:
        if not self.assets_enabled():
            return
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM overmind_device_asset_staging WHERE device_internal_id = %s",
                    (device_internal_id,),
                )

    def stage_device_assets(
        self,
        device_internal_id: str,
        device_id: str,
        inventory_id: str,
        asset_type: str,
        rows: Iterable[dict],
    ) -> list[str]:
        if not self.assets_enabled():
            return []
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
            prepared.append((device_internal_id, inventory_id, device_id, asset_type, item_key, system_name, json.dumps(_encode_state(payload))))
            row_ids.append(row_id)
        if not prepared:
            return row_ids
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO overmind_device_asset_staging
                        (device_internal_id, inventory_id, device_id, asset_type, item_key, system_name, payload, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    ON CONFLICT (device_internal_id, inventory_id, asset_type, item_key)
                    DO UPDATE SET device_id = EXCLUDED.device_id,
                                  system_name = EXCLUDED.system_name,
                                  payload = EXCLUDED.payload,
                                  updated_at = now()
                    """,
                    prepared,
                )
        return row_ids

    def publish_device_asset_inventory(self, device_internal_id: str, inventory_id: str) -> None:
        if not self.assets_enabled():
            return
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM overmind_device_assets WHERE device_internal_id = %s",
                    (device_internal_id,),
                )
                cur.execute(
                    """
                    INSERT INTO overmind_device_assets
                        (device_internal_id, device_id, asset_type, item_key, system_name, payload, updated_at)
                    SELECT device_internal_id, device_id, asset_type, item_key, system_name, payload, now()
                    FROM overmind_device_asset_staging
                    WHERE device_internal_id = %s AND inventory_id = %s
                    """,
                    (device_internal_id, inventory_id),
                )
                cur.execute(
                    "DELETE FROM overmind_device_asset_staging WHERE device_internal_id = %s",
                    (device_internal_id,),
                )

    def delete_device_asset_rows(self, device_internal_id: str, asset_type: str, rows: Iterable[dict]) -> None:
        if not self.assets_enabled():
            return
        source_rows = [row for row in rows if isinstance(row, dict)]
        keys = [_asset_key(asset_type, row) for row in source_rows]
        keys = [key for key in keys if key]
        paths = [
            str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or "").replace("\\", "/").strip().lstrip("./").lower()
            for row in source_rows
        ]
        paths = [path for path in paths if path]
        if not keys and not paths:
            return
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                if asset_type == "rom":
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s AND asset_type = %s AND item_key = ANY(%s)",
                        (device_internal_id, asset_type, keys),
                    )
                elif asset_type == "bios":
                    cur.execute(
                        """
                        DELETE FROM overmind_device_assets
                        WHERE device_internal_id = %s AND asset_type = %s
                          AND (
                              item_key = ANY(%s)
                              OR lower(coalesce(payload->>'file_path', payload->>'relative_path', payload->>'rom_path', '')) = ANY(%s)
                          )
                        """,
                        (device_internal_id, asset_type, keys, paths),
                    )
                else:
                    for row in source_rows:
                        key = _asset_key(asset_type, row)
                        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
                        path = str(row.get("rom_path") or row.get("file_path") or "").replace("\\", "/").strip().lstrip("./").lower()
                        cur.execute(
                            """
                            DELETE FROM overmind_device_assets
                            WHERE device_internal_id = %s AND asset_type = %s
                              AND (
                                  item_key = %s
                                  OR (lower(coalesce(system_name, payload->>'system', '')) = %s
                                      AND lower(coalesce(payload->>'rom_path', payload->>'file_path', '')) = %s)
                              )
                            """,
                            (device_internal_id, asset_type, key, system, path),
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

    def page_master_assets(
        self,
        device_internal_ids: Iterable[str],
        asset_type: str,
        *,
        selected_internal_id: Optional[str] = None,
        query: Optional[str] = None,
        system_name: Optional[str] = None,
        status: Optional[str] = None,
        artwork_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[dict], int]:
        """Return only the master-list asset rows needed to render one page."""
        ids = [str(value) for value in device_internal_ids if value]
        if not ids or not self.assets_enabled():
            return [], 0
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return [], 0
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        offset = (page - 1) * per_page
        if asset_type == "rom":
            master_key = """
                CASE
                    WHEN nullif(lower(coalesce(payload->>'rom_md5', '')), '') IS NOT NULL
                        THEN 'md5:' || lower(payload->>'rom_md5')
                    ELSE 'path:' || lower(coalesce(system_name, '')) || ':' || lower(coalesce(payload->>'file_path', payload->>'rom_name', ''))
                END
            """
            sort_key = "lower(coalesce(system_name, '')) || ':' || lower(coalesce(payload->>'file_path', payload->>'rom_name', ''))"
            source = "overmind_device_assets"
            extra_select = "NULL::text AS artwork_type"
        elif asset_type == "bios":
            master_key = """
                CASE
                    WHEN nullif(lower(coalesce(payload->>'bios_md5', payload->>'md5', '')), '') IS NOT NULL
                        THEN 'md5:' || lower(coalesce(payload->>'bios_md5', payload->>'md5'))
                    ELSE 'path:' || lower(coalesce(payload->>'file_path', payload->>'relative_path', payload->>'bios_name', ''))
                END
            """
            sort_key = "lower(coalesce(payload->>'file_path', payload->>'relative_path', payload->>'bios_name', ''))"
            source = "overmind_device_assets"
            extra_select = "NULL::text AS artwork_type"
        elif asset_type == "artwork":
            master_key = """
                'artwork:' || lower(coalesce(system_name, payload->>'system', '')) || ':' ||
                lower(coalesce(payload->>'rom_path', payload->>'file_path', payload->>'rom_name', '')) || ':' ||
                lower(artwork_type.value)
            """
            sort_key = """
                lower(coalesce(system_name, payload->>'system', '')) || ':' ||
                lower(coalesce(payload->>'rom_path', payload->>'file_path', payload->>'rom_name', '')) || ':' ||
                lower(artwork_type.value)
            """
            source = """
                overmind_device_assets
                CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(payload->'artwork_types', '[]'::jsonb)) AS artwork_type(value)
            """
            extra_select = "artwork_type.value AS artwork_type"
        else:
            return [], 0

        normalized_sql = f"""
            SELECT device_internal_id, device_id, payload, system_name, {extra_select},
                   {master_key} AS master_key,
                   {sort_key} AS sort_key
            FROM {source}
            WHERE device_internal_id = ANY(%s) AND asset_type = %s
        """
        clauses = ["n.master_key <> ''"]
        filters: list[object] = []
        clean_query = str(query or "").strip().lower()
        if clean_query:
            clauses.append("lower(n.payload::text) LIKE %s")
            filters.append(f"%{clean_query}%")
        clean_system = str(system_name or "").strip().lower()
        if clean_system:
            clauses.append("lower(coalesce(n.system_name, n.payload->>'system', '')) = %s")
            filters.append(clean_system)
        clean_artwork_type = str(artwork_type or "").strip().lower()
        if clean_artwork_type and asset_type == "artwork":
            clauses.append("lower(coalesce(n.artwork_type, '')) = %s")
            filters.append(clean_artwork_type)
        clean_status = str(status or "").strip().lower()
        if selected_internal_id and clean_status in {"missing", "present"}:
            presence = "EXISTS" if clean_status == "present" else "NOT EXISTS"
            clauses.append(
                f"{presence} (SELECT 1 FROM normalized selected WHERE selected.master_key = n.master_key AND selected.device_internal_id = %s)"
            )
            filters.append(str(selected_internal_id))
        filtered_sql = f"""
            filtered_keys AS (
                SELECT n.master_key, min(n.sort_key) AS sort_key
                FROM normalized n
                WHERE {" AND ".join(clauses)}
                GROUP BY n.master_key
            )
        """
        base_params = [ids, asset_type, *filters]
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"WITH normalized AS ({normalized_sql}), {filtered_sql} SELECT count(*) FROM filtered_keys",
                    base_params,
                )
                total = int((cur.fetchone() or [0])[0] or 0)
                if not total:
                    return [], 0
                selected_param = str(selected_internal_id) if selected_internal_id else None
                cur.execute(
                    f"""
                    WITH normalized AS ({normalized_sql}), {filtered_sql},
                    paged_keys AS (
                        SELECT master_key
                        FROM filtered_keys
                        ORDER BY sort_key, master_key
                        LIMIT %s OFFSET %s
                    )
                    SELECT n.device_internal_id, n.payload, n.master_key, n.artwork_type,
                           CASE WHEN %s::text IS NULL THEN false ELSE EXISTS (
                               SELECT 1 FROM normalized selected
                               WHERE selected.master_key = n.master_key AND selected.device_internal_id = %s
                           ) END AS present_on_selected
                    FROM normalized n
                    JOIN paged_keys p ON p.master_key = n.master_key
                    ORDER BY n.sort_key, n.master_key, n.device_internal_id
                    """,
                    [*base_params, per_page, offset, selected_param, selected_param],
                )
                rows = cur.fetchall()
        output = []
        for internal_id, payload, group_key, row_artwork_type, present_on_selected in rows:
            decoded = _decode_state(payload)
            if isinstance(decoded, dict):
                decoded["_device_internal_id"] = internal_id
                decoded["_master_key"] = group_key
                decoded["_artwork_type"] = row_artwork_type
                decoded["_present_on_selected"] = bool(present_on_selected)
                output.append(decoded)
        return output, total

    def summarize_rom_systems(self, device_internal_ids: Iterable[str]) -> list[dict]:
        ids = [str(value) for value in device_internal_ids if value]
        if not ids or not self.assets_enabled():
            return []
        conn = self._connect()
        if conn is None:
            return []
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT system_name, count(*), count(DISTINCT device_internal_id)
                    FROM overmind_device_assets
                    WHERE device_internal_id = ANY(%s) AND asset_type = 'rom' AND system_name IS NOT NULL
                    GROUP BY system_name
                    ORDER BY system_name
                    """,
                    (ids,),
                )
                rows = cur.fetchall()
        return [
            {"system_name": row[0], "rom_count": int(row[1]), "device_count": int(row[2])}
            for row in rows
        ]

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
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {str(key).replace("\x00", ""): _encode_state(item) for key, item in value.items()}
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
