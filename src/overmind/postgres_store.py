"""Optional PostgreSQL storage for collected Drone metadata."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("overmind.postgres_store")

try:
    from overmind import cache as _cache
except Exception:
    _cache = None  # type: ignore[assignment]


def _compute_asset_keys(asset_type: str, payload: dict, system_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (master_key, sort_key) for a ROM or BIOS asset row, or (None, None) for artwork."""
    if asset_type == "rom":
        fingerprint = str(payload.get("rom_fingerprint") or "").strip().lower()
        sys = str(system_name or "").strip().lower()
        path = str(payload.get("file_path") or payload.get("rom_name") or "").strip().lower()
        mk = f"fingerprint:{fingerprint}" if fingerprint else f"path:{sys}:{path}"
        return mk, f"{sys}:{path}"
    if asset_type == "bios":
        fingerprint = str(payload.get("bios_md5") or payload.get("md5") or "").strip().lower()
        path = str(payload.get("file_path") or payload.get("relative_path") or payload.get("bios_name") or "").strip().lower()
        mk = f"fingerprint:{fingerprint}" if fingerprint else f"path:{path}"
        return mk, path
    if asset_type == "saves":
        sys = str(system_name or "").strip().lower()
        path = str(payload.get("file_path") or payload.get("relative_path") or payload.get("save_name") or "").strip().lower()
        return f"saves:{sys}:{path}", f"{sys}:{path}"
    return None, None


def _is_excluded_emulator_config_path(value: str) -> bool:
    label = str(value or "").replace("\\", "/").strip("/")
    lowered = label.lower()
    if ".bak" in lowered:
        return True
    return bool({"log", "logs"} & {part for part in lowered.split("/") if part})


def _idle_volume_automation_columns(info: dict) -> tuple:
    """Extract (enabled, idle_minutes, target) from a Drone's reported system_info.

    The Drone reports idle-volume automation as a nested ``idle_volume_automation``
    dict; Overmind stores it as three explicit columns for the per-Drone admin UI.
    Returns (None, None, None) when the Drone has not reported it.
    """
    raw = info.get("idle_volume_automation")
    if not isinstance(raw, dict):
        return (None, None, None)
    enabled = raw.get("enabled")
    try:
        idle_minutes = int(raw["idle_minutes"]) if raw.get("idle_minutes") is not None else None
    except (TypeError, ValueError):
        idle_minutes = None
    try:
        target = int(raw["target_volume"]) if raw.get("target_volume") is not None else None
    except (TypeError, ValueError):
        target = None
    return (bool(enabled) if enabled is not None else None, idle_minutes, target)


def _idle_volume_automation_dict(enabled, idle_minutes, target):
    """Rebuild the nested idle_volume_automation dict from stored columns.

    Returns None when the Drone has never reported it (all columns NULL) so the
    UI can show "not yet reported" rather than a misleading default.
    """
    if enabled is None and idle_minutes is None and target is None:
        return None
    return {
        "enabled": bool(enabled),
        "idle_minutes": idle_minutes,
        "target_volume": target,
    }


def _is_lambda_runtime() -> bool:
    return (os.getenv("OVERMIND_RUNTIME") or "").strip().lower() == "lambda" or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _persist_json_app_state_enabled() -> bool:
    return _env_bool("OVERMIND_PERSIST_JSON_STATE", not _is_lambda_runtime())


def _postgres_query_logging_enabled() -> bool:
    return _env_bool("OVERMIND_POSTGRES_QUERY_LOGGING", _is_lambda_runtime())


def _postgres_query_log_min_ms() -> float:
    try:
        return max(0.0, float(os.getenv("OVERMIND_POSTGRES_QUERY_LOG_MIN_MS", "2000")))
    except (TypeError, ValueError):
        return 2000.0


def _postgres_query_log_sql_chars() -> int:
    try:
        return max(80, int(os.getenv("OVERMIND_POSTGRES_QUERY_LOG_SQL_CHARS", "2000")))
    except (TypeError, ValueError):
        return 2000


def _format_query_for_log(query) -> str:
    text = " ".join(str(query or "").split())
    limit = _postgres_query_log_sql_chars()
    return text if len(text) <= limit else f"{text[:limit]}..."


class _TimedCursor:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._cursor.__exit__(exc_type, exc, tb)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, query, params=None, *args, **kwargs):
        return self._time_query("execute", query, params, lambda: self._cursor.execute(query, params, *args, **kwargs))

    def executemany(self, query, params_seq, *args, **kwargs):
        return self._time_query("executemany", query, params_seq, lambda: self._cursor.executemany(query, params_seq, *args, **kwargs))

    def _time_query(self, operation: str, query, params, call):
        if not _postgres_query_logging_enabled():
            return call()
        started = time.perf_counter()
        error_name = None
        try:
            return call()
        except Exception as error:
            error_name = error.__class__.__name__
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            if duration_ms >= _postgres_query_log_min_ms() or error_name:
                logger.warning(
                    "PostgreSQL query operation=%s duration_ms=%.2f rowcount=%s error=%s query=%s params=%s",
                    operation,
                    duration_ms,
                    getattr(self._cursor, "rowcount", None),
                    error_name or "-",
                    _format_query_for_log(query),
                    _format_params_for_log(params),
                )


class _TimedConnection:
    def __init__(self, conn) -> None:
        self._conn = conn

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self, *args, **kwargs):
        return _TimedCursor(self._conn.cursor(*args, **kwargs))


def _format_params_for_log(params) -> str:
    if not _env_bool("OVERMIND_POSTGRES_QUERY_LOG_PARAMS", False):
        return "hidden"
    try:
        return _format_query_for_log(repr(params))
    except Exception:
        return "unavailable"


def _is_missing_column_error(error: BaseException) -> bool:
    """Detect a PostgreSQL "undefined column" error (SQLSTATE 42703).

    Raised when deployed code SELECTs a column the database does not yet have —
    i.e. schema drift after a deploy whose migration has not landed. Matched by
    SQLSTATE so it works without importing psycopg at module load time.
    """
    sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if sqlstate == "42703":
        return True
    return error.__class__.__name__ == "UndefinedColumn"


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
    """PostgreSQL storage for Overmind application state and Drone metadata."""

    def __init__(self) -> None:
        self.url = database_url()
        self._ready = False
        self.last_error: Optional[str] = None

    def refresh_from_environment(self) -> None:
        """Refresh the connection URL after runtime secrets are applied."""
        updated = database_url()
        if updated != self.url:
            self.url = updated
            self._ready = False
            self.last_error = None

    def _connect(self):
        if not self.url:
            return None
        try:
            import psycopg
        except Exception:
            return None
        try:
            timeout = max(1, int(os.getenv("OVERMIND_POSTGRES_CONNECT_TIMEOUT_SECONDS", "8")))
            conn = psycopg.connect(self.url, connect_timeout=timeout)
            return _TimedConnection(conn) if _postgres_query_logging_enabled() else conn
        except Exception as error:
            self._ready = False
            self.last_error = f"{error.__class__.__name__}: {error}"
            logger.warning("PostgreSQL connection failed: %s", self.last_error)
            return None

    def available(self) -> bool:
        if not self.url:
            return False
        self.ensure_schema()
        return self._ready

    def _core_connection(self, *, ensure_schema: bool = True):
        if ensure_schema:
            if not self.available():
                return None
        elif not self.url:
            return None
        return self._connect()

    def update_device_heartbeat_data(
        self,
        internal_id: str,
        *,
        system_info: Optional[dict] = None,
        network: Optional[dict] = None,
        api_port: Optional[int] = None,
        scheme: Optional[str] = None,
        reachable_url: Optional[str] = None,
    ) -> bool:
        """Persist heartbeat payload fields (system info, network addresses, port/scheme) to DB."""
        if not internal_id or not any([system_info, network, api_port, scheme, reachable_url]):
            return False
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                if system_info and isinstance(system_info, dict):
                    info = system_info
                    iva = _idle_volume_automation_columns(info)
                    cur.execute(
                        """
                        INSERT INTO drone_system_info
                            (drone_id, hostname, model, system_name, architecture, cpu_model, cpu_cores,
                             cpu_threads, cpu_max_frequency, memory_available, memory_total,
                             batocera_version, screen_mode, audio_volume,
                             idle_volume_enabled, idle_volume_idle_minutes, idle_volume_target,
                             container, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (drone_id) DO UPDATE SET
                            hostname          = COALESCE(EXCLUDED.hostname,          drone_system_info.hostname),
                            model             = COALESCE(EXCLUDED.model,             drone_system_info.model),
                            system_name       = COALESCE(EXCLUDED.system_name,       drone_system_info.system_name),
                            architecture      = COALESCE(EXCLUDED.architecture,      drone_system_info.architecture),
                            cpu_model         = COALESCE(EXCLUDED.cpu_model,         drone_system_info.cpu_model),
                            cpu_cores         = COALESCE(EXCLUDED.cpu_cores,         drone_system_info.cpu_cores),
                            cpu_threads       = COALESCE(EXCLUDED.cpu_threads,       drone_system_info.cpu_threads),
                            cpu_max_frequency = COALESCE(EXCLUDED.cpu_max_frequency, drone_system_info.cpu_max_frequency),
                            memory_available  = COALESCE(EXCLUDED.memory_available,  drone_system_info.memory_available),
                            memory_total      = COALESCE(EXCLUDED.memory_total,      drone_system_info.memory_total),
                            batocera_version  = COALESCE(EXCLUDED.batocera_version,  drone_system_info.batocera_version),
                            screen_mode       = COALESCE(EXCLUDED.screen_mode,       drone_system_info.screen_mode),
                            audio_volume      = COALESCE(EXCLUDED.audio_volume,      drone_system_info.audio_volume),
                            idle_volume_enabled      = COALESCE(EXCLUDED.idle_volume_enabled,      drone_system_info.idle_volume_enabled),
                            idle_volume_idle_minutes = COALESCE(EXCLUDED.idle_volume_idle_minutes, drone_system_info.idle_volume_idle_minutes),
                            idle_volume_target       = COALESCE(EXCLUDED.idle_volume_target,       drone_system_info.idle_volume_target),
                            container         = COALESCE(EXCLUDED.container,         drone_system_info.container),
                            updated_at        = now()
                        """,
                        (
                            internal_id,
                            info.get("hostname"),
                            info.get("model"),
                            info.get("system") or info.get("system_name"),
                            info.get("architecture"),
                            info.get("cpu_model"),
                            info.get("cpu_cores"),
                            info.get("cpu_threads"),
                            info.get("cpu_max_frequency"),
                            info.get("memory_available"),
                            info.get("memory_total"),
                            info.get("batocera_version"),
                            info.get("screen_mode"),
                            info.get("audio_volume"),
                            iva[0],
                            iva[1],
                            iva[2],
                            info.get("container"),
                        ),
                    )
                if network and isinstance(network, dict):
                    for addr_type in ("ipv4", "ipv6"):
                        for addr in (network.get(addr_type) or []):
                            if addr:
                                cur.execute(
                                    "INSERT INTO drone_network_addresses (drone_id, address_type, address)"
                                    " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                    (internal_id, addr_type, str(addr)),
                                )
                    for addr_type, key in (("hostname", "hostname"), ("mac", "mac_address")):
                        val = network.get(key)
                        if val:
                            cur.execute(
                                "INSERT INTO drone_network_addresses (drone_id, address_type, address)"
                                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                (internal_id, addr_type, str(val)),
                            )
                reported_public_ip = None
                if network and isinstance(network, dict):
                    reported_public_ip = str(network.get("public_ip") or network.get("public") or "").strip() or None
                if any([api_port, scheme, reachable_url, reported_public_ip]):
                    cur.execute(
                        """
                        INSERT INTO drone_network_state (drone_id, api_port, scheme, reachable_url, public_ip, updated_at)
                        VALUES (%s, %s, %s, %s, %s, now())
                        ON CONFLICT (drone_id) DO UPDATE SET
                            api_port     = COALESCE(EXCLUDED.api_port,     drone_network_state.api_port),
                            scheme       = COALESCE(EXCLUDED.scheme,       drone_network_state.scheme),
                            reachable_url = COALESCE(EXCLUDED.reachable_url, drone_network_state.reachable_url),
                            public_ip    = COALESCE(EXCLUDED.public_ip,    drone_network_state.public_ip),
                            updated_at   = now()
                        """,
                        (internal_id, api_port, scheme or None, reachable_url or None, reported_public_ip),
                    )
        return True

    def update_device_rom_inventory_fingerprint(
        self,
        internal_id: str,
        *,
        drone_fingerprint: Optional[str] = None,
        overmind_fingerprint: Optional[str] = None,
        algorithm: Optional[str] = None,
    ) -> bool:
        if not internal_id or not any([drone_fingerprint, overmind_fingerprint]):
            return False
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE drones
                    SET rom_inventory_fingerprint = COALESCE(%s::text, rom_inventory_fingerprint),
                        drone_rom_inventory_fingerprint = COALESCE(%s::text, drone_rom_inventory_fingerprint),
                        rom_inventory_fingerprint_algorithm = COALESCE(%s::text, rom_inventory_fingerprint_algorithm),
                        rom_inventory_fingerprint_at = CASE WHEN %s::text IS NULL THEN rom_inventory_fingerprint_at ELSE now() END,
                        drone_rom_inventory_fingerprint_at = CASE WHEN %s::text IS NULL THEN drone_rom_inventory_fingerprint_at ELSE now() END
                    WHERE id = %s
                    """,
                    (overmind_fingerprint, drone_fingerprint, algorithm, overmind_fingerprint, drone_fingerprint, internal_id),
                )
                return cur.rowcount > 0

    def update_device_asset_thumbprints(
        self,
        internal_id: str,
        *,
        romset_thumbprint: Optional[str] = None,
        bios_thumbprint: Optional[str] = None,
        saves_thumbprint: Optional[str] = None,
    ) -> bool:
        """Persist the Drone-supplied asset thumbprints verbatim (no recompute)."""
        if not internal_id or (romset_thumbprint is None and bios_thumbprint is None and saves_thumbprint is None):
            return False
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE drones
                    SET romset_files_thumbprint = COALESCE(%s::text, romset_files_thumbprint),
                        bios_files_thumbprint = COALESCE(%s::text, bios_files_thumbprint),
                        saves_files_thumbprint = COALESCE(%s::text, saves_files_thumbprint),
                        romset_files_thumbprint_at = CASE WHEN %s::text IS NULL THEN romset_files_thumbprint_at ELSE now() END,
                        bios_files_thumbprint_at = CASE WHEN %s::text IS NULL THEN bios_files_thumbprint_at ELSE now() END,
                        saves_files_thumbprint_at = CASE WHEN %s::text IS NULL THEN saves_files_thumbprint_at ELSE now() END
                    WHERE id = %s
                    """,
                    (
                        romset_thumbprint, bios_thumbprint, saves_thumbprint,
                        romset_thumbprint, bios_thumbprint, saves_thumbprint, internal_id,
                    ),
                )
                return cur.rowcount > 0

    def touch_device_last_seen(self, internal_id: str) -> bool:
        """Update a Drone's liveness timestamp with a minimal write path."""
        conn = self._core_connection()
        if conn is None or not internal_id:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE drones SET last_seen = now() WHERE id = %s RETURNING user_id",
                    (internal_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                if _cache:
                    _cache.invalidate_user_devices(str(row[0]))
                return True

    # Migration IDs that must never block a Lambda cold start:
    # large backfills/indexes exceed the 30 s timeout on production-sized tables.
    _BACKGROUND_MIGRATION_IDS = frozenset({"0003", "0004", "0017", "0018"})

    def _run_migrations(self, conn, migration_files: list[Path]) -> None:
        """Apply a list of SQL migration files that have not yet been recorded."""
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS _overmind_migrations (
                        id TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
        for path in migration_files:
            migration_id = path.stem
            conn2 = self._connect()
            if conn2 is None:
                break
            try:
                with conn2:
                    with conn2.cursor() as cur:
                        row = cur.execute(
                            "SELECT 1 FROM _overmind_migrations WHERE id = %s", (migration_id,)
                        ).fetchone()
                        if row:
                            continue
                sql = path.read_text(encoding="utf-8")
                no_transaction = any(
                    line.strip().lower().startswith("-- no-transaction")
                    for line in sql.splitlines()
                )
                # Strip yoyo directives and rollback sections (not used here)
                lines = []
                in_rollback = False
                for line in sql.splitlines():
                    stripped = line.strip().lower()
                    if stripped.startswith("-- rollback"):
                        in_rollback = True
                    if stripped.startswith("-- depends:") or stripped.startswith("-- no-transaction"):
                        continue
                    if not in_rollback:
                        lines.append(line)
                clean_sql = "\n".join(lines).strip()
                if not clean_sql:
                    continue
                conn3 = self._connect()
                if conn3 is None:
                    break
                if no_transaction:
                    try:
                        target_conn = getattr(conn3, "_conn", conn3)
                        target_conn.autocommit = True
                        with conn3.cursor() as cur:
                            cur.execute(clean_sql)
                    finally:
                        try:
                            conn3.close()
                        except Exception:
                            pass
                else:
                    with conn3:
                        with conn3.cursor() as cur:
                            cur.execute(clean_sql)
                conn4 = self._connect()
                if conn4 is None:
                    break
                with conn4:
                    with conn4.cursor() as cur:
                        cur.execute(
                            "INSERT INTO _overmind_migrations (id) VALUES (%s) ON CONFLICT DO NOTHING",
                            (migration_id,),
                        )
                logger.info("Migration applied: %s", migration_id)
            except Exception as err:
                logger.warning("Migration %s failed (non-fatal): %s", migration_id, err)

    def ensure_schema(self) -> None:
        """Apply pending SQL migrations to bring the schema up to date.

        Uses a simple custom runner instead of yoyo to avoid:
        - Database-persisted locks that survive Lambda process death
        - Automatic rollbacks that can drop tables when steps fail
        - Complex state tracking that breaks under concurrent cold starts

        Each migration file is idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
        guards), so concurrent execution is safe without locks.

        Fast migrations run synchronously on cold start. Slow migrations
        (large backfills/indexes) run in a background thread so Lambda cold
        starts complete in < 1 s.
        """
        if self._ready or not self.url:
            return
        import threading as _threading

        if os.getenv("OVERMIND_RESET_RELATIONAL_SCHEMA", "").lower() == "true":
            conn = self._connect()
            if conn:
                with conn:
                    with conn.cursor() as cur:
                        self._drop_existing_schema(cur)

        migrations_dir = Path(__file__).parent / "migrations"
        all_files = sorted(migrations_dir.glob("*.sql"))
        fast_files = [f for f in all_files if not any(bg in f.stem for bg in self._BACKGROUND_MIGRATION_IDS)]
        slow_files = [f for f in all_files if any(bg in f.stem for bg in self._BACKGROUND_MIGRATION_IDS)]

        try:
            conn = self._connect()
            if conn is None:
                self.last_error = self.last_error or "Could not connect to PostgreSQL"
                logger.warning("Schema migration skipped: %s", self.last_error)
                return
            self._run_migrations(conn, fast_files)
            self._ready = True
            self.last_error = None
        except Exception as error:
            self._ready = False
            self.last_error = f"{error.__class__.__name__}: {error}"
            logger.warning("Schema migration failed: %s", self.last_error)
            return

        if slow_files:
            def _apply_slow() -> None:
                try:
                    slow_conn = self._connect()
                    if slow_conn is None:
                        return
                    self._run_migrations(slow_conn, slow_files)
                    logger.info("Background migrations applied: %s", [f.stem for f in slow_files])
                except Exception as slow_err:
                    logger.warning("Background migrations deferred (non-fatal): %s", slow_err)
            _threading.Thread(target=_apply_slow, name="schema-background-migrations", daemon=True).start()

    def _drop_existing_schema(self, cur) -> None:
        """Drop all known Overmind tables for an intentional no-migration rebuild."""
        cur.execute(
            """
            DROP TABLE IF EXISTS
                notification_delivery_attempts,
                notification_recipients,
                notifications,
                drone_emulator_config_versions,
                drone_emulator_configs,
                drone_log_files,
                drone_log_sources,
                gameplay_sessions,
                sync_activity,
                download_items,
                download_snapshots,
                drone_action_result_fields,
                drone_action_result_records,
                drone_action_results,
                drone_actions,
                drone_peer_checks,
                drone_speed_samples,
                drone_events,
                drone_artwork,
                drone_bios,
                drone_roms,
                asset_inventory_batches,
                systems,
                drone_auto_sync_policy_systems,
                drone_auto_sync_policies,
                drone_certificates,
                drone_system_info,
                drone_network_state,
                device_admin_claims,
                pending_drone_connections,
                approved_drone_tokens,
                integration_tokens,
                drones,
                swarm_invitations,
                swarm_memberships,
                swarms,
                password_resets,
                email_verifications,
                user_notification_type_settings,
                user_notification_settings,
                user_fleet_settings,
                user_auth_identities,
                user_profiles,
                users,
                overmind_device_asset_staging,
                overmind_device_assets,
                overmind_app_state,
                overmind_schema_versions
            CASCADE
            """
        )

    def assets_enabled(self) -> bool:
        if not self.url:
            return False
        self.ensure_schema()
        return self._ready

    def store_action_result(self, device_id: str, action_id: str, result: dict) -> None:
        if not self.url:
            return
        if result.get("type") == "log_sources":
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

    def store_device_log_sources(self, internal_device_id: str, payload: dict, max_lines: int = 1000) -> None:
        if not self.url or not internal_device_id or not isinstance(payload, dict):
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
        append = bool(payload.get("append", True))
        with conn:
            with conn.cursor() as cur:
                for source in logs:
                    if not isinstance(source, dict):
                        continue
                    source_name = str(source.get("source") or "").strip()
                    if not source_name:
                        continue
                    cur.execute(
                        """
                        INSERT INTO drone_log_sources (drone_id, source, updated_at)
                        VALUES (%s, %s, now())
                        ON CONFLICT (drone_id, source) DO UPDATE SET updated_at = EXCLUDED.updated_at
                        RETURNING id
                        """,
                        (internal_device_id, source_name),
                    )
                    source_id = cur.fetchone()[0]
                    for file_row in source.get("files") if isinstance(source.get("files"), list) else []:
                        if not isinstance(file_row, dict):
                            continue
                        content = str(file_row.get("content") or "")
                        if not content and not file_row.get("error"):
                            continue
                        if file_row.get("error"):
                            content = f"[Log read error] {file_row.get('error')}"
                        path = str(file_row.get("path") or source_name)
                        modified_at = self._dt(file_row.get("modified_at"))
                        cur.execute(
                            "SELECT content FROM drone_log_files WHERE source_id = %s AND path = %s",
                            (source_id, path),
                        )
                        existing_row = cur.fetchone()
                        existing = existing_row[0] if existing_row else ""
                        combined = f"{existing}{'' if existing.endswith(chr(10)) or not existing else chr(10)}{content}" if append and existing else content
                        combined = self._tail_text(combined, max_lines=max_lines)
                        cur.execute(
                            """
                            INSERT INTO drone_log_files (source_id, path, content, modified_at, received_at)
                            VALUES (%s, %s, %s, %s, now())
                            ON CONFLICT (source_id, path) DO UPDATE SET
                                content = EXCLUDED.content,
                                modified_at = EXCLUDED.modified_at,
                                received_at = EXCLUDED.received_at
                            """,
                            (source_id, path, combined, modified_at),
                        )

    def store_device_emulator_configs(self, internal_device_id: str, payload: dict, max_versions: int = 1) -> None:
        if not self.url or not internal_device_id or not isinstance(payload, dict):
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        configs = payload.get("configs") if isinstance(payload.get("configs"), list) else []
        with conn:
            with conn.cursor() as cur:
                for item in configs:
                    if not isinstance(item, dict):
                        continue
                    relative_path = str(item.get("relative_path") or item.get("path") or "").strip()
                    if not relative_path or _is_excluded_emulator_config_path(relative_path):
                        continue
                    content = str(item.get("content") or "")
                    md5 = str(item.get("md5") or "") or None
                    fingerprint = str(item.get("fingerprint") or md5 or "") or None
                    root = str(item.get("root") or "") or None
                    try:
                        size_bytes = int(item.get("size")) if item.get("size") is not None else None
                    except (TypeError, ValueError):
                        size_bytes = None
                    cur.execute(
                        """
                        INSERT INTO drone_emulator_configs
                            (drone_id, root, relative_path, current_content, md5, fingerprint, size_bytes, truncated, error, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (drone_id, relative_path) DO UPDATE SET
                            root = EXCLUDED.root,
                            current_content = EXCLUDED.current_content,
                            md5 = EXCLUDED.md5,
                            fingerprint = EXCLUDED.fingerprint,
                            size_bytes = EXCLUDED.size_bytes,
                            truncated = EXCLUDED.truncated,
                            error = EXCLUDED.error,
                            updated_at = EXCLUDED.updated_at
                        RETURNING id
                        """,
                        (
                            internal_device_id,
                            root,
                            relative_path,
                            content,
                            md5,
                            fingerprint,
                            size_bytes,
                            bool(item.get("truncated")),
                            str(item.get("error") or "") or None,
                        ),
                    )
                    config_id = cur.fetchone()[0]
                    cur.execute(
                        """
                        SELECT 1
                        FROM drone_emulator_config_versions
                        WHERE config_id = %s AND COALESCE(fingerprint, '') = COALESCE(%s, '') AND content = %s
                        LIMIT 1
                        """,
                        (config_id, fingerprint, content),
                    )
                    if cur.fetchone() is None:
                        cur.execute(
                            """
                            INSERT INTO drone_emulator_config_versions (config_id, content, md5, fingerprint, received_at)
                            VALUES (%s, %s, %s, %s, now())
                            """,
                            (config_id, content, md5, fingerprint),
                        )
                    cur.execute(
                        """
                        DELETE FROM drone_emulator_config_versions
                        WHERE config_id = %s
                          AND id NOT IN (
                              SELECT id
                              FROM drone_emulator_config_versions
                              WHERE config_id = %s
                              ORDER BY received_at DESC, id DESC
                              LIMIT %s
                          )
                        """,
                        (config_id, config_id, max(1, int(max_versions or 1))),
                    )

    def get_device_log_sources(self, internal_device_id: str, line_limit: int = 10) -> dict:
        payload = {"type": "log_sources", "logs": []}
        if not self.url or not internal_device_id:
            return payload
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return payload
        line_limit = max(1, min(100, int(line_limit or 10)))
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.source, f.path, f.content, f.modified_at, f.received_at
                    FROM drone_log_sources s
                    JOIN drone_log_files f ON f.source_id = s.id
                    WHERE s.drone_id = %s
                    ORDER BY s.source, f.received_at DESC, f.id DESC
                    """,
                    (internal_device_id,),
                )
                by_source = {}
                for source, path, content, modified_at, received_at in cur.fetchall():
                    entry = by_source.setdefault(str(source), {"source": str(source), "files": []})
                    entry["files"].append({
                        "path": path,
                        "content": self._tail_text(content or "", max_lines=line_limit),
                        "modified_at": modified_at,
                        "received_at": received_at,
                    })
                payload["logs"] = list(by_source.values())
        return payload

    def get_device_gameplay_sessions(self, internal_device_id: str, system_name: Optional[str] = None) -> list[dict]:
        if not self.url or not internal_device_id:
            return []
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return []
        with conn:
            with conn.cursor() as cur:
                parameters = [internal_device_id]
                system_filter = ""
                if system_name:
                    system_filter = " AND system_name = %s"
                    parameters.append(system_name)
                cur.execute(
                    f"""
                    SELECT id, system_name, game_name, rom_path, rom_fingerprint, played_at, duration_seconds, received_at
                    FROM gameplay_sessions
                    WHERE drone_id = %s{system_filter}
                    ORDER BY played_at DESC NULLS LAST, received_at DESC, id DESC
                    """,
                    parameters,
                )
                return [
                    {
                        "id": gameplay_id,
                        "system_name": row_system,
                        "game_name": game_name,
                        "rom_path": rom_path,
                        "rom_fingerprint": rom_fingerprint,
                        "played_at": played_at,
                        "duration_seconds": duration_seconds,
                        "received_at": received_at,
                    }
                    for gameplay_id, row_system, game_name, rom_path, rom_fingerprint, played_at, duration_seconds, received_at
                    in cur.fetchall()
                ]

    def get_device_emulator_configs(self, internal_device_id: str, max_versions: int = 1) -> dict:
        payload = {"type": "emulator_configs", "configs": []}
        if not self.url or not internal_device_id:
            return payload
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return payload
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, root, relative_path, current_content, md5, fingerprint, size_bytes, truncated, error, updated_at
                    FROM drone_emulator_configs
                    WHERE drone_id = %s
                      AND lower(relative_path) <> 'log'
                      AND lower(relative_path) <> 'logs'
                      AND lower(relative_path) NOT LIKE 'log/%%'
                      AND lower(relative_path) NOT LIKE 'logs/%%'
                      AND lower(relative_path) NOT LIKE '%%/log/%%'
                      AND lower(relative_path) NOT LIKE '%%/logs/%%'
                    ORDER BY lower(relative_path)
                    """,
                    (internal_device_id,),
                )
                for config_id, root, relative_path, content, md5, fingerprint, size_bytes, truncated, error, updated_at in cur.fetchall():
                    cur.execute(
                        """
                        SELECT content, md5, fingerprint, received_at
                        FROM drone_emulator_config_versions
                        WHERE config_id = %s
                        ORDER BY received_at DESC, id DESC
                        LIMIT %s
                        """,
                        (config_id, max(1, int(max_versions or 1))),
                    )
                    versions = [
                        {
                            "content": version_content,
                            "md5": version_md5,
                            "fingerprint": version_fingerprint,
                            "collected_at": received_at,
                        }
                        for version_content, version_md5, version_fingerprint, received_at in cur.fetchall()
                    ]
                    payload["configs"].append(
                        {
                            "root": root,
                            "relative_path": relative_path,
                            "content": content,
                            "md5": md5,
                            "fingerprint": fingerprint,
                            "size": size_bytes,
                            "truncated": bool(truncated),
                            "error": error,
                            "collected_at": updated_at,
                            "versions": versions,
                        }
                    )
        return payload

    def load_app_state(self) -> Optional[dict]:
        if not self.url:
            return None
        if not _persist_json_app_state_enabled():
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
                state = _decode_state(row[0])
                return state if isinstance(state, dict) else None

    def load_admin_overview_state(self) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        state = {
            "users": {},
            "user_by_email": {},
            "swarms": {},
            "swarm_memberships": {},
            "devices": {},
            "user_devices": {},
        }
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id, u.email, u.password_hash, u.email_verified, u.is_active, u.auth_provider,
                           p.username, p.full_name, p.avatar_data_url, u.created_at,
                           ns.notify_slack, ns.notify_discord, ns.notify_email,
                           ns.slack_webhook, ns.discord_webhook, ns.email_address
                    FROM users u
                    LEFT JOIN user_profiles p ON p.user_id = u.id
                    LEFT JOIN user_notification_settings ns ON ns.user_id = u.id
                    """
                )
                for (
                    user_id, email, password_hash, email_verified, is_active, auth_provider,
                    username, full_name, avatar_data_url, created_at,
                    notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address,
                ) in cur.fetchall():
                    if not user_id or not email:
                        continue
                    state["users"][user_id] = {
                        "id": user_id,
                        "email": email,
                        "password": password_hash,
                        "email_verified": bool(email_verified),
                        "is_active": bool(is_active),
                        "auth_provider": auth_provider or "password",
                        "username": username,
                        "full_name": full_name,
                        "avatar_data_url": avatar_data_url,
                        "created_at": created_at,
                        # Notification settings must be present here: the digest delivery job
                        # hydrates recipients from this overview state and should_notify_user()
                        # treats a missing notification_settings as "all channels off".
                        "notification_settings": {
                            "notify_slack": bool(notify_slack),
                            "notify_discord": bool(notify_discord),
                            "notify_email": True if notify_email is None else bool(notify_email),
                            "slack_webhook": slack_webhook or "",
                            "discord_webhook": discord_webhook or "",
                            "email_address": email_address or email,
                            "types": {},
                        },
                    }
                    state["user_by_email"][email] = user_id
                    state["user_devices"].setdefault(user_id, [])

                if state["users"]:
                    cur.execute(
                        "SELECT user_id, event_type, enabled FROM user_notification_type_settings WHERE user_id = ANY(%s)",
                        (list(state["users"]),),
                    )
                    for user_id, event_type, enabled in cur.fetchall():
                        user = state["users"].get(user_id)
                        if user:
                            user["notification_settings"].setdefault("types", {})[event_type] = bool(enabled)

                cur.execute("SELECT id, owner_user_id, name, is_public, created_at FROM swarms")
                for swarm_id, owner_id, name, is_public, created_at in cur.fetchall():
                    state["swarms"][swarm_id] = {
                        "id": swarm_id,
                        "owner_id": owner_id,
                        "name": name,
                        "is_public": bool(is_public),
                        "created_at": created_at,
                    }

                cur.execute("SELECT swarm_id, user_id, role, created_at FROM swarm_memberships")
                for swarm_id, user_id, role, created_at in cur.fetchall():
                    state["swarm_memberships"].setdefault(swarm_id, {})[user_id] = {
                        "user_id": user_id,
                        "role": role,
                        "created_at": created_at,
                    }

                cur.execute(
                    """
                    SELECT d.id, d.device_id, d.device_name, d.user_id, d.swarm_id,
                           d.approval_status, d.swarm_connected, d.registered_at, d.last_seen,
                           n.reachable_url, n.public_resolvable, n.public_ip, n.api_port, n.scheme, n.checked_at
                    FROM drones d
                    LEFT JOIN drone_network_state n ON n.drone_id = d.id
                    WHERE d.removed_at IS NULL
                    """
                )
                for (
                    internal_id, device_id, device_name, user_id, swarm_id, approval_status,
                    swarm_connected, registered_at, last_seen, reachable_url,
                    public_resolvable, public_ip, api_port, scheme, checked_at,
                ) in cur.fetchall():
                    device = {
                        "id": internal_id,
                        "device_id": device_id,
                        "device_name": device_name,
                        "user_id": user_id,
                        "swarm_id": swarm_id,
                        "approval_status": approval_status or "approved",
                        "swarm_connected": bool(swarm_connected),
                        "registered_at": registered_at,
                        "last_seen": last_seen,
                        "reachable_url": reachable_url,
                        # Carry probe-owned reachability so a full-state mirror round-trip
                        # (_mirror_device_details) cannot silently reset public_resolvable.
                        "api_port": api_port,
                        "scheme": scheme,
                        "public_reachability": {
                            "resolvable": bool(public_resolvable),
                            "public_ip": public_ip,
                            "api_port": api_port,
                            "checked_at": checked_at,
                        },
                    }
                    state["devices"][internal_id] = device
                    state["user_devices"].setdefault(user_id, []).append(internal_id)
        return state

    def claim_pending_notifications(self, notification_ids: Iterable[str], limit: int = 0) -> Optional[dict[str, datetime]]:
        ids = [str(item) for item in notification_ids if item]
        if not self.url:
            return None
        if not ids:
            return {}
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return None
        row_limit = max(1, min(len(ids), int(limit or len(ids))))
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH selected AS (
                        SELECT id
                        FROM notifications
                        WHERE id = ANY(%s)
                          AND delivery_pending IS TRUE
                          AND delivery_completed_at IS NULL
                        ORDER BY created_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE notifications AS n
                    SET delivery_pending = false,
                        delivery_completed_at = now()
                    FROM selected
                    WHERE n.id = selected.id
                    RETURNING n.id, n.delivery_completed_at
                    """,
                    (ids, row_limit),
                )
                return {notification_id: completed_at for notification_id, completed_at in cur.fetchall()}

    def insert_swarm_notification(
        self,
        swarm_id: str,
        event_type: str,
        title: str,
        message: str,
        details: Optional[dict] = None,
        delivery_pending: bool = True,
        notification_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> Optional[str]:
        """Insert a single swarm notification straight into Postgres (lean path).

        Used by stateless scheduled jobs (e.g. the reachability probe) that must not
        round-trip the whole in-memory app state. Defaults to delivery_pending so the
        digest job picks it up; the in-app notification is visible immediately.

        A caller may supply ``notification_id`` so the row stays idempotent with a
        later full-state mirror that carries the same in-memory entry id.
        """
        if not self.url or not swarm_id:
            return None
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return None
        notification_id = str(notification_id or uuid.uuid4())
        details = details if isinstance(details, dict) else {}
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO notifications (id, swarm_id, event_type, title, message, created_at, delivery_pending)
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (notification_id, swarm_id, event_type, title or "", message or "", self._dt(created_at), bool(delivery_pending)),
                )
                for key, value in details.items():
                    cur.execute(
                        "INSERT INTO notification_fields (notification_id, field_name, field_value) "
                        "VALUES (%s, %s, %s) ON CONFLICT (notification_id, field_name) DO UPDATE SET field_value = EXCLUDED.field_value",
                        (notification_id, str(key), self._json(value)),
                    )
        return notification_id

    def load_pending_notifications(self, limit: int = 500) -> Optional[list[dict]]:
        """Return notifications still awaiting delivery, with their detail fields.

        This is the authoritative source for the digest delivery job in stateless
        runtimes (Lambda), where the in-memory notification store is never hydrated.
        """
        if not self.url:
            return None
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, swarm_id, event_type, title, message, actor_user_id, created_at
                    FROM notifications
                    WHERE delivery_pending IS TRUE AND delivery_completed_at IS NULL
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (max(1, int(limit)),),
                )
                by_id: dict = {}
                result: list[dict] = []
                for notification_id, swarm_id, event_type, title, message, actor_user_id, created_at in cur.fetchall():
                    entry = {
                        "id": notification_id,
                        "swarm_id": swarm_id,
                        "event_type": event_type,
                        "title": title,
                        "message": message,
                        "actor_user_id": actor_user_id,
                        "created_at": created_at,
                        "details": {},
                        "read_by": {},
                        "dismissed_by": {},
                        "delivery_pending": True,
                        "delivery_completed_at": None,
                    }
                    by_id[notification_id] = entry
                    result.append(entry)
                if by_id:
                    cur.execute(
                        "SELECT notification_id, field_name, field_value FROM notification_fields WHERE notification_id = ANY(%s)",
                        (list(by_id),),
                    )
                    for notification_id, field_name, field_value in cur.fetchall():
                        entry = by_id.get(notification_id)
                        if not entry or not field_name:
                            continue
                        try:
                            value = _decode_state(json.loads(field_value)) if field_value is not None else None
                        except (TypeError, ValueError, json.JSONDecodeError):
                            value = field_value
                        entry["details"][str(field_name)] = value
                    # Per-recipient read state so the digest can skip notifications a
                    # user has already read in-app (aggregation "unread" filter).
                    cur.execute(
                        """
                        SELECT notification_id, user_id, read_at
                        FROM notification_recipients
                        WHERE notification_id = ANY(%s) AND read_at IS NOT NULL
                        """,
                        (list(by_id),),
                    )
                    for notification_id, user_id, read_at in cur.fetchall():
                        entry = by_id.get(notification_id)
                        if entry and user_id:
                            entry.setdefault("read_by", {})[user_id] = read_at
                return result

    def _load_relational_state(self, cur) -> dict:
        """Rehydrate core app state from normalized tables for fresh workers."""
        state = {
            "users": {},
            "user_by_email": {},
            "user_devices": {},
            "integration_tokens": {},
            "swarms": {},
            "swarm_memberships": {},
            "devices": {},
            "device_admin_claims": {},
            "device_actions": {},
            "gamelogs": {},
            "speed_samples": {},
            "device_events": {},
            "peer_checks": {},
            "download_states": {},
            "rom_sync_activity": {},
            "notifications": {},
            "pending_drone_connections": {},
        }
        try:
            cur.execute(
                """
                SELECT u.id, u.email, u.password_hash, u.email_verified, u.is_active, u.auth_provider, u.created_at,
                       p.username, p.full_name, p.avatar_data_url,
                       fs.auto_sync_roms,
                       ns.notify_slack, ns.notify_discord, ns.notify_email, ns.slack_webhook, ns.discord_webhook, ns.email_address
                FROM users u
                LEFT JOIN user_profiles p ON p.user_id = u.id
                LEFT JOIN user_fleet_settings fs ON fs.user_id = u.id
                LEFT JOIN user_notification_settings ns ON ns.user_id = u.id
                """
            )
        except Exception:
            return {}
        for row in cur.fetchall():
            (
                user_id, email, password_hash, email_verified, is_active, auth_provider, created_at,
                username, full_name, avatar_data_url,
                auto_sync_roms,
                notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address,
            ) = row
            if not user_id or not email:
                continue
            user = {
                "id": user_id,
                "email": email,
                "password": password_hash,
                "email_verified": bool(email_verified),
                "is_active": bool(is_active),
                "auth_provider": auth_provider or "password",
                "username": username,
                "full_name": full_name,
                "avatar_data_url": avatar_data_url,
                "fleet_settings": {"auto_sync_roms": True if auto_sync_roms is None else bool(auto_sync_roms)},
                "notification_settings": {
                    "notify_slack": bool(notify_slack),
                    "notify_discord": bool(notify_discord),
                    "notify_email": True if notify_email is None else bool(notify_email),
                    "slack_webhook": slack_webhook or "",
                    "discord_webhook": discord_webhook or "",
                    "email_address": email_address or email,
                    "types": {},
                },
                "created_at": created_at,
            }
            state["users"][user_id] = user
            state["user_by_email"][email] = user_id
            state["user_devices"].setdefault(user_id, [])

        active_user_ids = list(state["users"])
        cur.execute(
            "SELECT user_id, event_type, enabled FROM user_notification_type_settings WHERE user_id = ANY(%s)",
            (active_user_ids,),
        )
        for user_id, event_type, enabled in cur.fetchall():
            user = state["users"].get(user_id)
            if user:
                user["notification_settings"].setdefault("types", {})[event_type] = bool(enabled)

        cur.execute("SELECT id, owner_user_id, name, created_at FROM swarms")
        for swarm_id, owner_id, name, created_at in cur.fetchall():
            state["swarms"][swarm_id] = {"id": swarm_id, "owner_id": owner_id, "name": name, "created_at": created_at}
        active_swarm_ids = list(state["swarms"])
        cur.execute(
            "SELECT swarm_id, user_id, role, created_at FROM swarm_memberships WHERE swarm_id = ANY(%s)",
            (active_swarm_ids,),
        )
        for swarm_id, user_id, role, created_at in cur.fetchall():
            state["swarm_memberships"].setdefault(swarm_id, {})[user_id] = {"user_id": user_id, "role": role, "created_at": created_at}

        cur.execute(
            """
            SELECT id, user_id, label, token_hash, bound_device_id, bound_fingerprint, created_at, last_used_at, revoked_at
            FROM integration_tokens
            WHERE user_id = ANY(%s)
            """,
            (active_user_ids,),
        )
        for token_id, user_id, label, token_hash, bound_device_id, bound_fingerprint, created_at, last_used_at, revoked_at in cur.fetchall():
            state["integration_tokens"].setdefault(user_id, []).append({
                "id": token_id,
                "label": label,
                "token_hash": token_hash,
                "bound_device_id": bound_device_id,
                "bound_device_fingerprint": bound_fingerprint,
                "created_at": created_at,
                "last_used_at": last_used_at,
                "revoked_at": revoked_at,
            })

        cur.execute(
            """
            SELECT d.id, d.device_id, d.device_name, d.user_id, d.swarm_id, d.approval_status, d.swarm_connected,
                   d.authorization_token_id, d.drone_token_hash, d.registered_at, d.last_seen,
                   d.rom_inventory_fingerprint, d.drone_rom_inventory_fingerprint,
                   d.rom_inventory_fingerprint_algorithm, d.rom_inventory_fingerprint_at,
                   d.drone_rom_inventory_fingerprint_at,
                   d.romset_files_thumbprint, d.bios_files_thumbprint,
                   n.api_port, n.scheme, n.reachable_url, n.public_resolvable, n.public_ip, n.checked_at,
                   s.hostname, s.model, s.system_name, s.architecture, s.cpu_model, s.cpu_cores, s.cpu_threads,
                   s.cpu_max_frequency, s.memory_available, s.memory_total, s.batocera_version,
                   s.screen_mode, s.audio_volume, s.container,
                   s.idle_volume_enabled, s.idle_volume_idle_minutes, s.idle_volume_target
            FROM drones d
            LEFT JOIN drone_network_state n ON n.drone_id = d.id
            LEFT JOIN drone_system_info s ON s.drone_id = d.id
            WHERE d.removed_at IS NULL
            """
        )
        for row in cur.fetchall():
            (
                internal_id, device_id, device_name, user_id, swarm_id, approval_status, swarm_connected,
                authorization_token_id, drone_token_hash, registered_at, last_seen,
                rom_inventory_fingerprint, drone_rom_inventory_fingerprint,
                rom_inventory_fingerprint_algorithm, rom_inventory_fingerprint_at,
                drone_rom_inventory_fingerprint_at,
                romset_files_thumbprint, bios_files_thumbprint,
                api_port, scheme, reachable_url, public_resolvable, public_ip, checked_at,
                hostname, model, system_name, architecture, cpu_model, cpu_cores, cpu_threads,
                cpu_max_frequency, memory_available, memory_total, batocera_version,
                screen_mode, audio_volume, container,
                idle_volume_enabled, idle_volume_idle_minutes, idle_volume_target,
            ) = row
            device = {
                "id": internal_id,
                "device_id": device_id,
                "device_name": device_name,
                "user_id": user_id,
                "swarm_id": swarm_id,
                "approval_status": approval_status or "approved",
                "swarm_connected": bool(swarm_connected),
                "authorization_token_id": authorization_token_id,
                "drone_token_hash": drone_token_hash,
                "registered_at": registered_at,
                "last_seen": last_seen,
                "rom_inventory_fingerprint": rom_inventory_fingerprint,
                "drone_rom_inventory_fingerprint": drone_rom_inventory_fingerprint,
                "rom_inventory_fingerprint_algorithm": rom_inventory_fingerprint_algorithm,
                "rom_inventory_fingerprint_at": rom_inventory_fingerprint_at,
                "drone_rom_inventory_fingerprint_at": drone_rom_inventory_fingerprint_at,
                "romset_files_thumbprint": romset_files_thumbprint,
                "bios_files_thumbprint": bios_files_thumbprint,
                "api_port": api_port,
                "scheme": scheme or "https",
                "reachable_url": reachable_url,
                "network": {"public_ip": public_ip} if public_ip else {},
                "public_reachability": {"resolvable": bool(public_resolvable), "public_ip": public_ip, "checked_at": checked_at},
                "system_info": {
                    "hostname": hostname,
                    "model": model,
                    "system": system_name,
                    "architecture": architecture,
                    "cpu_model": cpu_model,
                    "cpu_cores": cpu_cores,
                    "cpu_threads": cpu_threads,
                    "cpu_max_frequency": cpu_max_frequency,
                    "memory_available": memory_available,
                    "memory_total": memory_total,
                    "batocera_version": batocera_version,
                    "screen_mode": screen_mode,
                    "audio_volume": audio_volume,
                    "idle_volume_automation": _idle_volume_automation_dict(
                        idle_volume_enabled, idle_volume_idle_minutes, idle_volume_target
                    ),
                    "container": container,
                },
            }
            state["devices"][internal_id] = device
            state["user_devices"].setdefault(user_id, []).append(internal_id)

        active_drone_ids = list(state["devices"])
        history_limit = _env_int("OVERMIND_RELATIONAL_HISTORY_ROWS_PER_DRONE", 200)
        action_limit = _env_int("OVERMIND_RELATIONAL_ACTION_ROWS_PER_DRONE", 100)
        notification_limit = _env_int("OVERMIND_RELATIONAL_NOTIFICATIONS_PER_SWARM", 500)

        if active_drone_ids:
            cur.execute(
                """
                SELECT drone_id, address_type, address
                FROM drone_network_addresses
                WHERE drone_id = ANY(%s) AND address_type IN ('ipv4', 'ipv6')
                ORDER BY drone_id, address_type, observed_at
                """,
                (active_drone_ids,),
            )
            for drone_id, address_type, address in cur.fetchall():
                device = state["devices"].get(drone_id)
                if not device:
                    continue
                network = device.setdefault("network", {})
                addr_list = network.setdefault(address_type, [])
                if address not in addr_list:
                    addr_list.append(address)

        cur.execute(
            """
            SELECT c.drone_id, c.status, c.fingerprint, c.sha256_fingerprint, c.public_certificate,
                   c.subject, c.issuer, c.valid_from, c.valid_until, c.serial_number, c.overmind_signed_at,
                   c.updated_at
            FROM drone_certificates c
            WHERE c.drone_id = ANY(%s)
            """,
            (active_drone_ids,),
        )
        for drone_id, cert_status, fingerprint, sha256_fingerprint, public_certificate, subject, issuer, valid_from, valid_until, serial_number, overmind_signed_at, updated_at in cur.fetchall():
            device = state["devices"].get(drone_id)
            if not device:
                continue
            device["certificate"] = {
                "status": cert_status,
                "fingerprint": fingerprint,
                "sha256_fingerprint": sha256_fingerprint,
                "public_certificate": public_certificate,
                "certificate_pem": public_certificate,
                "subject": subject,
                "issuer": issuer,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "serial_number": serial_number,
                "overmind_signed_at": overmind_signed_at,
                "updated_at": updated_at,
                "san": [],
            }

        cur.execute(
            """
            SELECT s.drone_id, s.san
            FROM drone_certificate_sans s
            WHERE s.drone_id = ANY(%s)
            ORDER BY s.san
            """,
            (active_drone_ids,),
        )
        for drone_id, san in cur.fetchall():
            cert = (state["devices"].get(drone_id) or {}).get("certificate")
            if isinstance(cert, dict) and san:
                cert.setdefault("san", []).append(san)

        cur.execute("SELECT drone_id, user_id FROM device_admin_claims WHERE drone_id = ANY(%s)", (active_drone_ids,))
        for drone_id, user_id in cur.fetchall():
            state["device_admin_claims"].setdefault(drone_id, []).append(user_id)

        cur.execute(
            """
            SELECT a.id, a.drone_id, d.device_id, a.action, a.status, a.created_at, a.claimed_at, a.completed_at, a.message
            FROM (
                SELECT a.*, row_number() OVER (PARTITION BY a.drone_id ORDER BY a.created_at DESC, a.id DESC) AS rn
                FROM drone_actions a
                WHERE a.drone_id = ANY(%s)
                  AND (a.status IN ('pending', 'claimed') OR a.created_at >= now() - interval '30 days')
            ) a
            JOIN drones d ON d.id = a.drone_id
            WHERE a.rn <= %s
            ORDER BY a.created_at ASC
            """,
            (active_drone_ids, action_limit),
        )
        for action_id, drone_id, device_id, action_type, action_status, created_at, claimed_at, completed_at, message in cur.fetchall():
            if not action_id or not drone_id:
                continue
            state["device_actions"].setdefault(drone_id, []).append({
                "id": action_id,
                "device_id": device_id,
                "action": action_type,
                "status": action_status or "pending",
                "payload": {},
                "created_at": created_at,
                "claimed_at": claimed_at,
                "completed_at": completed_at,
                "message": message,
                "result": None,
                "result_received_at": None,
            })

        actions_by_id = {
            action.get("id"): action
            for rows in state["device_actions"].values()
            for action in rows
            if isinstance(action, dict)
        }
        cur.execute(
            """
            SELECT p.action_id, p.parameter_name, p.parameter_value
            FROM drone_action_parameters p
            JOIN drone_actions a ON a.id = p.action_id
            WHERE a.drone_id = ANY(%s)
              AND p.action_id = ANY(%s)
            """,
            (active_drone_ids, list(actions_by_id) or [""]),
        )
        for action_id, parameter_name, parameter_value in cur.fetchall():
            action = actions_by_id.get(action_id)
            if not action or not parameter_name:
                continue
            try:
                value = _decode_state(json.loads(parameter_value)) if parameter_value is not None else None
            except (TypeError, ValueError, json.JSONDecodeError):
                value = parameter_value
            action.setdefault("payload", {})[str(parameter_name)] = value

        cur.execute(
            """
            SELECT g.id, g.drone_id, g.system_name, g.game_name, g.rom_path, g.rom_fingerprint,
                   g.played_at, g.duration_seconds, g.received_at
            FROM (
                SELECT g.*, row_number() OVER (PARTITION BY g.drone_id ORDER BY g.played_at DESC NULLS LAST, g.received_at DESC, g.id DESC) AS rn
                FROM gameplay_sessions g
                WHERE g.drone_id = ANY(%s)
            ) g
            WHERE g.rn <= %s
            ORDER BY g.played_at ASC NULLS LAST, g.received_at ASC
            """,
            (active_drone_ids, history_limit),
        )
        for gameplay_id, drone_id, system_name, game_name, rom_path, rom_fingerprint, played_at, duration_seconds, received_at in cur.fetchall():
            state["gamelogs"].setdefault(drone_id, []).append({
                "id": gameplay_id,
                "system_name": system_name,
                "game_name": game_name,
                "name": game_name,
                "rom_path": rom_path,
                "rom_fingerprint": rom_fingerprint,
                "played_at": played_at,
                "duration_seconds": duration_seconds,
                "received_at": received_at,
            })

        cur.execute(
            """
            SELECT s.drone_id, s.upload_mbps, s.download_mbps, s.latency_ms, s.measured_at, s.received_at
            FROM (
                SELECT s.*, row_number() OVER (PARTITION BY s.drone_id ORDER BY s.received_at DESC, s.id DESC) AS rn
                FROM drone_speed_samples s
                WHERE s.drone_id = ANY(%s)
            ) s
            WHERE s.rn <= %s
            ORDER BY s.received_at ASC, s.id ASC
            """,
            (active_drone_ids, history_limit),
        )
        for drone_id, upload_mbps, download_mbps, latency_ms, measured_at, received_at in cur.fetchall():
            state["speed_samples"].setdefault(drone_id, []).append({
                "upload_mbps": upload_mbps,
                "download_mbps": download_mbps,
                "latency_ms": latency_ms,
                "measured_at": measured_at,
                "sampled_at": received_at,
            })

        cur.execute(
            """
            SELECT e.id, e.drone_id, e.event_type, e.severity, e.message, e.occurred_at, e.received_at
            FROM (
                SELECT e.*, row_number() OVER (PARTITION BY e.drone_id ORDER BY e.received_at DESC, e.id DESC) AS rn
                FROM drone_events e
                WHERE e.drone_id = ANY(%s)
            ) e
            WHERE e.rn <= %s
            ORDER BY e.received_at ASC, e.id ASC
            """,
            (active_drone_ids, history_limit),
        )
        event_entries = {}
        for event_id, drone_id, event_type, severity, message, occurred_at, received_at in cur.fetchall():
            entry = {
                "id": str(event_id),
                "event_type": event_type,
                "severity": severity,
                "message": message,
                "timestamp": occurred_at,
                "occurred_at": occurred_at,
                "received_at": received_at,
                "metadata": {},
            }
            event_entries[event_id] = entry
            state["device_events"].setdefault(drone_id, []).append(entry)
        if event_entries:
            cur.execute(
                """
                SELECT event_id, field_name, field_value
                FROM drone_event_fields
                WHERE event_id = ANY(%s)
                """,
                (list(event_entries),),
            )
            for event_id, field_name, field_value in cur.fetchall():
                entry = event_entries.get(event_id)
                if not entry or not field_name:
                    continue
                try:
                    value = _decode_state(json.loads(field_value)) if field_value is not None else None
                except (TypeError, ValueError, json.JSONDecodeError):
                    value = field_value
                entry.setdefault("metadata", {})[str(field_name)] = value

        cur.execute(
            """
            SELECT c.id, c.source_drone_id, sd.device_id, c.target_drone_id, c.target_address,
                   c.status, c.latency_ms, c.checked_at, c.error, c.received_at
            FROM (
                SELECT c.*, row_number() OVER (PARTITION BY c.source_drone_id ORDER BY c.received_at DESC, c.id DESC) AS rn
                FROM drone_peer_checks c
                WHERE c.source_drone_id = ANY(%s)
            ) c
            JOIN drones sd ON sd.id = c.source_drone_id
            WHERE c.rn <= %s
            ORDER BY c.received_at ASC, c.id ASC
            """,
            (active_drone_ids, history_limit),
        )
        for check_id, source_internal_id, source_device_id, target_drone_id, target_address, check_status, latency_ms, checked_at, error, received_at in cur.fetchall():
            state["peer_checks"].setdefault(source_internal_id, []).append({
                "id": str(check_id),
                "source_drone_id": source_device_id,
                "target_drone_id": target_drone_id,
                "target_address": target_address,
                "status": check_status or "fail",
                "latency_ms": latency_ms,
                "failure_reason": error,
                "checked_at": checked_at,
                "received_at": received_at,
            })

        cur.execute(
            """
            SELECT target_drone_id, id, reported_at, concurrency_scope, active_limit
            FROM (
                SELECT s.*, row_number() OVER (PARTITION BY s.target_drone_id ORDER BY s.reported_at DESC, s.id DESC) AS rn
                FROM download_snapshots s
                WHERE s.target_drone_id = ANY(%s)
            ) ranked
            WHERE rn = 1
            """,
            (active_drone_ids,),
        )
        latest_download_snapshots = {}
        for target_internal_id, snapshot_id, reported_at, concurrency_scope, active_limit in cur.fetchall():
            latest_download_snapshots[snapshot_id] = target_internal_id
            state["download_states"][target_internal_id] = {
                "target_drone_id": (state["devices"].get(target_internal_id) or {}).get("device_id"),
                "concurrency": {"scope": concurrency_scope or "target_drone", "active_limit": active_limit or 1},
                "active": [],
                "queued": [],
                "recent": [],
                "downloads": [],
                "received_at": reported_at,
            }
        if latest_download_snapshots:
            cur.execute(
                """
                SELECT snapshot_id, job_id, state_bucket, asset_type, status, source_drone_id, system_name,
                       file_path, rom_path, bios_name, artwork_type, file_size, downloaded_bytes,
                       percentage, transfer_speed_bps, queue_position, failure_reason
                FROM download_items
                WHERE snapshot_id = ANY(%s)
                ORDER BY id ASC
                """,
                (list(latest_download_snapshots),),
            )
            for snapshot_id, job_id, state_bucket, asset_type, item_status, source_drone_id, system_name, file_path, rom_path, bios_name, artwork_type, file_size, downloaded_bytes, percentage, transfer_speed_bps, queue_position, failure_reason in cur.fetchall():
                target_internal_id = latest_download_snapshots.get(snapshot_id)
                state_row = state["download_states"].get(target_internal_id)
                if not state_row or state_bucket not in {"active", "queued", "recent"}:
                    continue
                item = {
                    "job_id": job_id,
                    "asset_type": asset_type,
                    "status": item_status,
                    "source_drone_id": source_drone_id,
                    "system": system_name,
                    "file_path": file_path,
                    "relative_path": file_path,
                    "rom_path": rom_path,
                    "bios_name": bios_name,
                    "artwork_type": artwork_type,
                    "file_size": file_size,
                    "total_bytes": file_size,
                    "downloaded_bytes": downloaded_bytes,
                    "bytes_transferred": downloaded_bytes,
                    "percentage": percentage,
                    "transfer_speed_bps": transfer_speed_bps,
                    "queue_position": queue_position,
                    "failure_reason": failure_reason,
                }
                state_row[state_bucket].append(item)
                state_row["downloads"].append(item)

        cur.execute(
            """
            SELECT a.id, a.target_drone_id, td.device_id, a.source_drone_id, a.asset_type, a.action,
                   a.status, a.system_name, a.file_path, a.rom_fingerprint, a.bios_md5, a.artwork_type,
                   a.bytes_transferred, a.file_size, a.started_at, a.completed_at, a.failure_reason,
                   a.received_at
            FROM (
                SELECT a.*, row_number() OVER (PARTITION BY a.target_drone_id ORDER BY a.received_at DESC, a.id DESC) AS rn
                FROM sync_activity a
                WHERE a.target_drone_id = ANY(%s)
            ) a
            JOIN drones td ON td.id = a.target_drone_id
            WHERE a.rn <= %s
            ORDER BY a.received_at ASC
            """,
            (active_drone_ids, history_limit),
        )
        for sync_id, target_internal_id, target_device_id, source_drone_id, asset_type, action, activity_status, system_name, file_path, rom_fingerprint, bios_md5, artwork_type, bytes_transferred, file_size, started_at, completed_at, failure_reason, received_at in cur.fetchall():
            if not target_internal_id:
                continue
            state["rom_sync_activity"].setdefault(target_internal_id, []).append({
                "id": sync_id,
                "sync_id": sync_id,
                "asset_type": asset_type,
                "source_drone_id": source_drone_id,
                "target_drone_id": target_device_id,
                "system": system_name,
                "rom_name": file_path,
                "rom_path": file_path,
                "bios_name": file_path if asset_type == "bios" else None,
                "artwork_type": artwork_type,
                "relative_path": file_path,
                "action": action or "download",
                "status": activity_status or "pending",
                "bytes_transferred": bytes_transferred,
                "file_size": file_size,
                "rom_fingerprint": rom_fingerprint,
                "bios_md5": bios_md5,
                "download_started_at": started_at,
                "download_completed_at": completed_at,
                "started_at": started_at,
                "completed_at": completed_at,
                "failure_reason": failure_reason,
                "received_at": received_at,
            })

        cur.execute(
            """
            SELECT id, swarm_id, event_type, title, message, actor_user_id,
                   created_at, delivery_pending, delivery_completed_at
            FROM (
                SELECT n.*, row_number() OVER (PARTITION BY n.swarm_id ORDER BY n.created_at DESC, n.id DESC) AS rn
                FROM notifications n
                WHERE n.swarm_id = ANY(%s)
            ) n
            WHERE n.delivery_pending IS TRUE
               OR n.delivery_completed_at IS NULL
               OR n.rn <= %s
            ORDER BY created_at ASC
            """,
            (active_swarm_ids, notification_limit),
        )
        notifications_by_id = {}
        for notification_id, swarm_id, event_type, title, message, actor_user_id, created_at, delivery_pending, delivery_completed_at in cur.fetchall():
            entry = {
                "id": notification_id,
                "swarm_id": swarm_id,
                "event_type": event_type,
                "title": title,
                "message": message,
                "actor_user_id": actor_user_id,
                "created_at": created_at,
                "details": {},
                "read_by": {},
                "dismissed_by": {},
                "delivery_pending": bool(delivery_pending),
                "delivery_completed_at": delivery_completed_at,
            }
            notifications_by_id[notification_id] = entry
            state["notifications"].setdefault(swarm_id, []).append(entry)
        if notifications_by_id:
            cur.execute(
                """
                SELECT notification_id, field_name, field_value
                FROM notification_fields
                WHERE notification_id = ANY(%s)
                """,
                (list(notifications_by_id),),
            )
            for notification_id, field_name, field_value in cur.fetchall():
                entry = notifications_by_id.get(notification_id)
                if not entry or not field_name:
                    continue
                try:
                    value = _decode_state(json.loads(field_value)) if field_value is not None else None
                except (TypeError, ValueError, json.JSONDecodeError):
                    value = field_value
                entry.setdefault("details", {})[str(field_name)] = value
            cur.execute(
                """
                SELECT notification_id, user_id, read_at, dismissed_at
                FROM notification_recipients
                WHERE notification_id = ANY(%s)
                """,
                (list(notifications_by_id),),
            )
            for notification_id, user_id, read_at, dismissed_at in cur.fetchall():
                entry = notifications_by_id.get(notification_id)
                if not entry or not user_id:
                    continue
                if read_at:
                    entry.setdefault("read_by", {})[user_id] = read_at
                if dismissed_at:
                    entry.setdefault("dismissed_by", {})[user_id] = dismissed_at

        cur.execute(
            """
            SELECT device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id,
                   drone_token_hash, recovery_reason, requested_at, status
            FROM pending_drone_connections
            WHERE user_id = ANY(%s) OR swarm_id = ANY(%s)
            """,
            (active_user_ids, active_swarm_ids),
        )
        for device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id, drone_token_hash, recovery_reason, requested_at, status in cur.fetchall():
            state["pending_drone_connections"][device_id] = {
                "id": device_id,
                "user_id": user_id,
                "swarm_id": swarm_id,
                "device_id": device_id,
                "device_name": device_name,
                "batocera_info": _decode_state(batocera_info) if isinstance(batocera_info, dict) else {},
                "authorization_token_id": authorization_token_id,
                "drone_token_hash": drone_token_hash,
                "recovery_reason": recovery_reason,
                "detected_at": requested_at,
                "last_seen": requested_at,
                "status": status,
            }
        return state

    def _device_from_row(self, row) -> Optional[dict]:
        if not row:
            return None
        (
            internal_id, device_id, device_name, user_id, swarm_id, approval_status, swarm_connected,
            authorization_token_id, drone_token_hash, registered_at, last_seen, removed_at,
            rom_inventory_fingerprint, drone_rom_inventory_fingerprint,
            rom_inventory_fingerprint_algorithm, rom_inventory_fingerprint_at,
            drone_rom_inventory_fingerprint_at,
            romset_files_thumbprint, bios_files_thumbprint,
            api_port, scheme, reachable_url, public_resolvable, public_ip, checked_at,
            edge_online, reflexive_endpoint,
            hostname, model, system_name, architecture, cpu_model, cpu_cores, cpu_threads,
            cpu_max_frequency, memory_available, memory_total, batocera_version,
            screen_mode, audio_volume, container,
            idle_volume_enabled, idle_volume_idle_minutes, idle_volume_target,
            cert_status, fingerprint, sha256_fingerprint, public_certificate, subject, issuer,
            valid_from, valid_until, serial_number, overmind_signed_at,
            auto_sync_enabled, auto_sync_systems, ipv4, ipv6, hostnames, macs, last_speed_sample,
        ) = row
        network = {
            "ipv4": list(ipv4 or []),
            "ipv6": list(ipv6 or []),
            "hostnames": list(hostnames or []),
            "mac_addresses": list(macs or []),
        }
        if public_ip:
            network["public_ip"] = public_ip
        certificate = None
        if public_certificate or fingerprint or sha256_fingerprint:
            certificate = {
                "status": cert_status,
                "fingerprint": fingerprint,
                "sha256_fingerprint": sha256_fingerprint,
                "public_certificate": public_certificate,
                "certificate_pem": public_certificate,
                "subject": subject,
                "issuer": issuer,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "serial_number": serial_number,
                "overmind_signed_at": overmind_signed_at,
                "san": [],
            }
        return {
            "id": internal_id,
            "device_id": device_id,
            "device_name": device_name,
            "user_id": user_id,
            "swarm_id": swarm_id,
            "approval_status": approval_status or "approved",
            "swarm_connected": bool(swarm_connected),
            "authorization_token_id": authorization_token_id,
            "drone_token_hash": drone_token_hash,
            "registered_at": registered_at,
            "last_seen": last_seen,
            "removed_at": removed_at,
            "rom_inventory_fingerprint": rom_inventory_fingerprint,
            "drone_rom_inventory_fingerprint": drone_rom_inventory_fingerprint,
            "rom_inventory_fingerprint_algorithm": rom_inventory_fingerprint_algorithm,
            "rom_inventory_fingerprint_at": rom_inventory_fingerprint_at,
            "drone_rom_inventory_fingerprint_at": drone_rom_inventory_fingerprint_at,
            "romset_files_thumbprint": romset_files_thumbprint,
            "bios_files_thumbprint": bios_files_thumbprint,
            "api_port": api_port,
            "scheme": scheme or "https",
            "reachable_url": reachable_url,
            "edge_online": bool(edge_online),
            "reflexive_endpoint": reflexive_endpoint,
            "network": network,
            "resolved_network": {"ipv4": list(ipv4 or []), "ipv6": list(ipv6 or [])},
            "public_reachability": {
                "resolvable": bool(public_resolvable),
                "public_ip": public_ip,
                "api_port": api_port,
                "checked_at": checked_at,
            },
            "system_info": {
                "hostname": hostname,
                "model": model,
                "system": system_name,
                "system_name": system_name,
                "architecture": architecture,
                "cpu_model": cpu_model,
                "cpu_cores": cpu_cores,
                "cpu_threads": cpu_threads,
                "cpu_max_frequency": cpu_max_frequency,
                "memory_available": memory_available,
                "memory_total": memory_total,
                "batocera_version": batocera_version,
                "screen_mode": screen_mode,
                "audio_volume": audio_volume,
                "idle_volume_automation": _idle_volume_automation_dict(
                    idle_volume_enabled, idle_volume_idle_minutes, idle_volume_target
                ),
                "container": container,
            },
            "batocera_info": {},
            "certificate": certificate,
            "auto_sync_policy": {"enabled": bool(auto_sync_enabled), "systems": list(auto_sync_systems or [])},
            "rom_systems": list(auto_sync_systems or []),
            "last_speed_sample": _decode_state(last_speed_sample) if isinstance(last_speed_sample, dict) else last_speed_sample,
        }

    def _force_schema_recheck(self) -> None:
        """Re-run pending migrations after schema drift is detected at read time."""
        self._ready = False
        try:
            self.ensure_schema()
        except Exception:
            logger.warning("Schema re-check after drift failed", exc_info=True)

    def _with_schema_self_heal(self, operation: str, fn):
        """Run a read query, recovering once from post-deploy schema drift.

        If ``fn`` fails only because the database is missing a column the code
        expects, re-run migrations and retry a single time. This prevents one
        column that has not migrated yet from blanking the entire fleet view —
        the read recovers automatically once the corrected migration lands.
        Any other error (and a still-failing retry) propagates unchanged.
        """
        try:
            return fn()
        except Exception as error:
            if not _is_missing_column_error(error):
                raise
            logger.warning(
                "Schema drift detected during %s (%s); re-running migrations and retrying once",
                operation,
                error,
            )
            self._force_schema_recheck()
            return fn()

    def _select_device_sql(self, where_clause: str) -> str:
        return f"""
            SELECT d.id, d.device_id, d.device_name, d.user_id, d.swarm_id, d.approval_status,
                   d.swarm_connected, d.authorization_token_id, d.drone_token_hash,
                   d.registered_at, d.last_seen, d.removed_at,
                   d.rom_inventory_fingerprint, d.drone_rom_inventory_fingerprint,
                   d.rom_inventory_fingerprint_algorithm, d.rom_inventory_fingerprint_at,
                   d.drone_rom_inventory_fingerprint_at,
                   d.romset_files_thumbprint, d.bios_files_thumbprint,
                   ns.api_port, ns.scheme, ns.reachable_url, ns.public_resolvable, ns.public_ip, ns.checked_at,
                   ns.edge_online, ns.reflexive_endpoint,
                   si.hostname, si.model, si.system_name, si.architecture, si.cpu_model, si.cpu_cores,
                   si.cpu_threads, si.cpu_max_frequency, si.memory_available, si.memory_total,
                   si.batocera_version, si.screen_mode, si.audio_volume, si.container,
                   si.idle_volume_enabled, si.idle_volume_idle_minutes, si.idle_volume_target,
                   dc.status, dc.fingerprint, dc.sha256_fingerprint, dc.public_certificate, dc.subject,
                   dc.issuer, dc.valid_from, dc.valid_until, dc.serial_number, dc.overmind_signed_at,
                   COALESCE(p.enabled, false),
                   COALESCE((
                       SELECT array_agg(system_name ORDER BY system_name)
                       FROM drone_auto_sync_policy_systems aps
                       WHERE aps.drone_id = d.id
                   ), ARRAY[]::text[]),
                   COALESCE((
                       SELECT array_agg(address ORDER BY address)
                       FROM drone_network_addresses a
                       WHERE a.drone_id = d.id AND a.address_type = 'ipv4'
                   ), ARRAY[]::text[]),
                   COALESCE((
                       SELECT array_agg(address ORDER BY address)
                       FROM drone_network_addresses a
                       WHERE a.drone_id = d.id AND a.address_type = 'ipv6'
                   ), ARRAY[]::text[]),
                   COALESCE((
                       SELECT array_agg(address ORDER BY address)
                       FROM drone_network_addresses a
                       WHERE a.drone_id = d.id AND a.address_type = 'hostname'
                   ), ARRAY[]::text[]),
                   COALESCE((
                       SELECT array_agg(address ORDER BY address)
                       FROM drone_network_addresses a
                       WHERE a.drone_id = d.id AND a.address_type = 'mac'
                   ), ARRAY[]::text[]),
                   (
                       SELECT jsonb_build_object(
                           'upload_mbps', s.upload_mbps,
                           'download_mbps', s.download_mbps,
                           'latency_ms', s.latency_ms,
                           'measured_at', s.measured_at,
                           'sampled_at', s.received_at
                       )
                       FROM drone_speed_samples s
                       WHERE s.drone_id = d.id
                       ORDER BY s.received_at DESC, s.id DESC
                       LIMIT 1
                   )
            FROM drones d
            LEFT JOIN drone_network_state ns ON ns.drone_id = d.id
            LEFT JOIN drone_system_info si ON si.drone_id = d.id
            LEFT JOIN drone_certificates dc ON dc.drone_id = d.id
            LEFT JOIN drone_auto_sync_policies p ON p.drone_id = d.id
            WHERE {where_clause}
        """

    def get_device(self, internal_id: str) -> Optional[dict]:
        def _query():
            conn = self._core_connection(ensure_schema=False)
            if conn is None:
                return None
            with conn:
                with conn.cursor() as cur:
                    cur.execute(self._select_device_sql("d.id = %s"), (internal_id,))
                    return self._device_from_row(cur.fetchone())
        return self._with_schema_self_heal("get_device", _query)

    def get_device_by_device_id(self, device_id: str) -> Optional[dict]:
        def _query():
            conn = self._core_connection(ensure_schema=False)
            if conn is None:
                return None
            with conn:
                with conn.cursor() as cur:
                    cur.execute(self._select_device_sql("d.device_id = %s"), (device_id,))
                    return self._device_from_row(cur.fetchone())
        return self._with_schema_self_heal("get_device_by_device_id", _query)

    def update_device_authorization(
        self,
        user_id: str,
        device_id: str,
        *,
        authorization_token_id: Optional[str],
        drone_token_hash: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE drones
                    SET authorization_token_id = %s,
                        drone_token_hash = COALESCE(%s, drone_token_hash),
                        device_name = COALESCE(%s, device_name)
                    WHERE user_id = %s AND device_id = %s
                    RETURNING id
                    """,
                    (authorization_token_id, drone_token_hash, device_name, user_id, device_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
        if _cache:
            _cache.invalidate_user_devices(user_id)
        return self.get_device_by_device_id(device_id)

    def list_user_swarms(self, user_id: str) -> Optional[list[dict]]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.id, s.owner_user_id, s.name, s.is_public, s.created_at, m.role
                    FROM swarms s
                    JOIN swarm_memberships m ON m.swarm_id = s.id
                    WHERE m.user_id = %s
                    ORDER BY s.created_at ASC, s.name ASC
                    """,
                    (user_id,),
                )
                return [
                    {
                        "id": swarm_id,
                        "owner_id": owner_id,
                        "name": name,
                        "is_public": bool(is_public),
                        "created_at": created_at,
                        "role": role,
                    }
                    for swarm_id, owner_id, name, is_public, created_at, role in cur.fetchall()
                ]

    def get_swarm(self, swarm_id: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, owner_user_id, name, is_public, created_at FROM swarms WHERE id = %s",
                    (swarm_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "owner_id": row[1],
                    "name": row[2],
                    "is_public": bool(row[3]),
                    "created_at": row[4],
                }

    def get_swarm_member(self, swarm_id: str, user_id: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT swarm_id, user_id, role, created_at FROM swarm_memberships WHERE swarm_id = %s AND user_id = %s",
                    (swarm_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {"swarm_id": row[0], "user_id": row[1], "role": row[2], "created_at": row[3]}

    def update_swarm_name(self, swarm_id: str, name: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE swarms
                    SET name = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING id, owner_user_id, name, is_public, created_at
                    """,
                    (name, swarm_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "owner_id": row[1],
                    "name": row[2],
                    "is_public": bool(row[3]),
                    "created_at": row[4],
                }

    def default_swarm_id(self, user_id: str) -> Optional[str]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.id
                    FROM swarms s
                    JOIN swarm_memberships m ON m.swarm_id = s.id
                    WHERE m.user_id = %s
                    ORDER BY (s.owner_user_id = %s) DESC, s.created_at ASC
                    LIMIT 1
                    """,
                    (user_id, user_id),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def list_user_devices(self, user_id: str, swarm_id: Optional[str] = None) -> Optional[list[dict]]:
        if _cache:
            cache_key = _cache.user_devices_key(user_id, swarm_id)
            cached = _cache.get(cache_key)
            if cached is not None:
                for d in cached:
                    d["last_seen"] = self._dt(d.get("last_seen"))
                    d["registered_at"] = self._dt(d.get("registered_at"))
                    d["removed_at"] = self._dt(d.get("removed_at"))
                    pr = d.get("public_reachability")
                    if isinstance(pr, dict):
                        pr["checked_at"] = self._dt(pr.get("checked_at"))
                return cached
        where = """
            d.approval_status = 'approved'
            AND (
                EXISTS (
                    SELECT 1 FROM swarm_memberships m
                    WHERE m.swarm_id = d.swarm_id AND m.user_id = %s
                )
                OR EXISTS (
                    SELECT 1 FROM device_admin_claims c
                    WHERE c.drone_id = d.id AND c.user_id = %s
                )
            )
        """
        params = [user_id, user_id]
        if swarm_id:
            where += " AND d.swarm_id = %s"
            params.append(swarm_id)

        def _query():
            conn = self._core_connection(ensure_schema=False)
            if conn is None:
                return None
            with conn:
                with conn.cursor() as cur:
                    cur.execute(self._select_device_sql(where) + " ORDER BY d.device_name ASC, d.device_id ASC", tuple(params))
                    return [device for device in (self._device_from_row(row) for row in cur.fetchall()) if device]

        result = self._with_schema_self_heal("list_user_devices", _query)
        if _cache and result is not None:
            _cache.set(cache_key, result, ttl=15)
        return result

    def list_all_approved_devices(
        self,
        limit: int = 0,
        oldest_checked_first: bool = True,
    ) -> Optional[list[dict]]:
        """Return all approved Drones, ordered for round-robin reachability polling."""
        order = "ns.checked_at ASC NULLS FIRST, d.registered_at ASC" if oldest_checked_first else "d.registered_at ASC"
        limit_clause = f"LIMIT {max(1, int(limit))}" if limit else ""

        def _query():
            conn = self._core_connection(ensure_schema=False)
            if conn is None:
                return None
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        self._select_device_sql("d.approval_status = 'approved' AND d.removed_at IS NULL")
                        + f" ORDER BY {order} {limit_clause}"
                    )
                    return [d for d in (self._device_from_row(row) for row in cur.fetchall()) if d]
        return self._with_schema_self_heal("list_all_approved_devices", _query)

    def update_device_reachability(self, drone_id: str, result: dict) -> bool:
        """Write a probe result directly to drone_network_state without in-memory state."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        resolvable = bool(result.get("resolvable"))
        probed_ip = str(result.get("public_ip") or "") or None
        api_port = int(result["api_port"]) if resolvable and result.get("api_port") else None
        checked_at = self._dt(result.get("checked_at"))
        reachable_url = None
        if resolvable and probed_ip and api_port:
            host = probed_ip
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            reachable_url = f"https://{host}" if api_port == 443 else f"https://{host}:{api_port}"
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO drone_network_state
                        (drone_id, public_resolvable, public_ip, api_port, scheme, reachable_url, checked_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (drone_id) DO UPDATE SET
                        public_resolvable = EXCLUDED.public_resolvable,
                        public_ip         = COALESCE(EXCLUDED.public_ip, drone_network_state.public_ip),
                        api_port          = COALESCE(EXCLUDED.api_port,  drone_network_state.api_port),
                        scheme            = COALESCE(EXCLUDED.scheme,     drone_network_state.scheme),
                        reachable_url     = COALESCE(EXCLUDED.reachable_url, drone_network_state.reachable_url),
                        checked_at        = EXCLUDED.checked_at,
                        updated_at        = now()
                    """,
                    (drone_id, resolvable, probed_ip, api_port, "https" if reachable_url else None, reachable_url, checked_at),
                )
        return True

    def update_device_edge_presence(
        self,
        device_id: str,
        *,
        online: bool,
        edge_node: Optional[str] = None,
        reflexive_endpoint: Optional[str] = None,
    ) -> bool:
        """Record Edge mux presence for a Drone, keyed by its device_id.

        Lean writer (mirrors update_device_reachability): a single targeted UPSERT
        into drone_network_state that resolves device_id -> internal id in SQL and
        only touches edge_* columns, so it never clobbers reachability or any
        column owned by another writer. Returns True if a matching device existed.
        """
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        online = bool(online)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO drone_network_state
                        (drone_id, edge_online, edge_node, reflexive_endpoint,
                         edge_connected_at, updated_at)
                    SELECT d.id, %s, %s, %s,
                           CASE WHEN %s THEN now() ELSE NULL END, now()
                    FROM drones d
                    WHERE d.device_id = %s
                    ON CONFLICT (drone_id) DO UPDATE SET
                        edge_online        = EXCLUDED.edge_online,
                        edge_node          = EXCLUDED.edge_node,
                        reflexive_endpoint = COALESCE(EXCLUDED.reflexive_endpoint,
                                                      drone_network_state.reflexive_endpoint),
                        edge_connected_at  = EXCLUDED.edge_connected_at,
                        updated_at         = now()
                    """,
                    (online, edge_node, reflexive_endpoint, online, device_id),
                )
                return cur.rowcount > 0

    def create_transfer_session(
        self,
        *,
        session_id: str,
        from_device: str,
        to_device: str,
        asset: dict,
        token_hash: str,
        expires_at_epoch: int,
        swarm_id: Optional[str] = None,
        status: str = "offered",
    ) -> bool:
        """Record a coordinated transfer (lean insert). Returns True if inserted."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transfer_sessions
                        (id, swarm_id, from_device, to_device, asset, token_hash,
                         status, expires_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), now(), now())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        session_id,
                        swarm_id,
                        from_device,
                        to_device,
                        json.dumps(asset, separators=(",", ":")),
                        token_hash,
                        status,
                        int(expires_at_epoch),
                    ),
                )
                return cur.rowcount > 0

    def get_transfer_session(self, session_id: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, swarm_id, from_device, to_device, asset, transport_used,
                           status, bytes_total, bytes_done, error,
                           extract(epoch FROM expires_at)
                    FROM transfer_sessions WHERE id = %s
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "swarm_id": row[1],
            "from_device": row[2],
            "to_device": row[3],
            "asset": json.loads(row[4]) if row[4] else {},
            "transport_used": row[5],
            "status": row[6],
            "bytes_total": row[7],
            "bytes_done": row[8],
            "error": row[9],
            "expires_at_epoch": int(row[10]) if row[10] is not None else None,
        }

    def list_recent_transfer_sessions(
        self, *, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Optional[dict]:
        """Recent transfer sessions newest-first for admin monitoring.

        Returns ``{"transfers": [...], "total": N}`` or None when no DB.
        """
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        row_limit = max(1, min(int(limit or 50), 200))
        row_offset = max(0, int(offset or 0))
        where = ""
        params: list = []
        norm_status = str(status or "").strip().lower()
        if norm_status:
            where = "WHERE status = %s"
            params = [norm_status]
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM transfer_sessions {where}", params)
                total = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT id, swarm_id, from_device, to_device, asset, transport_used,
                           status, bytes_total, bytes_done, error,
                           extract(epoch FROM expires_at), extract(epoch FROM created_at),
                           extract(epoch FROM updated_at)
                    FROM transfer_sessions
                    {where}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    params + [row_limit, row_offset],
                )
                transfers = []
                for row in cur.fetchall():
                    try:
                        asset = json.loads(row[4]) if row[4] else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        asset = {}
                    transfers.append(
                        {
                            "session_id": row[0],
                            "swarm_id": row[1],
                            "from_device": row[2],
                            "to_device": row[3],
                            "asset": asset,
                            "transport_used": row[5],
                            "status": row[6],
                            "bytes_total": row[7],
                            "bytes_done": row[8],
                            "error": row[9],
                            "expires_at_epoch": int(row[10]) if row[10] is not None else None,
                            "created_at_epoch": int(row[11]) if row[11] is not None else None,
                            "updated_at_epoch": int(row[12]) if row[12] is not None else None,
                        }
                    )
        return {"transfers": transfers, "total": total}

    def update_transfer_session(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
        transport_used: Optional[str] = None,
        bytes_total: Optional[int] = None,
        bytes_done: Optional[int] = None,
        error: Optional[str] = None,
    ) -> bool:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE transfer_sessions SET
                        status         = COALESCE(%s, status),
                        transport_used = COALESCE(%s, transport_used),
                        bytes_total    = COALESCE(%s, bytes_total),
                        bytes_done     = COALESCE(%s, bytes_done),
                        error          = COALESCE(%s, error),
                        updated_at     = now()
                    WHERE id = %s
                    """,
                    (status, transport_used, bytes_total, bytes_done, error, session_id),
                )
                return cur.rowcount > 0

    def expire_transfer_sessions(self) -> int:
        """Mark still-pending transfers past their expiry as expired. Returns count."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return 0
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE transfer_sessions SET status = 'expired', updated_at = now()
                    WHERE expires_at < now() AND status IN ('offered', 'active')
                    """
                )
                return cur.rowcount

    def user_can_access_device(self, user_id: str, device_id: str, swarm_id: Optional[str] = None) -> Optional[dict]:
        where = """
            d.device_id = %s
            AND d.approval_status = 'approved'
            AND (
                EXISTS (
                    SELECT 1 FROM swarm_memberships m
                    WHERE m.swarm_id = d.swarm_id AND m.user_id = %s
                )
                OR EXISTS (
                    SELECT 1 FROM device_admin_claims c
                    WHERE c.drone_id = d.id AND c.user_id = %s
                )
            )
        """
        params = [device_id, user_id, user_id]
        if swarm_id:
            where += " AND d.swarm_id = %s"
            params.append(swarm_id)

        def _query():
            conn = self._core_connection(ensure_schema=False)
            if conn is None:
                return None
            with conn:
                with conn.cursor() as cur:
                    cur.execute(self._select_device_sql(where), tuple(params))
                    return self._device_from_row(cur.fetchone())
        return self._with_schema_self_heal("user_can_access_device", _query)

    def count_device_roms(self, device_id: str) -> Optional[int]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)
                    FROM drone_roms r
                    JOIN drones d ON d.id = r.drone_id
                    WHERE d.device_id = %s
                    """,
                    (device_id,),
                )
                row = cur.fetchone()
                return int(row[0] or 0) if row else 0

    def count_user_notifications(self, user_id: str) -> Optional[dict]:
        """Return {"total", "unread"} for a user's non-dismissed notifications.

        A single COUNT lets the UI page through notifications and show an accurate
        unread badge / page count without ever fetching every row + detail field.
        """
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE r.read_at IS NULL) AS unread
                    FROM notifications n
                    JOIN swarm_memberships m ON m.swarm_id = n.swarm_id AND m.user_id = %s
                    LEFT JOIN notification_recipients r ON r.notification_id = n.id AND r.user_id = %s
                    WHERE r.dismissed_at IS NULL
                    """,
                    (user_id, user_id),
                )
                row = cur.fetchone()
        return {"total": int(row[0] or 0), "unread": int(row[1] or 0)} if row else {"total": 0, "unread": 0}

    def list_user_notifications(self, user_id: str, limit: int = 50, offset: int = 0) -> Optional[list[dict]]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        row_limit = max(1, min(int(limit or 50), 500))
        row_offset = max(0, int(offset or 0))
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT n.id, n.swarm_id, s.name, n.event_type, n.title, n.message, n.actor_user_id,
                           n.created_at, n.delivery_pending, n.delivery_completed_at,
                           r.read_at, r.dismissed_at
                    FROM notifications n
                    JOIN swarm_memberships m ON m.swarm_id = n.swarm_id AND m.user_id = %s
                    JOIN swarms s ON s.id = n.swarm_id
                    LEFT JOIN notification_recipients r ON r.notification_id = n.id AND r.user_id = %s
                    WHERE r.dismissed_at IS NULL
                    ORDER BY n.created_at DESC, n.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, user_id, row_limit, row_offset),
                )
                rows = cur.fetchall()
                ids = [row[0] for row in rows]
                details: dict[str, dict] = {notification_id: {} for notification_id in ids}
                if ids:
                    cur.execute(
                        """
                        SELECT notification_id, field_name, field_value
                        FROM notification_fields
                        WHERE notification_id = ANY(%s)
                        """,
                        (ids,),
                    )
                    for notification_id, field_name, field_value in cur.fetchall():
                        try:
                            value = _decode_state(json.loads(field_value)) if field_value is not None else None
                        except (TypeError, ValueError, json.JSONDecodeError):
                            value = field_value
                        details.setdefault(notification_id, {})[field_name] = value
                return [
                    {
                        "id": notification_id,
                        "swarm_id": swarm_id,
                        "swarm_name": swarm_name,
                        "event_type": event_type,
                        "title": title,
                        "message": message,
                        "short_description": title or "",
                        "full_description": message or "",
                        "actor_user_id": actor_user_id,
                        "created_at": created_at,
                        "details": details.get(notification_id, {}),
                        "read": bool(read_at),
                        "delivery_pending": bool(delivery_pending),
                        "delivery_completed_at": delivery_completed_at,
                    }
                    for (
                        notification_id, swarm_id, swarm_name, event_type, title, message, actor_user_id,
                        created_at, delivery_pending, delivery_completed_at, read_at, dismissed_at,
                    ) in rows
                ]

    def mark_notifications_read(self, user_id: str, notification_ids: Optional[Iterable[str]] = None) -> Optional[int]:
        ids = [str(item) for item in (notification_ids or []) if str(item)]
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                if ids:
                    cur.execute(
                        """
                        INSERT INTO notification_recipients (notification_id, user_id, read_at)
                        SELECT n.id, %s, now()
                        FROM notifications n
                        JOIN swarm_memberships m ON m.swarm_id = n.swarm_id AND m.user_id = %s
                        WHERE n.id = ANY(%s)
                        ON CONFLICT (notification_id, user_id)
                        DO UPDATE SET read_at = COALESCE(notification_recipients.read_at, EXCLUDED.read_at)
                        """,
                        (user_id, user_id, ids),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO notification_recipients (notification_id, user_id, read_at)
                        SELECT n.id, %s, now()
                        FROM notifications n
                        JOIN swarm_memberships m ON m.swarm_id = n.swarm_id AND m.user_id = %s
                        ON CONFLICT (notification_id, user_id)
                        DO UPDATE SET read_at = COALESCE(notification_recipients.read_at, EXCLUDED.read_at)
                        """,
                        (user_id, user_id),
                    )
                return cur.rowcount

    def dismiss_notifications(self, user_id: str, notification_ids: Optional[Iterable[str]] = None) -> Optional[int]:
        ids = [str(item) for item in (notification_ids or []) if str(item)]
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                if ids:
                    cur.execute(
                        """
                        INSERT INTO notification_recipients (notification_id, user_id, read_at, dismissed_at)
                        SELECT n.id, %s, now(), now()
                        FROM notifications n
                        JOIN swarm_memberships m ON m.swarm_id = n.swarm_id AND m.user_id = %s
                        WHERE n.id = ANY(%s)
                        ON CONFLICT (notification_id, user_id)
                        DO UPDATE SET read_at = COALESCE(notification_recipients.read_at, EXCLUDED.read_at),
                                      dismissed_at = COALESCE(notification_recipients.dismissed_at, EXCLUDED.dismissed_at)
                        """,
                        (user_id, user_id, ids),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO notification_recipients (notification_id, user_id, read_at, dismissed_at)
                        SELECT n.id, %s, now(), now()
                        FROM notifications n
                        JOIN swarm_memberships m ON m.swarm_id = n.swarm_id AND m.user_id = %s
                        ON CONFLICT (notification_id, user_id)
                        DO UPDATE SET read_at = COALESCE(notification_recipients.read_at, EXCLUDED.read_at),
                                      dismissed_at = COALESCE(notification_recipients.dismissed_at, EXCLUDED.dismissed_at)
                        """,
                        (user_id, user_id),
                    )
                return cur.rowcount

    def store_download_state(self, device_id: str, state: dict) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        clean = {
            "target_drone_id": state.get("target_drone_id") or device_id,
            "concurrency": state.get("concurrency") if isinstance(state.get("concurrency"), dict) else {"scope": "target_drone", "active_limit": 1},
            "active": state.get("active") if isinstance(state.get("active"), list) else [],
            "queued": state.get("queued") if isinstance(state.get("queued"), list) else [],
            "recent": state.get("recent") if isinstance(state.get("recent"), list) else [],
            "downloads": state.get("downloads") if isinstance(state.get("downloads"), list) else [],
            "received_at": datetime.utcnow(),
        }
        if not clean["downloads"]:
            clean["downloads"] = clean["active"] + clean["queued"] + clean["recent"]
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                self._insert_download_snapshot(cur, device["id"], clean)
        return clean

    def list_download_states(self, user_id: str, device_id: Optional[str] = None) -> Optional[list[dict]]:
        devices = [self.user_can_access_device(user_id, device_id)] if device_id else self.list_user_devices(user_id)
        if devices is None:
            return None
        ids = [device["id"] for device in devices if device]
        latest: dict[int, str] = {}
        states = {
            device["id"]: {
                "target_drone_id": device.get("device_id"),
                "device_name": device.get("device_name"),
                "concurrency": {"scope": "target_drone", "active_limit": 1},
                "active": [],
                "queued": [],
                "recent": [],
                "downloads": [],
                "received_at": None,
            }
            for device in devices if device
        }
        if not ids:
            return []
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT target_drone_id, id, reported_at, concurrency_scope, active_limit
                    FROM (
                        SELECT s.*, row_number() OVER (PARTITION BY s.target_drone_id ORDER BY s.reported_at DESC, s.id DESC) AS rn
                        FROM download_snapshots s
                        WHERE s.target_drone_id = ANY(%s)
                    ) ranked
                    WHERE rn = 1
                    """,
                    (ids,),
                )
                for target_id, snapshot_id, reported_at, concurrency_scope, active_limit in cur.fetchall():
                    latest[snapshot_id] = target_id
                    state = states.get(target_id)
                    if state is not None:
                        state["received_at"] = reported_at
                        state["concurrency"] = {"scope": concurrency_scope or "target_drone", "active_limit": active_limit or 1}
                if latest:
                    cur.execute(
                        """
                        SELECT snapshot_id, job_id, state_bucket, asset_type, status, source_drone_id, system_name,
                               file_path, rom_path, bios_name, artwork_type, file_size, downloaded_bytes,
                               percentage, transfer_speed_bps, queue_position, failure_reason
                        FROM download_items
                        WHERE snapshot_id = ANY(%s)
                        ORDER BY id ASC
                        """,
                        (list(latest),),
                    )
                    for snapshot_id, job_id, bucket, asset_type, item_status, source_drone_id, system_name, file_path, rom_path, bios_name, artwork_type, file_size, downloaded_bytes, percentage, transfer_speed_bps, queue_position, failure_reason in cur.fetchall():
                        state = states.get(latest.get(snapshot_id))
                        if state is None or bucket not in {"active", "queued", "recent"}:
                            continue
                        item = {
                            "job_id": job_id,
                            "asset_type": asset_type,
                            "status": item_status,
                            "source_drone_id": source_drone_id,
                            "system": system_name,
                            "file_path": file_path,
                            "relative_path": file_path,
                            "rom_path": rom_path,
                            "bios_name": bios_name,
                            "artwork_type": artwork_type,
                            "file_size": file_size,
                            "total_bytes": file_size,
                            "downloaded_bytes": downloaded_bytes,
                            "bytes_transferred": downloaded_bytes,
                            "percentage": percentage,
                            "transfer_speed_bps": transfer_speed_bps,
                            "queue_position": queue_position,
                            "failure_reason": failure_reason,
                        }
                        state[bucket].append(item)
                        state["downloads"].append(item)
        rows = list(states.values())
        rows.sort(key=lambda row: str(row.get("target_drone_id") or "").lower())
        return rows

    def create_device_action(self, user_id: str, device_id: str, action_type: str, payload: Optional[dict] = None) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device or device.get("user_id") != user_id:
            return None
        action = {
            "id": str(uuid.uuid4()),
            "device_id": device_id,
            "action": action_type,
            "status": "pending",
            "payload": payload or {},
            "created_at": datetime.utcnow(),
            "claimed_at": None,
            "completed_at": None,
            "message": None,
            "result": None,
            "result_received_at": None,
        }
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO drone_actions (id, drone_id, action, status, created_at)
                    VALUES (%s, %s, %s, 'pending', %s)
                    """,
                    (action["id"], device["id"], action_type, action["created_at"]),
                )
                for key, value in action["payload"].items():
                    cur.execute(
                        """
                        INSERT INTO drone_action_parameters (action_id, parameter_name, parameter_value)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (action_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value
                        """,
                        (action["id"], str(key), self._json(value)),
                    )
        return action

    def list_device_actions(self, user_id: str, device_id: str, include_recent: bool = False) -> Optional[list[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device or device.get("user_id") != user_id:
            return None
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        # The actions UI passes include_recent so an operator can confirm a queued action
        # was actually claimed and finished: without it, completed/failed actions vanish
        # the moment the Drone reports them and the queue looks like nothing happened.
        status_filter = (
            "(a.status IN ('pending', 'claimed', 'in_progress')"
            " OR a.completed_at >= now() - interval '1 hour')"
            if include_recent
            else "a.status IN ('pending', 'claimed', 'in_progress')"
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT a.id, a.action, a.status, a.created_at, a.claimed_at, a.completed_at, a.message
                    FROM drone_actions a
                    WHERE a.drone_id = %s AND {status_filter}
                    ORDER BY a.created_at DESC, a.id DESC
                    LIMIT 100
                    """,
                    (device["id"],),
                )
                rows = cur.fetchall()
                actions = [
                    {
                        "id": action_id,
                        "device_id": device_id,
                        "action": action,
                        "status": "in_progress" if action_status == "claimed" else action_status,
                        "payload": {},
                        "created_at": created_at,
                        "claimed_at": claimed_at,
                        "completed_at": completed_at,
                        "message": message,
                    }
                    for action_id, action, action_status, created_at, claimed_at, completed_at, message in rows
                ]
                by_id = {action["id"]: action for action in actions}
                if by_id:
                    cur.execute(
                        "SELECT action_id, parameter_name, parameter_value FROM drone_action_parameters WHERE action_id = ANY(%s)",
                        (list(by_id),),
                    )
                    for action_id, parameter_name, parameter_value in cur.fetchall():
                        try:
                            value = _decode_state(json.loads(parameter_value)) if parameter_value is not None else None
                        except (TypeError, ValueError, json.JSONDecodeError):
                            value = parameter_value
                        by_id[action_id].setdefault("payload", {})[parameter_name] = value
                return actions

    def claim_pending_device_actions(self, device_id: str, limit: int = 25) -> Optional[list[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        row_limit = max(1, int(limit or 25))
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE drone_actions
                    SET status = 'pending',
                        claimed_at = NULL,
                        message = COALESCE(message, 'Requeued after stale claim')
                    WHERE drone_id = %s
                      AND status = 'claimed'
                      AND claimed_at < now() - interval '15 minutes'
                    """,
                    (device["id"],),
                )
                cur.execute(
                    """
                    WITH selected AS (
                        SELECT id
                        FROM drone_actions
                        WHERE drone_id = %s AND status = 'pending'
                        ORDER BY created_at ASC, id ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE drone_actions a
                    SET status = 'claimed', claimed_at = now()
                    FROM selected
                    WHERE a.id = selected.id
                    RETURNING a.id, a.action, a.status, a.created_at, a.claimed_at, a.completed_at, a.message
                    """,
                    (device["id"], row_limit),
                )
                rows = cur.fetchall()
                actions = [
                    {
                        "id": action_id,
                        "device_id": device_id,
                        "action": action,
                        "status": "in_progress",
                        "payload": {},
                        "created_at": created_at,
                        "claimed_at": claimed_at,
                        "completed_at": completed_at,
                        "message": message,
                    }
                    for action_id, action, action_status, created_at, claimed_at, completed_at, message in rows
                ]
                by_id = {action["id"]: action for action in actions}
                if by_id:
                    cur.execute(
                        "SELECT action_id, parameter_name, parameter_value FROM drone_action_parameters WHERE action_id = ANY(%s)",
                        (list(by_id),),
                    )
                    for action_id, parameter_name, parameter_value in cur.fetchall():
                        try:
                            value = _decode_state(json.loads(parameter_value)) if parameter_value is not None else None
                        except (TypeError, ValueError, json.JSONDecodeError):
                            value = parameter_value
                        by_id[action_id].setdefault("payload", {})[parameter_name] = value
                return actions

    def expire_stale_device_actions(self, timeout_seconds: int = 600) -> int:
        """Mark claimed/in-progress actions older than the timeout as failed. Returns count."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return 0
        seconds = max(60, int(timeout_seconds))
        message = f"Action timed out: the Drone did not report completion within {seconds // 60} minute(s)."
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE drone_actions
                    SET status = 'failed',
                        completed_at = now(),
                        message = %s
                    WHERE status IN ('claimed', 'in_progress')
                      AND COALESCE(claimed_at, created_at) < now() - (%s * interval '1 second')
                    """,
                    (message, seconds),
                )
                return cur.rowcount or 0

    def complete_device_action(
        self,
        device_id: str,
        action_id: str,
        status: str,
        message: Optional[str] = None,
        result: Optional[dict] = None,
    ) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, action, status, created_at, claimed_at, completed_at, message
                    FROM drone_actions
                    WHERE id = %s AND drone_id = %s
                    FOR UPDATE
                    """,
                    (action_id, device["id"]),
                )
                row = cur.fetchone()
                if not row:
                    return None
                current_id, action, current_status, created_at, claimed_at, completed_at, current_message = row
                already_terminal = current_status in {"completed", "failed"}
                if already_terminal:
                    return {
                        "id": current_id,
                        "device_id": device_id,
                        "action": action,
                        "status": current_status,
                        "payload": {},
                        "created_at": created_at,
                        "claimed_at": claimed_at,
                        "completed_at": completed_at,
                        "message": current_message,
                        "_already_terminal": True,
                    }
                completed_at = datetime.utcnow()
                cur.execute(
                    """
                    UPDATE drone_actions
                    SET status = %s, completed_at = %s, message = %s
                    WHERE id = %s AND drone_id = %s
                    RETURNING id, action, status, created_at, claimed_at, completed_at, message
                    """,
                    (status, completed_at, message, action_id, device["id"]),
                )
                updated = cur.fetchone()
        if not updated:
            return None
        updated_id, action, updated_status, created_at, claimed_at, completed_at, updated_message = updated
        if isinstance(result, dict):
            self.store_action_result(device_id, action_id, result)
        return {
            "id": updated_id,
            "device_id": device_id,
            "action": action,
            "status": updated_status,
            "payload": {},
            "created_at": created_at,
            "claimed_at": claimed_at,
            "completed_at": completed_at,
            "message": updated_message,
            "result": result,
            "result_received_at": datetime.utcnow() if result is not None else None,
        }

    def clear_device_actions(self, user_id: str, device_id: str) -> Optional[int]:
        device = self.get_device_by_device_id(device_id)
        if not device or device.get("user_id") != user_id:
            return None
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM drone_actions WHERE drone_id = %s AND status IN ('pending', 'claimed', 'in_progress')",
                    (device["id"],),
                )
                return cur.rowcount

    def store_app_state(self, state: dict) -> None:
        if not self.url:
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                if _persist_json_app_state_enabled():
                    encoded = json.dumps(_encode_state(state))
                    cur.execute(
                        """
                        INSERT INTO overmind_app_state (id, state, updated_at)
                        VALUES (%s, %s::jsonb, now())
                        ON CONFLICT (id)
                        DO UPDATE SET state = EXCLUDED.state, updated_at = now()
                        """,
                        ("default", encoded),
                    )
                self._mirror_app_state_to_relational(cur, state)

    def _user_from_row(self, row) -> Optional[dict]:
        if not row:
            return None
        (
            user_id, email, password_hash, email_verified, is_active, auth_provider, created_at,
            username, full_name, avatar_data_url,
            auto_sync_roms,
            notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address,
        ) = row
        return {
            "id": user_id,
            "email": email,
            "password": password_hash,
            "email_verified": bool(email_verified),
            "is_active": bool(is_active),
            "auth_provider": auth_provider or "password",
            "username": username,
            "full_name": full_name,
            "avatar_data_url": avatar_data_url,
            "fleet_settings": {"auto_sync_roms": True if auto_sync_roms is None else bool(auto_sync_roms)},
            "notification_settings": {
                "notify_slack": bool(notify_slack),
                "notify_discord": bool(notify_discord),
                "notify_email": True if notify_email is None else bool(notify_email),
                "slack_webhook": slack_webhook or "",
                "discord_webhook": discord_webhook or "",
                "email_address": email_address or email,
                "types": {},
            },
            "created_at": created_at,
        }

    def _load_notification_types(self, cur, user: dict) -> dict:
        cur.execute("SELECT event_type, enabled FROM user_notification_type_settings WHERE user_id = %s", (user["id"],))
        user["notification_settings"]["types"] = {event_type: bool(enabled) for event_type, enabled in cur.fetchall()}
        return user

    def _select_user_sql(self, where_clause: str) -> str:
        return f"""
            SELECT u.id, u.email, u.password_hash, u.email_verified, u.is_active, u.auth_provider, u.created_at,
                   p.username, p.full_name, p.avatar_data_url,
                   fs.auto_sync_roms,
                   ns.notify_slack, ns.notify_discord, ns.notify_email, ns.slack_webhook, ns.discord_webhook, ns.email_address
            FROM users u
            LEFT JOIN user_profiles p ON p.user_id = u.id
            LEFT JOIN user_fleet_settings fs ON fs.user_id = u.id
            LEFT JOIN user_notification_settings ns ON ns.user_id = u.id
            WHERE {where_clause}
        """

    def get_user_by_email(self, email: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_user_sql("lower(u.email) = lower(%s)"), (email,))
                user = self._user_from_row(cur.fetchone())
                return self._load_notification_types(cur, user) if user else None

    def get_user(self, user_id: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_user_sql("u.id = %s"), (user_id,))
                user = self._user_from_row(cur.fetchone())
                return self._load_notification_types(cur, user) if user else None

    def user_exists(self, email: str) -> bool:
        return self.get_user_by_email(email) is not None

    def username_exists(self, username: str, exclude_user_id: Optional[str] = None) -> bool:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                if exclude_user_id:
                    cur.execute(
                        "SELECT 1 FROM user_profiles WHERE lower(username) = lower(%s) AND user_id <> %s LIMIT 1",
                        (username, exclude_user_id),
                    )
                else:
                    cur.execute("SELECT 1 FROM user_profiles WHERE lower(username) = lower(%s) LIMIT 1", (username,))
                return cur.fetchone() is not None

    def create_user_record(
        self,
        *,
        user_id: str,
        email: str,
        password_hash: str,
        full_name: Optional[str],
        verified: bool,
        auth_provider: str,
        username: Optional[str],
        notification_types: dict,
    ) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, email, password_hash, email_verified, is_active, auth_provider, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now(), now())
                    """,
                    (user_id, email, password_hash, bool(verified), bool(verified), auth_provider or "password"),
                )
                cur.execute(
                    """
                    INSERT INTO user_profiles (user_id, username, full_name, avatar_data_url, updated_at)
                    VALUES (%s, %s, %s, NULL, now())
                    """,
                    (user_id, username, full_name),
                )
                cur.execute("INSERT INTO user_fleet_settings (user_id, auto_sync_roms) VALUES (%s, true)", (user_id,))
                cur.execute(
                    """
                    INSERT INTO user_notification_settings
                        (user_id, notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address)
                    VALUES (%s, false, false, true, NULL, NULL, %s)
                    """,
                    (user_id, email),
                )
                for event_type, enabled in notification_types.items():
                    cur.execute(
                        """
                        INSERT INTO user_notification_type_settings (user_id, event_type, enabled)
                        VALUES (%s, %s, %s)
                        """,
                        (user_id, str(event_type), bool(enabled)),
                    )
        return self.get_user(user_id)

    def upsert_social_identity(self, user_id: str, provider: str, provider_subject: Optional[str], provider_email: Optional[str]) -> None:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_auth_identities (user_id, provider, provider_subject, provider_email, last_login_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id, provider) DO UPDATE SET
                        provider_subject = EXCLUDED.provider_subject,
                        provider_email = EXCLUDED.provider_email,
                        last_login_at = now()
                    """,
                    (user_id, provider, provider_subject, provider_email),
                )

    def set_user_verified(self, user_id: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET email_verified = true, is_active = true, updated_at = now() WHERE id = %s", (user_id,))
        return self.get_user(user_id)

    def update_user_profile(self, user_id: str, username: Optional[str], full_name: Optional[str], avatar_data_url: Optional[str]) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_profiles (user_id, username, full_name, avatar_data_url, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = COALESCE(EXCLUDED.username, user_profiles.username),
                        full_name = COALESCE(EXCLUDED.full_name, user_profiles.full_name),
                        avatar_data_url = COALESCE(EXCLUDED.avatar_data_url, user_profiles.avatar_data_url),
                        updated_at = now()
                    """,
                    (user_id, username, full_name, avatar_data_url),
                )
        return self.get_user(user_id)

    def complete_social_login(
        self,
        *,
        email: str,
        full_name: Optional[str],
        provider: str,
        user_id: str,
        password_hash: str,
        username: Optional[str],
        notification_types: dict,
    ) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_user_sql("lower(u.email) = lower(%s)"), (email,))
                existing = self._user_from_row(cur.fetchone())
                if existing:
                    user_id = existing["id"]
                    username = existing.get("username") or username
                    full_name = existing.get("full_name") or full_name
                    cur.execute(
                        "UPDATE users SET email_verified = true, is_active = true, updated_at = now() WHERE id = %s",
                        (user_id,),
                    )
                    cur.execute(
                        """
                        INSERT INTO user_profiles (user_id, username, full_name, avatar_data_url, updated_at)
                        VALUES (%s, %s, %s, %s, now())
                        ON CONFLICT (user_id) DO UPDATE SET
                            username = COALESCE(user_profiles.username, EXCLUDED.username),
                            full_name = COALESCE(user_profiles.full_name, EXCLUDED.full_name),
                            avatar_data_url = COALESCE(user_profiles.avatar_data_url, EXCLUDED.avatar_data_url),
                            updated_at = now()
                        """,
                        (user_id, username, full_name, existing.get("avatar_data_url")),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO users (id, email, password_hash, email_verified, is_active, auth_provider, created_at, updated_at)
                        VALUES (%s, %s, %s, true, true, %s, now(), now())
                        """,
                        (user_id, email, password_hash, provider or "password"),
                    )
                    cur.execute(
                        """
                        INSERT INTO user_profiles (user_id, username, full_name, avatar_data_url, updated_at)
                        VALUES (%s, %s, %s, NULL, now())
                        """,
                        (user_id, username, full_name),
                    )
                    cur.execute("INSERT INTO user_fleet_settings (user_id, auto_sync_roms) VALUES (%s, true)", (user_id,))
                    cur.execute(
                        """
                        INSERT INTO user_notification_settings
                            (user_id, notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address)
                        VALUES (%s, false, false, true, NULL, NULL, %s)
                        """,
                        (user_id, email),
                    )
                    for event_type, enabled in notification_types.items():
                        cur.execute(
                            """
                            INSERT INTO user_notification_type_settings (user_id, event_type, enabled)
                            VALUES (%s, %s, %s)
                            """,
                            (user_id, str(event_type), bool(enabled)),
                        )
                cur.execute(
                    """
                    INSERT INTO user_auth_identities (user_id, provider, provider_subject, provider_email, last_login_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id, provider) DO UPDATE SET
                        provider_subject = EXCLUDED.provider_subject,
                        provider_email = EXCLUDED.provider_email,
                        last_login_at = now()
                    """,
                    (user_id, provider, email, email),
                )
                swarm = self._ensure_personal_swarm_with_cursor(cur, user_id, f"{full_name or email or 'Overlord'}'s Swarm")
                accepted = self._accept_invitations_for_email_with_cursor(cur, email, user_id)
                cur.execute(self._select_user_sql("u.id = %s"), (user_id,))
                user = self._user_from_row(cur.fetchone())
                if user:
                    self._load_notification_types(cur, user)
                return {"user": user, "swarm": swarm, "accepted_invitations": accepted}

    def ensure_personal_swarm(self, user_id: str, name: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None or not user_id:
            return None
        with conn:
            with conn.cursor() as cur:
                return self._ensure_personal_swarm_with_cursor(cur, user_id, name)

    def _ensure_personal_swarm_with_cursor(self, cur, user_id: str, name: str) -> dict:
        cur.execute(
            """
            SELECT id, owner_user_id, name, created_at
            FROM swarms
            WHERE owner_user_id = %s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            swarm_id, owner_id, swarm_name, created_at = row
        else:
            swarm_id = str(uuid.uuid4())
            swarm_name = (name or "Overlord's Swarm").strip() or "Overlord's Swarm"
            cur.execute(
                """
                INSERT INTO swarms (id, owner_user_id, name, is_public, created_at, updated_at)
                VALUES (%s, %s, %s, false, now(), now())
                RETURNING id, owner_user_id, name, created_at
                """,
                (swarm_id, user_id, swarm_name),
            )
            swarm_id, owner_id, swarm_name, created_at = cur.fetchone()
        cur.execute(
            """
            INSERT INTO swarm_memberships (swarm_id, user_id, role, created_at)
            VALUES (%s, %s, 'overlord', now())
            ON CONFLICT (swarm_id, user_id) DO UPDATE SET role = 'overlord'
            """,
            (swarm_id, user_id),
        )
        return {"id": swarm_id, "owner_id": owner_id, "name": swarm_name, "created_at": created_at}

    def ensure_admin_alert_swarm(self, swarm_id: str, name: str, owner_id: str, member_ids: list[str]) -> Optional[str]:
        """Upsert the hidden system swarm and superadmin memberships (lean path)."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None or not owner_id:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO swarms (id, owner_user_id, name, is_public, created_at, updated_at)
                    VALUES (%s, %s, %s, false, now(), now())
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    (swarm_id, owner_id, name),
                )
                for uid in [u for u in (member_ids or []) if u]:
                    cur.execute(
                        """
                        INSERT INTO swarm_memberships (swarm_id, user_id, role, created_at)
                        VALUES (%s, %s, 'overlord', now())
                        ON CONFLICT (swarm_id, user_id) DO UPDATE SET role = 'overlord'
                        """,
                        (swarm_id, uid),
                    )
        return swarm_id

    def insert_audit_event(self, event: dict) -> bool:
        """Insert one Super Admin audit-log row (idempotent by id)."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO admin_audit_log
                        (id, event_type, summary, actor_user_id, actor_email,
                         target_type, target_id, target_label, details, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        event["id"], event["event_type"], event["summary"],
                        event.get("actor_user_id"), event.get("actor_email"),
                        event.get("target_type"), event.get("target_id"), event.get("target_label"),
                        json.dumps(event.get("details") or {}), event.get("created_at"),
                    ),
                )
        return True

    def list_audit_events(self, search: Optional[str] = None, limit: int = 20, offset: int = 0) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        row_limit = max(1, min(int(limit or 20), 100))
        row_offset = max(0, int(offset or 0))
        term = str(search or "").strip().lower()
        where = ""
        where_params: list = []
        if term:
            where = ("WHERE lower(event_type) LIKE %s OR lower(summary) LIKE %s "
                     "OR lower(coalesce(actor_email, '')) LIKE %s OR lower(coalesce(target_label, '')) LIKE %s")
            like = f"%{term}%"
            where_params = [like, like, like, like]
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM admin_audit_log {where}", where_params)
                total = cur.fetchone()[0]
                cur.execute(
                    f"""
                    SELECT id, event_type, summary, actor_email, target_type, target_id,
                           target_label, details, created_at
                    FROM admin_audit_log
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    where_params + [row_limit, row_offset],
                )
                events = []
                for row in cur.fetchall():
                    try:
                        details = json.loads(row[7]) if row[7] else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        details = {}
                    events.append({
                        "id": row[0], "event_type": row[1], "summary": row[2], "actor_email": row[3],
                        "target_type": row[4], "target_id": row[5], "target_label": row[6],
                        "details": details, "created_at": row[8],
                    })
        return {"events": events, "total": total}

    def record_landing_visit(self, ip: str, user_agent: Optional[str] = None) -> bool:
        """Upsert one anonymous landing-page visit keyed by client IP."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None or not ip:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO landing_visits (ip, first_seen, last_seen, visit_count, user_agent)
                    VALUES (%s, now(), now(), 1, %s)
                    ON CONFLICT (ip) DO UPDATE SET
                        last_seen = now(),
                        visit_count = landing_visits.visit_count + 1,
                        user_agent = COALESCE(EXCLUDED.user_agent, landing_visits.user_agent)
                    """,
                    (ip, user_agent),
                )
        return True

    def landing_visit_stats(self) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*), COALESCE(sum(visit_count), 0) FROM landing_visits")
                row = cur.fetchone()
        return {"unique": int(row[0] or 0), "total": int(row[1] or 0)}

    def list_landing_visits(self, limit: int = 20, offset: int = 0) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        row_limit = max(1, min(int(limit or 20), 100))
        row_offset = max(0, int(offset or 0))
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM landing_visits")
                total = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT ip, first_seen, last_seen, visit_count, user_agent
                    FROM landing_visits
                    ORDER BY last_seen DESC
                    LIMIT %s OFFSET %s
                    """,
                    (row_limit, row_offset),
                )
                visits = [
                    {"ip": r[0], "first_seen": r[1], "last_seen": r[2], "visit_count": int(r[3] or 0), "user_agent": r[4]}
                    for r in cur.fetchall()
                ]
        return {"visits": visits, "total": total}

    def summarize_sync_actions(self) -> Optional[dict]:
        """Counts of sync actions grouped by status (super-admin summary)."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, count(*) FROM drone_actions WHERE action LIKE 'sync%%' GROUP BY status"
                )
                by_status: dict = {}
                total = 0
                for status_value, count in cur.fetchall():
                    key = "in_progress" if status_value == "claimed" else str(status_value or "pending")
                    by_status[key] = by_status.get(key, 0) + int(count)
                    total += int(count)
        return {"total": total, "by_status": by_status}

    def accept_invitations_for_email(self, email: str, user_id: str) -> list[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None or not email or not user_id:
            return []
        with conn:
            with conn.cursor() as cur:
                rows = self._accept_invitations_for_email_with_cursor(cur, email, user_id)
        return rows

    def _accept_invitations_for_email_with_cursor(self, cur, email: str, user_id: str) -> list[dict]:
        cur.execute(
            """
            UPDATE swarm_invitations
            SET status = 'accepted', accepted_at = now()
            WHERE lower(email) = lower(%s)
              AND status = 'pending'
              AND expires_at >= now()
            RETURNING id, swarm_id, role, accepted_at
            """,
            (email,),
        )
        rows = cur.fetchall()
        for _invite_id, swarm_id, role, _accepted_at in rows:
            cur.execute(
                """
                INSERT INTO swarm_memberships (swarm_id, user_id, role, created_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (swarm_id, user_id) DO UPDATE SET role = EXCLUDED.role
                """,
                (swarm_id, user_id, role if role in {"overlord", "overseer"} else "overseer"),
            )
        return [
            {"id": invite_id, "swarm_id": swarm_id, "role": role, "accepted_at": accepted_at}
            for invite_id, swarm_id, role, accepted_at in rows
        ]

    def update_user_fleet_settings(self, user_id: str, fleet_settings: dict) -> Optional[dict]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                if "auto_sync_roms" in fleet_settings:
                    cur.execute(
                        """
                        INSERT INTO user_fleet_settings (user_id, auto_sync_roms, updated_at)
                        VALUES (%s, %s, now())
                        ON CONFLICT (user_id) DO UPDATE SET auto_sync_roms = EXCLUDED.auto_sync_roms, updated_at = now()
                        """,
                        (user_id, bool(fleet_settings.get("auto_sync_roms"))),
                    )
        return self.get_user(user_id)

    def update_user_notification_settings(self, user_id: str, notification_settings: dict) -> Optional[dict]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_notification_settings
                        (user_id, notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        notify_slack = COALESCE(EXCLUDED.notify_slack, user_notification_settings.notify_slack),
                        notify_discord = COALESCE(EXCLUDED.notify_discord, user_notification_settings.notify_discord),
                        notify_email = COALESCE(EXCLUDED.notify_email, user_notification_settings.notify_email),
                        slack_webhook = COALESCE(EXCLUDED.slack_webhook, user_notification_settings.slack_webhook),
                        discord_webhook = COALESCE(EXCLUDED.discord_webhook, user_notification_settings.discord_webhook),
                        email_address = COALESCE(EXCLUDED.email_address, user_notification_settings.email_address),
                        updated_at = now()
                    """,
                    (
                        user_id,
                        notification_settings.get("notify_slack"),
                        notification_settings.get("notify_discord"),
                        notification_settings.get("notify_email"),
                        notification_settings.get("slack_webhook") or None,
                        notification_settings.get("discord_webhook") or None,
                        notification_settings.get("email_address") or None,
                    ),
                )
                if isinstance(notification_settings.get("types"), dict):
                    for event_type, enabled in notification_settings["types"].items():
                        cur.execute(
                            """
                            INSERT INTO user_notification_type_settings (user_id, event_type, enabled)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (user_id, event_type) DO UPDATE SET enabled = EXCLUDED.enabled
                            """,
                            (user_id, str(event_type), bool(enabled)),
                        )
        return self.get_user(user_id)

    def create_integration_token_record(self, user_id: str, entry: dict) -> Optional[dict]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO integration_tokens (id, user_id, label, token_hash, created_at)
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
                    """,
                    (entry.get("id"), user_id, entry.get("label"), entry.get("token_hash"), self._dt(entry.get("created_at"))),
                )
        return dict(entry)

    def get_integration_tokens(self, user_id: str) -> Optional[list[dict]]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, label, token_hash, bound_device_id, bound_fingerprint, created_at, last_used_at, revoked_at
                    FROM integration_tokens
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "id": row[0],
                "label": row[1],
                "token_hash": row[2],
                "bound_device_id": row[3],
                "bound_device_fingerprint": row[4],
                "created_at": row[5],
                "last_used_at": row[6],
                "revoked_at": row[7],
            }
            for row in rows
        ]

    def _pending_drone_connection_from_row(self, row) -> Optional[dict]:
        if not row:
            return None
        device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id, drone_token_hash, recovery_reason, requested_at, status = row
        decoded_info = _decode_state(batocera_info) if isinstance(batocera_info, dict) else batocera_info
        return {
            "id": device_id,
            "user_id": user_id,
            "swarm_id": swarm_id,
            "device_id": device_id,
            "device_name": device_name,
            "batocera_info": decoded_info if isinstance(decoded_info, dict) else {},
            "authorization_token_id": authorization_token_id,
            "drone_token_hash": drone_token_hash,
            "recovery_reason": recovery_reason,
            "detected_at": requested_at,
            "last_seen": requested_at,
            "status": status or "pending",
        }

    def upsert_pending_drone_connection(
        self,
        device_id: str,
        device_name: str,
        batocera_info: dict,
        *,
        user_id: Optional[str],
        swarm_id: Optional[str] = None,
        authorization_token_id: Optional[str] = None,
        drone_token_hash: Optional[str] = None,
        recovery_reason: Optional[str] = None,
    ) -> Optional[dict]:
        conn = self._core_connection()
        if conn is None:
            return None
        selected_swarm_id = swarm_id
        if user_id and not selected_swarm_id:
            selected_swarm_id = self.default_swarm_id(user_id)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM drones
                    WHERE device_id = %s
                      AND approval_status = 'approved'
                      AND removed_at IS NULL
                    LIMIT 1
                    """,
                    (device_id,),
                )
                if cur.fetchone():
                    cur.execute("DELETE FROM pending_drone_connections WHERE device_id = %s", (device_id,))
                    return {
                        "device_id": device_id,
                        "device_name": device_name or device_id,
                        "status": "approved",
                        "_created": False,
                    }
                cur.execute(
                    """
                    INSERT INTO pending_drone_connections
                        (device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id,
                         drone_token_hash, recovery_reason, requested_at, status)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, now(), 'pending')
                    ON CONFLICT (device_id) DO UPDATE SET
                        user_id = COALESCE(EXCLUDED.user_id, pending_drone_connections.user_id),
                        swarm_id = COALESCE(EXCLUDED.swarm_id, pending_drone_connections.swarm_id),
                        device_name = EXCLUDED.device_name,
                        batocera_info = EXCLUDED.batocera_info,
                        authorization_token_id = COALESCE(EXCLUDED.authorization_token_id, pending_drone_connections.authorization_token_id),
                        drone_token_hash = COALESCE(EXCLUDED.drone_token_hash, pending_drone_connections.drone_token_hash),
                        recovery_reason = COALESCE(EXCLUDED.recovery_reason, pending_drone_connections.recovery_reason),
                        requested_at = now(),
                        status = 'pending'
                    RETURNING device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id,
                              drone_token_hash, recovery_reason, requested_at, status, (xmax = 0)
                    """,
                    (
                        device_id,
                        user_id,
                        selected_swarm_id,
                        device_name or device_id,
                        self._json(batocera_info if isinstance(batocera_info, dict) else {}),
                        authorization_token_id,
                        drone_token_hash,
                        recovery_reason,
                    ),
                )
                row = cur.fetchone()
                result = self._pending_drone_connection_from_row(row[:10] if row else row)
                if result is not None and row is not None:
                    result["_created"] = bool(row[10])
                return result

    def get_pending_drone_connections(self, user_id: str) -> Optional[list[dict]]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.device_id, p.user_id, p.swarm_id, p.device_name, p.batocera_info,
                           p.authorization_token_id, p.drone_token_hash, p.recovery_reason, p.requested_at, p.status
                    FROM pending_drone_connections p
                    WHERE p.status = 'pending'
                      AND NOT EXISTS (
                          SELECT 1 FROM drones d
                          WHERE d.device_id = p.device_id
                            AND d.approval_status = 'approved'
                            AND d.removed_at IS NULL
                      )
                      AND (
                          p.user_id = %s
                          OR EXISTS (
                              SELECT 1
                              FROM swarm_memberships m
                              WHERE m.swarm_id = p.swarm_id AND m.user_id = %s
                          )
                      )
                    ORDER BY p.requested_at DESC
                    """,
                    (user_id, user_id),
                )
                return [
                    conn_row
                    for conn_row in (self._pending_drone_connection_from_row(row) for row in cur.fetchall())
                    if conn_row
                ]

    def get_all_pending_drone_connections(self) -> Optional[list[dict]]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.device_id, p.user_id, p.swarm_id, p.device_name, p.batocera_info,
                           p.authorization_token_id, p.drone_token_hash, p.recovery_reason, p.requested_at, p.status
                    FROM pending_drone_connections p
                    WHERE p.status = 'pending'
                      AND NOT EXISTS (
                          SELECT 1 FROM drones d
                          WHERE d.device_id = p.device_id
                            AND d.approval_status = 'approved'
                            AND d.removed_at IS NULL
                      )
                    ORDER BY p.requested_at DESC
                    """
                )
                return [
                    conn_row
                    for conn_row in (self._pending_drone_connection_from_row(row) for row in cur.fetchall())
                    if conn_row
                ]

    def list_all_sync_actions(self, search: Optional[str] = None, limit: int = 20, offset: int = 0) -> Optional[dict]:
        """Return a page of sync actions (any status) across all users and drones.

        Newest first, optionally filtered by a search term matched against owner
        email/username, drone id, and the action's system/rom parameters. Returns
        ``{"actions": [...], "total": N}`` so the admin UI can paginate.
        """
        conn = self._core_connection()
        if conn is None:
            return None
        term = str(search or "").strip()
        like = f"%{term}%" if term else None
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        # Shared filter: all sync actions, optionally narrowed by the search term.
        where = """
            FROM drone_actions a
            JOIN drones d ON d.id = a.drone_id
            LEFT JOIN users u ON u.id = d.user_id
            LEFT JOIN user_profiles up ON up.user_id = u.id
            WHERE a.action LIKE 'sync%%'
              AND (
                  %s::text IS NULL
                  OR u.email ILIKE %s
                  OR up.username ILIKE %s
                  OR up.full_name ILIKE %s
                  OR d.device_id ILIKE %s
                  OR d.device_name ILIKE %s
                  OR EXISTS (
                      SELECT 1 FROM drone_action_parameters p
                      WHERE p.action_id = a.id
                        AND p.parameter_name IN ('system', 'system_name', 'rom_name', 'rom_path', 'rom_file')
                        AND p.parameter_value ILIKE %s
                  )
              )
        """
        filter_params = (like, like, like, like, like, like, like)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) " + where, filter_params)
                total = int(cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT a.id, a.action, a.status, a.created_at,
                           d.device_id, d.device_name, u.email, up.username, up.full_name,
                           COALESCE(
                               (SELECT parameter_value FROM drone_action_parameters
                                WHERE action_id = a.id AND parameter_name = 'system' LIMIT 1),
                               (SELECT parameter_value FROM drone_action_parameters
                                WHERE action_id = a.id AND parameter_name = 'system_name' LIMIT 1)
                           ) AS system_val,
                           COALESCE(
                               (SELECT parameter_value FROM drone_action_parameters
                                WHERE action_id = a.id AND parameter_name = 'rom_name' LIMIT 1),
                               (SELECT parameter_value FROM drone_action_parameters
                                WHERE action_id = a.id AND parameter_name = 'rom_path' LIMIT 1),
                               (SELECT parameter_value FROM drone_action_parameters
                                WHERE action_id = a.id AND parameter_name = 'rom_file' LIMIT 1)
                           ) AS rom_val
                    """ + where + """
                    ORDER BY a.created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    filter_params + (limit, offset),
                )
                actions = []
                for action_id, action, action_status, created_at, device_id, device_name, email, username, full_name, system_val, rom_val in cur.fetchall():
                    actions.append({
                        "id": action_id,
                        "action": action,
                        "status": "in_progress" if action_status == "claimed" else (action_status or "pending"),
                        "created_at": created_at,
                        "device_id": device_id,
                        "device_name": device_name,
                        "email": email,
                        "username": username,
                        "full_name": full_name,
                        "system": _coerce_param_text(system_val),
                        "rom": _coerce_param_text(rom_val),
                    })
                return {"actions": actions, "total": total}

    def get_pending_drone_connection(self, user_id: str, device_id: str) -> Optional[dict]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.device_id, p.user_id, p.swarm_id, p.device_name, p.batocera_info,
                           p.authorization_token_id, p.drone_token_hash, p.recovery_reason, p.requested_at, p.status
                    FROM pending_drone_connections p
                    WHERE p.device_id = %s
                      AND p.status = 'pending'
                      AND NOT EXISTS (
                          SELECT 1 FROM drones d
                          WHERE d.device_id = p.device_id
                            AND d.approval_status = 'approved'
                            AND d.removed_at IS NULL
                      )
                      AND (
                          p.user_id = %s
                          OR EXISTS (
                              SELECT 1
                              FROM swarm_memberships m
                              WHERE m.swarm_id = p.swarm_id AND m.user_id = %s
                          )
                      )
                    """,
                    (device_id, user_id, user_id),
                )
                return self._pending_drone_connection_from_row(cur.fetchone())

    def delete_pending_drone_connection(self, user_id: str, device_id: str, *, status: Optional[str] = None) -> Optional[bool]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        """
                        UPDATE pending_drone_connections p
                        SET status = %s
                        WHERE p.device_id = %s
                          AND (
                              p.user_id = %s
                              OR EXISTS (
                                  SELECT 1
                                  FROM swarm_memberships m
                                  WHERE m.swarm_id = p.swarm_id AND m.user_id = %s
                              )
                          )
                        """,
                        (status, device_id, user_id, user_id),
                    )
                else:
                    cur.execute(
                        """
                        DELETE FROM pending_drone_connections p
                        WHERE p.device_id = %s
                          AND (
                              p.user_id = %s
                              OR EXISTS (
                                  SELECT 1
                                  FROM swarm_memberships m
                                  WHERE m.swarm_id = p.swarm_id AND m.user_id = %s
                              )
                          )
                        """,
                        (device_id, user_id, user_id),
                    )
                return cur.rowcount > 0

    def delete_any_pending_drone_connection(self, device_id: str) -> Optional[bool]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pending_drone_connections WHERE device_id = %s", (device_id,))
                return cur.rowcount > 0

    def claim_integration_token(self, email: Optional[str], token: Optional[str], device_id: Optional[str], device_fingerprint: Optional[str] = None) -> Optional[dict]:
        if not token:
            return None
        conn = self._core_connection()
        if conn is None:
            return None
        from overmind.drone_security import verify_drone_token

        fingerprint = str(device_fingerprint or "").strip()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, label, token_hash, bound_device_id, bound_fingerprint, created_at, last_used_at, revoked_at
                    FROM integration_tokens
                    WHERE revoked_at IS NULL
                    ORDER BY CASE WHEN user_id = (SELECT id FROM users WHERE lower(email) = lower(%s) LIMIT 1) THEN 0 ELSE 1 END,
                             created_at DESC
                    """,
                    (email or "",),
                )
                rows = cur.fetchall()
                for row in rows:
                    token_id, user_id, label, token_hash, bound_device, bound_fingerprint, created_at, last_used_at, revoked_at = row
                    if not verify_drone_token(token, token_hash):
                        continue
                    if bound_device and device_id and bound_device != device_id:
                        return None
                    if bound_fingerprint and fingerprint and bound_fingerprint != fingerprint:
                        return None
                    updated_fingerprint = bound_fingerprint
                    if fingerprint and not bound_fingerprint:
                        updated_fingerprint = fingerprint
                    cur.execute(
                        """
                        UPDATE integration_tokens
                        SET bound_device_id = COALESCE(bound_device_id, %s),
                            bound_fingerprint = COALESCE(bound_fingerprint, %s),
                            last_used_at = now()
                        WHERE id = %s
                        """,
                        (device_id, updated_fingerprint, token_id),
                    )
                    user = self.get_user(user_id)
                    if not user:
                        return None
                    return {
                        "user": user,
                        "token": {
                            "id": token_id,
                            "label": label,
                            "token_hash": token_hash,
                            "bound_device_id": bound_device or device_id,
                            "bound_device_fingerprint": updated_fingerprint,
                            "created_at": created_at,
                            "last_used_at": last_used_at,
                            "revoked_at": revoked_at,
                        },
                    }
        return None

    def revoke_integration_token(self, user_id: str, token_id: str) -> Optional[bool]:
        conn = self._core_connection()
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE integration_tokens
                    SET revoked_at = now()
                    WHERE user_id = %s AND id = %s AND revoked_at IS NULL
                    RETURNING id
                    """,
                    (user_id, token_id),
                )
                return cur.fetchone() is not None

    def _json(self, value) -> str:
        return json.dumps(_encode_state(value), default=str)

    def _tail_text(self, value: str, max_lines: int) -> str:
        max_lines = max(1, int(max_lines or 1))
        lines = str(value or "").splitlines()
        return "\n".join(lines[-max_lines:])

    def _dt(self, value):
        if isinstance(value, datetime) or value is None:
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _mirror_app_state_to_relational(self, cur, state: dict) -> None:
        """Materialize the in-process repository state into normalized tables."""
        users = state.get("users") if isinstance(state.get("users"), dict) else {}
        swarms = state.get("swarms") if isinstance(state.get("swarms"), dict) else {}
        memberships = state.get("swarm_memberships") if isinstance(state.get("swarm_memberships"), dict) else {}
        devices = state.get("devices") if isinstance(state.get("devices"), dict) else {}
        user_devices = state.get("user_devices") if isinstance(state.get("user_devices"), dict) else {}
        device_admin_claims = state.get("device_admin_claims") if isinstance(state.get("device_admin_claims"), dict) else {}

        for user in users.values():
            if not isinstance(user, dict) or not user.get("id") or not user.get("email"):
                continue
            cur.execute(
                """
                INSERT INTO users (id, email, password_hash, email_verified, is_active, auth_provider, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), now())
                ON CONFLICT (id) DO UPDATE SET
                    email = EXCLUDED.email,
                    password_hash = EXCLUDED.password_hash,
                    email_verified = EXCLUDED.email_verified,
                    is_active = EXCLUDED.is_active,
                    auth_provider = EXCLUDED.auth_provider,
                    updated_at = now()
                """,
                (
                    user.get("id"),
                    user.get("email"),
                    user.get("password"),
                    bool(user.get("email_verified")),
                    bool(user.get("is_active")),
                    user.get("auth_provider") or "password",
                    self._dt(user.get("created_at")),
                ),
            )
            cur.execute(
                """
                INSERT INTO user_profiles (user_id, username, full_name, avatar_data_url, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    avatar_data_url = EXCLUDED.avatar_data_url,
                    updated_at = now()
                """,
                (user.get("id"), user.get("username"), user.get("full_name"), user.get("avatar_data_url")),
            )
            if (user.get("auth_provider") or "password") != "password":
                cur.execute(
                    """
                    INSERT INTO user_auth_identities (user_id, provider, provider_subject, provider_email, last_login_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id, provider) DO UPDATE SET
                        provider_subject = EXCLUDED.provider_subject,
                        provider_email = EXCLUDED.provider_email,
                        last_login_at = EXCLUDED.last_login_at
                    """,
                    (user.get("id"), user.get("auth_provider"), user.get("provider_subject") or user.get("email"), user.get("email")),
                )
            fleet = user.get("fleet_settings") if isinstance(user.get("fleet_settings"), dict) else {}
            cur.execute(
                """
                INSERT INTO user_fleet_settings (user_id, auto_sync_roms, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET auto_sync_roms = EXCLUDED.auto_sync_roms, updated_at = now()
                """,
                (user.get("id"), bool(fleet.get("auto_sync_roms", True))),
            )
            settings = user.get("notification_settings") if isinstance(user.get("notification_settings"), dict) else {}
            cur.execute(
                """
                INSERT INTO user_notification_settings
                    (user_id, notify_slack, notify_discord, notify_email, slack_webhook, discord_webhook, email_address, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET
                    notify_slack = EXCLUDED.notify_slack,
                    notify_discord = EXCLUDED.notify_discord,
                    notify_email = EXCLUDED.notify_email,
                    slack_webhook = EXCLUDED.slack_webhook,
                    discord_webhook = EXCLUDED.discord_webhook,
                    email_address = EXCLUDED.email_address,
                    updated_at = now()
                """,
                (
                    user.get("id"),
                    bool(settings.get("notify_slack")),
                    bool(settings.get("notify_discord")),
                    bool(settings.get("notify_email", True)),
                    settings.get("slack_webhook") or None,
                    settings.get("discord_webhook") or None,
                    settings.get("email_address") or user.get("email"),
                ),
            )
            if isinstance(settings.get("types"), dict):
                for event_type, enabled in settings["types"].items():
                    cur.execute(
                        """
                        INSERT INTO user_notification_type_settings (user_id, event_type, enabled)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, event_type) DO UPDATE SET enabled = EXCLUDED.enabled
                        """,
                        (user.get("id"), str(event_type), bool(enabled)),
                    )

        for swarm in swarms.values():
            if not isinstance(swarm, dict) or not swarm.get("id") or not swarm.get("owner_id"):
                continue
            cur.execute(
                """
                INSERT INTO swarms (id, owner_user_id, name, is_public, created_at, updated_at)
                VALUES (%s, %s, %s, %s, COALESCE(%s, now()), now())
                ON CONFLICT (id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    name = EXCLUDED.name,
                    is_public = EXCLUDED.is_public,
                    updated_at = now()
                """,
                (swarm.get("id"), swarm.get("owner_id"), swarm.get("name") or "Swarm", bool(swarm.get("is_public")), self._dt(swarm.get("created_at"))),
            )
        for swarm_id, rows in memberships.items():
            if not isinstance(rows, dict):
                continue
            for user_id, member in rows.items():
                if not isinstance(member, dict):
                    continue
                role = member.get("role") if member.get("role") in {"overlord", "overseer"} else "overseer"
                cur.execute(
                    """
                    INSERT INTO swarm_memberships (swarm_id, user_id, role, created_at)
                    VALUES (%s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (swarm_id, user_id) DO UPDATE SET role = EXCLUDED.role
                    """,
                    (swarm_id, user_id, role, self._dt(member.get("created_at"))),
                )

        for device in devices.values():
            if not isinstance(device, dict) or not device.get("id") or not device.get("device_id") or not device.get("user_id"):
                continue
            cur.execute(
                """
                INSERT INTO drones
                    (id, device_id, device_name, user_id, swarm_id, approval_status, swarm_connected,
                     authorization_token_id, drone_token_hash, rom_inventory_fingerprint,
                     drone_rom_inventory_fingerprint, rom_inventory_fingerprint_algorithm,
                     rom_inventory_fingerprint_at, drone_rom_inventory_fingerprint_at,
                     romset_files_thumbprint, bios_files_thumbprint,
                     registered_at, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    device_name = EXCLUDED.device_name,
                    user_id = EXCLUDED.user_id,
                    swarm_id = EXCLUDED.swarm_id,
                    approval_status = EXCLUDED.approval_status,
                    swarm_connected = EXCLUDED.swarm_connected,
                    authorization_token_id = EXCLUDED.authorization_token_id,
                    drone_token_hash = EXCLUDED.drone_token_hash,
                    rom_inventory_fingerprint = EXCLUDED.rom_inventory_fingerprint,
                    drone_rom_inventory_fingerprint = EXCLUDED.drone_rom_inventory_fingerprint,
                    rom_inventory_fingerprint_algorithm = EXCLUDED.rom_inventory_fingerprint_algorithm,
                    rom_inventory_fingerprint_at = EXCLUDED.rom_inventory_fingerprint_at,
                    drone_rom_inventory_fingerprint_at = EXCLUDED.drone_rom_inventory_fingerprint_at,
                    romset_files_thumbprint = EXCLUDED.romset_files_thumbprint,
                    bios_files_thumbprint = EXCLUDED.bios_files_thumbprint,
                    last_seen = EXCLUDED.last_seen
                """,
                (
                    device.get("id"),
                    device.get("device_id"),
                    device.get("device_name") or device.get("device_id"),
                    device.get("user_id"),
                    device.get("swarm_id"),
                    device.get("approval_status") or "approved",
                    bool(device.get("swarm_connected", True)),
                    device.get("authorization_token_id"),
                    device.get("drone_token_hash"),
                    device.get("rom_inventory_fingerprint"),
                    device.get("drone_rom_inventory_fingerprint"),
                    device.get("rom_inventory_fingerprint_algorithm"),
                    self._dt(device.get("rom_inventory_fingerprint_at")),
                    self._dt(device.get("drone_rom_inventory_fingerprint_at")),
                    device.get("romset_files_thumbprint"),
                    device.get("bios_files_thumbprint"),
                    self._dt(device.get("registered_at")),
                    self._dt(device.get("last_seen")),
                ),
            )
            self._mirror_device_details(cur, device)

        for drone_id, claims in device_admin_claims.items():
            if not isinstance(claims, list):
                continue
            for user_id in claims:
                cur.execute(
                    """
                    INSERT INTO device_admin_claims (drone_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (drone_id, str(user_id)),
                )
        for user_id, device_ids in user_devices.items():
            for drone_id in device_ids if isinstance(device_ids, list) else []:
                cur.execute(
                    """
                    INSERT INTO device_admin_claims (drone_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (str(drone_id), str(user_id)),
                )

        self._mirror_auth_and_notifications(cur, state)
        self._mirror_operational_state(cur, state, devices)

    def _mirror_device_details(self, cur, device: dict) -> None:
        network = device.get("network") if isinstance(device.get("network"), dict) else {}
        reachability = device.get("public_reachability") if isinstance(device.get("public_reachability"), dict) else {}
        # The public-reachability probe (update_device_reachability) is the single
        # authoritative writer of public_resolvable/public_ip/checked_at. A full-state
        # mirror is often driven by a partial snapshot that carries no probe data, so
        # only write those columns when probe data is actually present; otherwise
        # COALESCE-preserve the existing row so a snapshot can never silently reset a
        # Drone to "Not Resolvable" (which previously re-fired drone_resolvable).
        has_probe_data = bool(reachability.get("checked_at"))
        resolvable_param = bool(reachability.get("resolvable")) if has_probe_data else None
        cur.execute(
            """
            INSERT INTO drone_network_state
                (drone_id, api_port, scheme, reachable_url, public_resolvable, public_ip, checked_at, updated_at)
            VALUES (%s, %s, %s, %s, COALESCE(%s::boolean, false), %s, %s, now())
            ON CONFLICT (drone_id) DO UPDATE SET
                api_port = EXCLUDED.api_port,
                scheme = EXCLUDED.scheme,
                reachable_url = EXCLUDED.reachable_url,
                public_resolvable = COALESCE(%s::boolean, drone_network_state.public_resolvable),
                public_ip = COALESCE(EXCLUDED.public_ip, drone_network_state.public_ip),
                checked_at = COALESCE(EXCLUDED.checked_at, drone_network_state.checked_at),
                updated_at = now()
            """,
            (
                device.get("id"),
                device.get("api_port"),
                device.get("scheme") or "https",
                device.get("reachable_url"),
                # public_resolvable is NOT NULL: a fresh insert with no probe data defaults to
                # false (COALESCE in VALUES); on conflict we preserve the existing value when no
                # probe data (COALESCE against the bind param, passed again below) so the mirror
                # never resets a Drone to Not Resolvable.
                resolvable_param,
                (reachability.get("public_ip") or network.get("public_ip") or network.get("public")) if has_probe_data else None,
                self._dt(reachability.get("checked_at")) if has_probe_data else None,
                resolvable_param,
            ),
        )
        cur.execute("DELETE FROM drone_network_addresses WHERE drone_id = %s", (device.get("id"),))
        for address_type in ("ipv4", "ipv6"):
            for address in network.get(address_type) if isinstance(network.get(address_type), list) else []:
                cur.execute(
                    "INSERT INTO drone_network_addresses (drone_id, address_type, address) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (device.get("id"), address_type, str(address)),
                )
        for address_type, key in (("hostname", "hostname"), ("mac", "mac_address")):
            if network.get(key):
                cur.execute(
                    "INSERT INTO drone_network_addresses (drone_id, address_type, address) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (device.get("id"), address_type, str(network.get(key))),
                )
        info = device.get("system_info") if isinstance(device.get("system_info"), dict) else {}
        iva = _idle_volume_automation_columns(info)
        cur.execute(
            """
            INSERT INTO drone_system_info
                (drone_id, hostname, model, system_name, architecture, cpu_model, cpu_cores, cpu_threads,
                 cpu_max_frequency, memory_available, memory_total, batocera_version, screen_mode,
                 audio_volume, idle_volume_enabled, idle_volume_idle_minutes, idle_volume_target,
                 container, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (drone_id) DO UPDATE SET
                hostname = EXCLUDED.hostname,
                model = EXCLUDED.model,
                system_name = EXCLUDED.system_name,
                architecture = EXCLUDED.architecture,
                cpu_model = EXCLUDED.cpu_model,
                cpu_cores = EXCLUDED.cpu_cores,
                cpu_threads = EXCLUDED.cpu_threads,
                cpu_max_frequency = EXCLUDED.cpu_max_frequency,
                memory_available = EXCLUDED.memory_available,
                memory_total = EXCLUDED.memory_total,
                batocera_version = EXCLUDED.batocera_version,
                screen_mode = EXCLUDED.screen_mode,
                audio_volume = EXCLUDED.audio_volume,
                idle_volume_enabled = EXCLUDED.idle_volume_enabled,
                idle_volume_idle_minutes = EXCLUDED.idle_volume_idle_minutes,
                idle_volume_target = EXCLUDED.idle_volume_target,
                container = EXCLUDED.container,
                updated_at = now()
            """,
            (
                device.get("id"),
                info.get("hostname"),
                info.get("model"),
                info.get("system") or info.get("system_name"),
                info.get("architecture"),
                info.get("cpu_model"),
                info.get("cpu_cores"),
                info.get("cpu_threads"),
                info.get("cpu_max_frequency"),
                info.get("memory_available"),
                info.get("memory_total"),
                info.get("batocera_version"),
                info.get("screen_mode"),
                info.get("audio_volume"),
                iva[0],
                iva[1],
                iva[2],
                info.get("container"),
            ),
        )
        if isinstance(info.get("performance"), dict):
            cur.execute("DELETE FROM drone_performance_metrics WHERE drone_id = %s", (device.get("id"),))
            for group, values in info["performance"].items():
                if not isinstance(values, dict):
                    continue
                for name, value in values.items():
                    # bool is a subclass of int, so it would be sent to the numeric
                    # metric_value column and fail with DatatypeMismatch (double precision
                    # vs boolean), aborting the whole state persist. Route bools to text.
                    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                    text = None if number is not None else (str(value) if value is not None else None)
                    cur.execute(
                        """
                        INSERT INTO drone_performance_metrics (drone_id, metric_group, metric_name, metric_value, metric_text)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (drone_id, metric_group, metric_name)
                        DO UPDATE SET metric_value = EXCLUDED.metric_value, metric_text = EXCLUDED.metric_text, observed_at = now()
                        """,
                        (device.get("id"), str(group), str(name), number, text),
                    )
        cert = device.get("certificate") if isinstance(device.get("certificate"), dict) else {}
        if cert:
            cur.execute(
                """
                INSERT INTO drone_certificates
                    (drone_id, status, fingerprint, sha256_fingerprint, public_certificate, subject, issuer,
                     valid_from, valid_until, serial_number, overmind_signed_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (drone_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    fingerprint = EXCLUDED.fingerprint,
                    sha256_fingerprint = EXCLUDED.sha256_fingerprint,
                    public_certificate = EXCLUDED.public_certificate,
                    subject = EXCLUDED.subject,
                    issuer = EXCLUDED.issuer,
                    valid_from = EXCLUDED.valid_from,
                    valid_until = EXCLUDED.valid_until,
                    serial_number = EXCLUDED.serial_number,
                    overmind_signed_at = EXCLUDED.overmind_signed_at,
                    updated_at = now()
                """,
                (
                    device.get("id"),
                    cert.get("status"),
                    cert.get("fingerprint"),
                    cert.get("sha256_fingerprint"),
                    cert.get("public_certificate") or cert.get("certificate_pem"),
                    cert.get("subject"),
                    cert.get("issuer"),
                    self._dt(cert.get("valid_from")),
                    self._dt(cert.get("valid_until")),
                    cert.get("serial_number"),
                    self._dt(cert.get("overmind_signed_at")),
                ),
            )
            cur.execute("DELETE FROM drone_certificate_sans WHERE drone_id = %s", (device.get("id"),))
            for san in cert.get("san") if isinstance(cert.get("san"), list) else []:
                cur.execute(
                    "INSERT INTO drone_certificate_sans (drone_id, san) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (device.get("id"), str(san)),
                )
        policy = device.get("auto_sync_policy") if isinstance(device.get("auto_sync_policy"), dict) else {}
        cur.execute(
            """
            INSERT INTO drone_auto_sync_policies (drone_id, enabled, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (drone_id) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()
            """,
            (device.get("id"), bool(policy.get("enabled"))),
        )
        cur.execute("DELETE FROM drone_auto_sync_policy_systems WHERE drone_id = %s", (device.get("id"),))
        for system in policy.get("systems") if isinstance(policy.get("systems"), list) else []:
            cur.execute(
                "INSERT INTO drone_auto_sync_policy_systems (drone_id, system_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (device.get("id"), str(system)),
            )

    def _mirror_auth_and_notifications(self, cur, state: dict) -> None:
        tokens = state.get("integration_tokens") if isinstance(state.get("integration_tokens"), dict) else {}
        for user_id, rows in tokens.items():
            for token in rows if isinstance(rows, list) else []:
                if not isinstance(token, dict) or not token.get("id"):
                    continue
                cur.execute(
                    """
                    INSERT INTO integration_tokens
                        (id, user_id, label, token_hash, bound_device_id, bound_fingerprint, created_at, last_used_at, revoked_at)
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label,
                        token_hash = EXCLUDED.token_hash,
                        bound_device_id = EXCLUDED.bound_device_id,
                        bound_fingerprint = EXCLUDED.bound_fingerprint,
                        last_used_at = EXCLUDED.last_used_at,
                        revoked_at = EXCLUDED.revoked_at
                    """,
                    (
                        token.get("id"),
                        user_id,
                        token.get("label") or "Drone onboarding",
                        token.get("token_hash"),
                        token.get("bound_device_id"),
                        token.get("bound_device_fingerprint") or token.get("bound_fingerprint"),
                        self._dt(token.get("created_at")),
                        self._dt(token.get("last_used_at")),
                        self._dt(token.get("revoked_at")),
                    ),
                )
        approved = state.get("approved_drone_tokens") if isinstance(state.get("approved_drone_tokens"), dict) else {}
        for device_id, token in approved.items():
            cur.execute(
                """
                INSERT INTO approved_drone_tokens (device_id, token_hash)
                VALUES (%s, %s)
                ON CONFLICT (device_id) DO UPDATE SET token_hash = EXCLUDED.token_hash
                """,
                (str(device_id), str(token)),
            )
        pending = state.get("pending_drone_connections") if isinstance(state.get("pending_drone_connections"), dict) else {}
        for conn in pending.values():
            if not isinstance(conn, dict) or not conn.get("device_id"):
                continue
            cur.execute(
                """
                INSERT INTO pending_drone_connections
                    (device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id,
                     drone_token_hash, recovery_reason, requested_at, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    swarm_id = EXCLUDED.swarm_id,
                    device_name = EXCLUDED.device_name,
                    batocera_info = EXCLUDED.batocera_info,
                    authorization_token_id = EXCLUDED.authorization_token_id,
                    drone_token_hash = EXCLUDED.drone_token_hash,
                    recovery_reason = EXCLUDED.recovery_reason,
                    status = EXCLUDED.status
                """,
                (
                    conn.get("device_id"),
                    conn.get("user_id"),
                    conn.get("swarm_id"),
                    conn.get("device_name") or conn.get("device_id"),
                    self._json(conn.get("batocera_info") if isinstance(conn.get("batocera_info"), dict) else {}),
                    conn.get("authorization_token_id"),
                    conn.get("drone_token_hash"),
                    conn.get("recovery_reason"),
                    self._dt(conn.get("detected_at") or conn.get("last_seen")),
                    conn.get("status") or "pending",
                ),
            )
        verifications = state.get("email_verifications") if isinstance(state.get("email_verifications"), dict) else {}
        for entry in verifications.values():
            if not isinstance(entry, dict) or not entry.get("user_id"):
                continue
            cur.execute(
                """
                INSERT INTO email_verifications (user_id, code, token_hash, expires_at, created_at, used_at)
                VALUES (%s, %s, %s, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    code = EXCLUDED.code,
                    token_hash = EXCLUDED.token_hash,
                    expires_at = EXCLUDED.expires_at,
                    used_at = EXCLUDED.used_at
                """,
                (entry.get("user_id"), entry.get("code"), entry.get("token_hash"), self._dt(entry.get("expires_at")), self._dt(entry.get("created_at")), self._dt(entry.get("used_at"))),
            )
        resets = state.get("password_resets") if isinstance(state.get("password_resets"), dict) else {}
        for entry in resets.values():
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            cur.execute(
                """
                INSERT INTO password_resets (id, user_id, token_hash, expires_at, created_at, used_at)
                VALUES (%s, %s, %s, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (id) DO UPDATE SET
                    token_hash = EXCLUDED.token_hash,
                    expires_at = EXCLUDED.expires_at,
                    used_at = EXCLUDED.used_at
                """,
                (entry.get("id"), entry.get("user_id"), entry.get("token_hash"), self._dt(entry.get("expires_at")), self._dt(entry.get("created_at")), self._dt(entry.get("used_at"))),
            )
        invitations = state.get("invitations") if isinstance(state.get("invitations"), dict) else {}
        for invite in invitations.values():
            if not isinstance(invite, dict) or not invite.get("id"):
                continue
            role = invite.get("role") if invite.get("role") in {"overlord", "overseer"} else "overseer"
            cur.execute(
                """
                INSERT INTO swarm_invitations
                    (id, swarm_id, email, role, token_hash, status, invited_by, created_at, resent_at, expires_at, accepted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    role = EXCLUDED.role,
                    token_hash = EXCLUDED.token_hash,
                    status = EXCLUDED.status,
                    resent_at = EXCLUDED.resent_at,
                    expires_at = EXCLUDED.expires_at,
                    accepted_at = EXCLUDED.accepted_at
                """,
                (
                    invite.get("id"),
                    invite.get("swarm_id"),
                    invite.get("email"),
                    role,
                    invite.get("token_hash"),
                    invite.get("status") or "pending",
                    invite.get("invited_by"),
                    self._dt(invite.get("created_at")),
                    self._dt(invite.get("resent_at")),
                    self._dt(invite.get("expires_at")),
                    self._dt(invite.get("accepted_at")),
                ),
            )
        notifications = state.get("notifications") if isinstance(state.get("notifications"), dict) else {}
        for swarm_id, rows in notifications.items():
            for note in rows if isinstance(rows, list) else []:
                if not isinstance(note, dict) or not note.get("id"):
                    continue
                cur.execute(
                    """
                    INSERT INTO notifications
                        (id, swarm_id, event_type, title, message, actor_user_id, created_at, delivery_pending, delivery_completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        event_type = EXCLUDED.event_type,
                        title = EXCLUDED.title,
                        message = EXCLUDED.message,
                        actor_user_id = EXCLUDED.actor_user_id,
                        delivery_pending = EXCLUDED.delivery_pending,
                        delivery_completed_at = EXCLUDED.delivery_completed_at
                    """,
                    (
                        note.get("id"),
                        note.get("swarm_id") or swarm_id,
                        note.get("event_type"),
                        note.get("title") or "",
                        note.get("message") or "",
                        note.get("actor_user_id"),
                        self._dt(note.get("created_at")),
                        bool(note.get("delivery_pending")),
                        self._dt(note.get("delivery_completed_at")),
                    ),
                )
                cur.execute("DELETE FROM notification_fields WHERE notification_id = %s", (note.get("id"),))
                details = note.get("details") if isinstance(note.get("details"), dict) else {}
                for key, value in details.items():
                    cur.execute(
                        "INSERT INTO notification_fields (notification_id, field_name, field_value) VALUES (%s, %s, %s) ON CONFLICT (notification_id, field_name) DO UPDATE SET field_value = EXCLUDED.field_value",
                        (note.get("id"), str(key), self._json(value)),
                    )
                recipient_ids = set()
                read_by = note.get("read_by") if isinstance(note.get("read_by"), dict) else {}
                dismissed_by = note.get("dismissed_by") if isinstance(note.get("dismissed_by"), dict) else {}
                recipient_ids.update(read_by.keys())
                recipient_ids.update(dismissed_by.keys())
                for user_id in recipient_ids:
                    cur.execute(
                        """
                        INSERT INTO notification_recipients
                            (notification_id, user_id, read_at, dismissed_at, delivery_pending)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (notification_id, user_id) DO UPDATE SET
                            read_at = EXCLUDED.read_at,
                            dismissed_at = EXCLUDED.dismissed_at,
                            delivery_pending = EXCLUDED.delivery_pending
                        """,
                        (note.get("id"), user_id, self._dt(read_by.get(user_id)), self._dt(dismissed_by.get(user_id)), bool(note.get("delivery_pending"))),
                    )

    def _mirror_operational_state(self, cur, state: dict, devices: dict) -> None:
        for internal_id, rows in (state.get("device_actions") if isinstance(state.get("device_actions"), dict) else {}).items():
            for action in rows if isinstance(rows, list) else []:
                if not isinstance(action, dict) or not action.get("id"):
                    continue
                cur.execute(
                    """
                    INSERT INTO drone_actions (id, drone_id, action, status, created_at, claimed_at, completed_at, message)
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()), %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        claimed_at = EXCLUDED.claimed_at,
                        completed_at = EXCLUDED.completed_at,
                        message = EXCLUDED.message
                    """,
                    (
                        action.get("id"),
                        internal_id,
                        action.get("action"),
                        action.get("status") or "pending",
                        self._dt(action.get("created_at")),
                        self._dt(action.get("claimed_at")),
                        self._dt(action.get("completed_at")),
                        action.get("message"),
                    ),
                )
                cur.execute("DELETE FROM drone_action_parameters WHERE action_id = %s", (action.get("id"),))
                for key, value in (action.get("payload") if isinstance(action.get("payload"), dict) else {}).items():
                    cur.execute(
                        "INSERT INTO drone_action_parameters (action_id, parameter_name, parameter_value) VALUES (%s, %s, %s) ON CONFLICT DO UPDATE SET parameter_value = EXCLUDED.parameter_value",
                        (action.get("id"), str(key), self._json(value)),
                    )
                if isinstance(action.get("result"), dict):
                    self._insert_action_result(cur, internal_id, action.get("device_id"), action.get("id"), action.get("result"), action.get("status"), action.get("message"), self._dt(action.get("result_received_at")))

        for internal_id, rows in (state.get("gamelogs") if isinstance(state.get("gamelogs"), dict) else {}).items():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO gameplay_sessions
                        (id, drone_id, system_name, game_name, rom_path, rom_fingerprint, played_at, duration_seconds, received_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        system_name = EXCLUDED.system_name,
                        game_name = EXCLUDED.game_name,
                        rom_path = EXCLUDED.rom_path,
                        rom_fingerprint = EXCLUDED.rom_fingerprint,
                        played_at = EXCLUDED.played_at,
                        duration_seconds = EXCLUDED.duration_seconds
                    """,
                    (
                        row.get("id") or f"{internal_id}:{row.get('played_at')}:{row.get('game_name')}",
                        internal_id,
                        row.get("system_name"),
                        row.get("game_name") or row.get("name") or "Unknown game",
                        row.get("rom_path"),
                        row.get("rom_fingerprint"),
                        self._dt(row.get("played_at")),
                        row.get("duration_seconds"),
                    ),
                )
        for internal_id, rows in (state.get("speed_samples") if isinstance(state.get("speed_samples"), dict) else {}).items():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                measured_at = self._dt(row.get("measured_at") or row.get("sampled_at"))
                received_at = self._dt(row.get("sampled_at") or row.get("received_at"))
                cur.execute(
                    """
                    INSERT INTO drone_speed_samples
                        (drone_id, upload_mbps, download_mbps, latency_ms, measured_at, received_at)
                    SELECT %s, %s, %s, %s, %s, COALESCE(%s, now())
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM drone_speed_samples
                        WHERE drone_id = %s
                          AND upload_mbps IS NOT DISTINCT FROM %s
                          AND download_mbps IS NOT DISTINCT FROM %s
                          AND latency_ms IS NOT DISTINCT FROM %s
                          AND measured_at IS NOT DISTINCT FROM %s
                          AND %s IS NOT NULL
                          AND received_at IS NOT DISTINCT FROM %s
                    )
                    """,
                    (
                        internal_id,
                        row.get("upload_mbps"),
                        row.get("download_mbps"),
                        row.get("latency_ms"),
                        measured_at,
                        received_at,
                        internal_id,
                        row.get("upload_mbps"),
                        row.get("download_mbps"),
                        row.get("latency_ms"),
                        measured_at,
                        received_at,
                        received_at,
                    ),
                )
        for internal_id, rows in (state.get("device_events") if isinstance(state.get("device_events"), dict) else {}).items():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                message = row.get("message") or row.get("rom") or row.get("path")
                occurred_at = self._dt(row.get("timestamp") or row.get("occurred_at"))
                received_at = self._dt(row.get("received_at"))
                cur.execute(
                    """
                    INSERT INTO drone_events (drone_id, event_type, severity, message, occurred_at, received_at)
                    SELECT %s, %s, %s, %s, %s, COALESCE(%s, now())
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM drone_events
                        WHERE drone_id = %s
                          AND event_type IS NOT DISTINCT FROM %s
                          AND severity IS NOT DISTINCT FROM %s
                          AND message IS NOT DISTINCT FROM %s
                          AND occurred_at IS NOT DISTINCT FROM %s
                          AND %s IS NOT NULL
                          AND received_at IS NOT DISTINCT FROM %s
                    )
                    RETURNING id
                    """,
                    (
                        internal_id,
                        row.get("event_type"),
                        row.get("severity"),
                        message,
                        occurred_at,
                        received_at,
                        internal_id,
                        row.get("event_type"),
                        row.get("severity"),
                        message,
                        occurred_at,
                        received_at,
                        received_at,
                    ),
                )
                event_id = (cur.fetchone() or [None])[0]
                if event_id is None:
                    continue
                for key, value in (row.get("metadata") if isinstance(row.get("metadata"), dict) else {}).items():
                    cur.execute(
                        "INSERT INTO drone_event_fields (event_id, field_name, field_value) VALUES (%s, %s, %s) ON CONFLICT (event_id, field_name) DO UPDATE SET field_value = EXCLUDED.field_value",
                        (event_id, str(key), self._json(value)),
                    )
        for internal_id, rows in (state.get("peer_checks") if isinstance(state.get("peer_checks"), dict) else {}).items():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or not row.get("target_drone_id"):
                    continue
                cur.execute(
                    """
                    INSERT INTO drone_peer_checks
                        (source_drone_id, target_drone_id, target_address, status, latency_ms, checked_at, error, received_at)
                    SELECT %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now())
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM drone_peer_checks
                        WHERE source_drone_id = %s
                          AND target_drone_id = %s
                          AND target_address IS NOT DISTINCT FROM %s
                          AND status = %s
                          AND latency_ms IS NOT DISTINCT FROM %s
                          AND checked_at IS NOT DISTINCT FROM %s
                          AND error IS NOT DISTINCT FROM %s
                          AND %s IS NOT NULL
                          AND received_at IS NOT DISTINCT FROM %s
                    )
                    """,
                    (
                        internal_id,
                        row.get("target_drone_id"),
                        row.get("target_address"),
                        row.get("status") or "fail",
                        row.get("latency_ms"),
                        self._dt(row.get("checked_at")),
                        row.get("failure_reason") or row.get("error"),
                        self._dt(row.get("received_at")),
                        internal_id,
                        row.get("target_drone_id"),
                        row.get("target_address"),
                        row.get("status") or "fail",
                        row.get("latency_ms"),
                        self._dt(row.get("checked_at")),
                        row.get("failure_reason") or row.get("error"),
                        self._dt(row.get("received_at")),
                        self._dt(row.get("received_at")),
                    ),
                )
        for internal_id, state_row in (state.get("download_states") if isinstance(state.get("download_states"), dict) else {}).items():
            if isinstance(state_row, dict):
                self._insert_download_snapshot(cur, internal_id, state_row)
        for internal_id, rows in (state.get("rom_sync_activity") if isinstance(state.get("rom_sync_activity"), dict) else {}).items():
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    self._insert_sync_activity(cur, internal_id, row)

    def _insert_action_result(self, cur, internal_id: Optional[str], device_id: Optional[str], action_id: Optional[str], result: dict, status: Optional[str], message: Optional[str], received_at) -> None:
        cur.execute(
            """
            INSERT INTO drone_action_result_records
                (drone_id, device_id, action_id, result_type, status, message, received_at)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
            RETURNING id
            """,
            (internal_id, device_id, action_id, result.get("type"), status, message, received_at),
        )
        result_id = (cur.fetchone() or [None])[0]
        for key, value in result.items():
            if result_id is None:
                continue
            cur.execute(
                "INSERT INTO drone_action_result_fields (result_id, field_name, field_value) VALUES (%s, %s, %s) ON CONFLICT (result_id, field_name) DO UPDATE SET field_value = EXCLUDED.field_value",
                (result_id, str(key), self._json(value)),
            )

    def _insert_download_snapshot(self, cur, internal_id: str, state: dict) -> None:
        concurrency = state.get("concurrency") if isinstance(state.get("concurrency"), dict) else {}
        reported_at = self._dt(state.get("received_at"))
        cur.execute(
            """
            INSERT INTO download_snapshots (target_drone_id, reported_at, concurrency_scope, active_limit)
            SELECT %s, COALESCE(%s, now()), %s, %s
            WHERE NOT EXISTS (
                SELECT 1
                FROM download_snapshots
                WHERE target_drone_id = %s
                  AND %s IS NOT NULL
                  AND reported_at IS NOT DISTINCT FROM %s
            )
            RETURNING id
            """,
            (
                internal_id,
                reported_at,
                concurrency.get("scope"),
                concurrency.get("active_limit"),
                internal_id,
                reported_at,
                reported_at,
            ),
        )
        snapshot_id = (cur.fetchone() or [None])[0]
        if snapshot_id is None:
            return
        for bucket in ("active", "queued", "recent"):
            for item in state.get(bucket) if isinstance(state.get(bucket), list) else []:
                if not isinstance(item, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO download_items
                        (snapshot_id, job_id, state_bucket, asset_type, status, source_drone_id, system_name,
                         file_path, rom_path, bios_name, artwork_type, file_size, downloaded_bytes,
                         percentage, transfer_speed_bps, queue_position, failure_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        snapshot_id,
                        item.get("job_id") or item.get("id"),
                        bucket,
                        item.get("asset_type"),
                        item.get("status"),
                        item.get("source_drone_id"),
                        item.get("system") or item.get("system_name"),
                        item.get("file_path") or item.get("relative_path"),
                        item.get("rom_path"),
                        item.get("bios_name"),
                        item.get("artwork_type"),
                        item.get("file_size") or item.get("total_bytes"),
                        item.get("downloaded_bytes") or item.get("bytes_transferred"),
                        item.get("percentage"),
                        item.get("transfer_speed_bps"),
                        item.get("queue_position"),
                        item.get("failure_reason") or item.get("error_message"),
                    ),
                )

    def _insert_sync_activity(self, cur, internal_id: str, row: dict) -> None:
        cur.execute(
            """
            INSERT INTO sync_activity
                (id, target_drone_id, source_drone_id, asset_type, action, status, system_name, file_path,
                 rom_fingerprint, bios_md5, artwork_type, bytes_transferred, file_size, started_at, completed_at,
                 failure_reason, received_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                bytes_transferred = EXCLUDED.bytes_transferred,
                file_size = EXCLUDED.file_size,
                completed_at = EXCLUDED.completed_at,
                failure_reason = EXCLUDED.failure_reason,
                received_at = EXCLUDED.received_at
            """,
            (
                row.get("id") or row.get("sync_id") or str(uuid.uuid4()),
                internal_id,
                row.get("source_drone_id"),
                row.get("asset_type") or "rom",
                row.get("action") or "download",
                row.get("status") or "pending",
                row.get("system") or row.get("system_name"),
                row.get("relative_path") or row.get("rom_path") or row.get("bios_name") or row.get("file_path"),
                row.get("rom_fingerprint") or row.get("fingerprint"),
                row.get("bios_md5"),
                row.get("artwork_type"),
                row.get("bytes_transferred"),
                row.get("file_size"),
                self._dt(row.get("started_at") or row.get("download_started_at")),
                self._dt(row.get("completed_at") or row.get("download_completed_at")),
                row.get("failure_reason"),
                self._dt(row.get("received_at")),
            ),
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
                    self._clear_domain_assets(cur, device_internal_id, asset_type)
                else:
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s",
                        (device_internal_id,),
                    )
                    for kind in ("rom", "bios", "artwork", "saves"):
                        self._clear_domain_assets(cur, device_internal_id, kind)

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
                        (device_internal_id, device_id, asset_type, item_key, system_name, payload,
                         master_key, sort_key, updated_at)
                    SELECT device_internal_id, device_id, asset_type, item_key, system_name, payload,
                        CASE
                            WHEN asset_type = 'rom' THEN
                                CASE WHEN nullif(lower(coalesce(payload->>'rom_fingerprint', '')), '') IS NOT NULL
                                     THEN 'fingerprint:' || lower(payload->>'rom_fingerprint')
                                ELSE 'path:' || lower(coalesce(system_name, '')) || ':' ||
                                                lower(coalesce(payload->>'file_path', payload->>'rom_name', ''))
                                END
                            WHEN asset_type = 'bios' THEN
                                CASE WHEN nullif(lower(coalesce(payload->>'bios_md5', payload->>'md5', '')), '') IS NOT NULL
                                     THEN 'fingerprint:' || lower(coalesce(payload->>'bios_md5', payload->>'md5'))
                                ELSE 'path:' || lower(coalesce(payload->>'file_path',
                                                       payload->>'relative_path', payload->>'bios_name', ''))
                                END
                            ELSE NULL
                        END,
                        CASE
                            WHEN asset_type = 'rom'  THEN lower(coalesce(system_name, '')) || ':' ||
                                                           lower(coalesce(payload->>'file_path', payload->>'rom_name', ''))
                            WHEN asset_type = 'bios' THEN lower(coalesce(payload->>'file_path',
                                                                  payload->>'relative_path', payload->>'bios_name', ''))
                            ELSE NULL
                        END,
                        now()
                    FROM overmind_device_asset_staging
                    WHERE device_internal_id = %s AND inventory_id = %s
                    """,
                    (device_internal_id, inventory_id),
                )
                cur.execute(
                    "DELETE FROM overmind_device_asset_staging WHERE device_internal_id = %s",
                    (device_internal_id,),
                )
        if _cache:
            _cache.invalidate_master_assets()
            _cache.invalidate_asset_counts(device_internal_id)

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
                    cur.execute(
                        "DELETE FROM drone_roms WHERE drone_id = %s AND normalized_path = ANY(%s)",
                        (device_internal_id, [_domain_path(row, "rom") for row in source_rows]),
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
                    cur.execute(
                        "DELETE FROM drone_bios WHERE drone_id = %s AND normalized_path = ANY(%s)",
                        (device_internal_id, [_domain_path(row, "bios") for row in source_rows]),
                    )
                elif asset_type == "saves":
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s AND asset_type = %s AND item_key = ANY(%s)",
                        (device_internal_id, asset_type, keys),
                    )
                    cur.execute(
                        "DELETE FROM drone_saves WHERE drone_id = %s AND normalized_path = ANY(%s)",
                        (device_internal_id, [_domain_path(row, "saves") for row in source_rows]),
                    )
                else:
                    asset_systems = [
                        str(row.get("system_name") or row.get("system") or "").strip().lower()
                        for row in source_rows
                    ]
                    asset_paths = [
                        str(row.get("rom_path") or row.get("file_path") or "").replace("\\", "/").strip().lstrip("./").lower()
                        for row in source_rows
                    ]
                    asset_pairs = [(system, path) for system, path in zip(asset_systems, asset_paths) if system and path]
                    if keys or asset_pairs:
                        cur.execute(
                            """
                            DELETE FROM overmind_device_assets
                            WHERE device_internal_id = %s AND asset_type = %s
                              AND (
                                  item_key = ANY(%s)
                                  OR (
                                      lower(coalesce(system_name, payload->>'system', '')),
                                      lower(coalesce(payload->>'rom_path', payload->>'file_path', ''))
                                  ) IN (
                                      SELECT system_name, rom_path
                                      FROM unnest(%s::text[], %s::text[]) AS deleted(system_name, rom_path)
                                  )
                              )
                            """,
                            (
                                device_internal_id,
                                asset_type,
                                keys,
                                [item[0] for item in asset_pairs],
                                [item[1] for item in asset_pairs],
                            ),
                        )
                    artwork_delete_rows = []
                    for row in source_rows:
                        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
                        path = str(row.get("rom_path") or row.get("file_path") or "").replace("\\", "/").strip().lstrip("./").lower()
                        if not system or not path:
                            continue
                        for artwork_type in _artwork_types(row):
                            artwork_delete_rows.append((system, path, artwork_type.lower()))
                    if artwork_delete_rows:
                        cur.execute(
                            """
                            DELETE FROM drone_artwork
                            WHERE drone_id = %s
                              AND (
                                  lower(system_name),
                                  normalized_rom_path,
                                  lower(artwork_type)
                              ) IN (
                                  SELECT system_name, rom_path, artwork_type
                                  FROM unnest(%s::text[], %s::text[], %s::text[]) AS deleted(system_name, rom_path, artwork_type)
                              )
                            """,
                            (
                                device_internal_id,
                                [item[0] for item in artwork_delete_rows],
                                [item[1] for item in artwork_delete_rows],
                                [item[2] for item in artwork_delete_rows],
                            ),
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
            mk, sk = _compute_asset_keys(asset_type, payload, system_name)
            prepared.append((device_internal_id, device_id, asset_type, item_key, system_name, json.dumps(_encode_state(payload)), mk, sk))
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
                    self._clear_domain_assets(cur, device_internal_id, asset_type, replace_system=replace_system)
                elif replace:
                    cur.execute(
                        "DELETE FROM overmind_device_assets WHERE device_internal_id = %s AND asset_type = %s",
                        (device_internal_id, asset_type),
                    )
                    self._clear_domain_assets(cur, device_internal_id, asset_type)
                if prepared:
                    cur.executemany(
                        """
                        INSERT INTO overmind_device_assets
                            (device_internal_id, device_id, asset_type, item_key, system_name, payload, master_key, sort_key, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, now())
                        ON CONFLICT (device_internal_id, asset_type, item_key)
                        DO UPDATE SET device_id   = EXCLUDED.device_id,
                                      system_name = EXCLUDED.system_name,
                                      payload     = EXCLUDED.payload,
                                      master_key  = EXCLUDED.master_key,
                                      sort_key    = EXCLUDED.sort_key,
                                      updated_at  = now()
                        """,
                        prepared,
                    )
                    self._upsert_domain_assets(cur, device_internal_id, asset_type, [row for row in rows if isinstance(row, dict)])
        if _cache and prepared:
            _cache.invalidate_master_assets()
            _cache.invalidate_asset_counts(device_id)
        return row_ids

    def _ensure_system(self, cur, system_name: Optional[str]) -> Optional[int]:
        clean = str(system_name or "").strip()
        if not clean:
            return None
        cur.execute(
            """
            INSERT INTO systems (name, display_name)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET display_name = COALESCE(systems.display_name, EXCLUDED.display_name)
            RETURNING id
            """,
            (clean, clean),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    def _clear_domain_assets(self, cur, device_internal_id: str, asset_type: str, replace_system: Optional[str] = None) -> None:
        table = {"rom": "drone_roms", "bios": "drone_bios", "artwork": "drone_artwork", "saves": "drone_saves"}.get(asset_type)
        if not table:
            return
        if replace_system and asset_type in {"rom", "artwork"}:
            cur.execute(f"DELETE FROM {table} WHERE drone_id = %s AND lower(system_name) = lower(%s)", (device_internal_id, replace_system))
        else:
            cur.execute(f"DELETE FROM {table} WHERE drone_id = %s", (device_internal_id,))

    def _replace_domain_assets(self, cur, device_internal_id: str, asset_type: str, rows: list[dict]) -> None:
        self._clear_domain_assets(cur, device_internal_id, asset_type)
        self._upsert_domain_assets(cur, device_internal_id, asset_type, rows)

    def _upsert_domain_assets(self, cur, device_internal_id: str, asset_type: str, rows: Iterable[dict]) -> None:
        if asset_type == "rom":
            for row in rows:
                system_name = str(row.get("system_name") or row.get("system") or "").strip()
                path = _domain_path(row, "rom")
                if not system_name or not path:
                    continue
                cur.execute(
                    """
                    INSERT INTO drone_roms
                        (drone_id, system_id, system_name, file_path, normalized_path, rom_name, rom_fingerprint,
                         file_size, entry_type, metadata_source, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (drone_id, system_name, normalized_path) DO UPDATE SET
                        system_id = EXCLUDED.system_id,
                        file_path = EXCLUDED.file_path,
                        rom_name = EXCLUDED.rom_name,
                        rom_fingerprint = EXCLUDED.rom_fingerprint,
                        file_size = EXCLUDED.file_size,
                        entry_type = EXCLUDED.entry_type,
                        metadata_source = EXCLUDED.metadata_source,
                        last_seen = EXCLUDED.last_seen
                    """,
                    (
                        device_internal_id,
                        self._ensure_system(cur, system_name),
                        system_name,
                        row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_name"),
                        path,
                        row.get("rom_name") or row.get("name"),
                        row.get("rom_fingerprint") or row.get("fingerprint"),
                        row.get("file_size") or row.get("byte_count"),
                        row.get("entry_type") or "file",
                        row.get("metadata_source") or row.get("source"),
                        self._dt(row.get("last_seen") or row.get("added_at")),
                    ),
                )
        elif asset_type == "bios":
            for row in rows:
                path = _domain_path(row, "bios")
                if not path:
                    continue
                cur.execute(
                    """
                    INSERT INTO drone_bios
                        (drone_id, file_path, normalized_path, bios_name, bios_md5, file_size, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (drone_id, normalized_path) DO UPDATE SET
                        file_path = EXCLUDED.file_path,
                        bios_name = EXCLUDED.bios_name,
                        bios_md5 = EXCLUDED.bios_md5,
                        file_size = EXCLUDED.file_size,
                        last_seen = EXCLUDED.last_seen
                    """,
                    (
                        device_internal_id,
                        row.get("file_path") or row.get("relative_path") or row.get("path") or row.get("bios_name"),
                        path,
                        row.get("bios_name") or row.get("name"),
                        row.get("bios_md5") or row.get("md5"),
                        row.get("file_size") or row.get("byte_count"),
                        self._dt(row.get("last_seen") or row.get("added_at")),
                    ),
                )
        elif asset_type == "saves":
            for row in rows:
                system_name = str(row.get("system_name") or row.get("system") or "").strip()
                path = _domain_path(row, "saves")
                if not path:
                    continue
                cur.execute(
                    """
                    INSERT INTO drone_saves
                        (drone_id, system_id, system_name, file_path, normalized_path, save_name,
                         fingerprint, file_size, modified_time, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (drone_id, system_name, normalized_path) DO UPDATE SET
                        system_id = EXCLUDED.system_id,
                        file_path = EXCLUDED.file_path,
                        save_name = EXCLUDED.save_name,
                        fingerprint = EXCLUDED.fingerprint,
                        file_size = EXCLUDED.file_size,
                        modified_time = EXCLUDED.modified_time,
                        last_seen = EXCLUDED.last_seen
                    """,
                    (
                        device_internal_id,
                        self._ensure_system(cur, system_name) if system_name else None,
                        system_name,
                        row.get("file_path") or row.get("relative_path") or row.get("save_name"),
                        path,
                        row.get("save_name") or row.get("name"),
                        row.get("fingerprint") or row.get("saves_fingerprint"),
                        row.get("file_size") or row.get("byte_count"),
                        row.get("modified_time") or row.get("mtime"),
                        self._dt(row.get("last_seen") or row.get("added_at")),
                    ),
                )
        elif asset_type == "artwork":
            for row in rows:
                system_name = str(row.get("system_name") or row.get("system") or "").strip()
                path = _domain_path(row, "artwork")
                if not system_name or not path:
                    continue
                system_id = self._ensure_system(cur, system_name)
                for artwork_type in _artwork_types(row):
                    cur.execute(
                        """
                        INSERT INTO drone_artwork
                            (drone_id, system_id, system_name, rom_path, normalized_rom_path, rom_name,
                             title, artwork_type, last_seen)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                        ON CONFLICT (drone_id, system_name, normalized_rom_path, artwork_type) DO UPDATE SET
                            system_id = EXCLUDED.system_id,
                            rom_path = EXCLUDED.rom_path,
                            rom_name = EXCLUDED.rom_name,
                            title = EXCLUDED.title,
                            last_seen = EXCLUDED.last_seen
                        """,
                        (
                            device_internal_id,
                            system_id,
                            system_name,
                            row.get("rom_path") or row.get("file_path") or row.get("rom_name"),
                            path,
                            row.get("rom_name"),
                            row.get("title"),
                            artwork_type,
                            self._dt(row.get("last_seen") or row.get("added_at")),
                        ),
                    )

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

    def page_device_assets(
        self,
        device_internal_id: str,
        asset_type: str,
        *,
        system_name: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
        offset: Optional[int] = None,
    ) -> tuple[list[dict], int]:
        if not self.assets_enabled():
            return [], 0
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return [], 0
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        offset_value = max(0, int(offset)) if offset is not None else (page - 1) * per_page
        where = ["device_internal_id = %s", "asset_type = %s"]
        params: list[object] = [device_internal_id, asset_type]
        if system_name:
            where.append("lower(coalesce(system_name, '')) = lower(%s)")
            params.append(system_name)
        clean_query = str(query or "").strip().lower()
        if clean_query:
            like = f"%{clean_query}%"
            if asset_type == "saves":
                where.append(
                    """
                    (
                        lower(coalesce(system_name, '')) LIKE %s
                        OR lower(coalesce(payload->>'system', '')) LIKE %s
                        OR lower(coalesce(payload->>'save_name', payload->>'name', '')) LIKE %s
                        OR lower(coalesce(payload->>'file_path', payload->>'relative_path', '')) LIKE %s
                    )
                    """
                )
                params.extend([like, like, like, like])
            else:
                where.append("lower(payload::text) LIKE %s")
                params.append(like)
        where_sql = " AND ".join(where)
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM overmind_device_assets WHERE {where_sql}", params)
                total = int((cur.fetchone() or [0])[0] or 0)
                if not total:
                    return [], 0
                cur.execute(
                    f"""
                    SELECT payload
                    FROM overmind_device_assets
                    WHERE {where_sql}
                    ORDER BY system_name NULLS LAST, item_key
                    LIMIT %s OFFSET %s
                    """,
                    [*params, per_page, offset_value],
                )
                rows = cur.fetchall()
        return [_decode_state(row[0]) for row in rows], total

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
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        if _cache:
            cache_key = _cache.master_assets_key(
                ids, asset_type,
                selected=selected_internal_id, q=query, sys=system_name,
                st=status, art=artwork_type, pg=page, pp=per_page,
            )
            cached = _cache.get(cache_key)
            if cached is not None:
                return cached["rows"], cached["total"]
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return [], 0
        offset = (page - 1) * per_page
        if asset_type in ("rom", "bios"):
            # Fast path: use stored master_key/sort_key columns + covering index.
            clauses: list[str] = [
                "device_internal_id = ANY(%s)",
                "asset_type = %s",
                "master_key IS NOT NULL",
                "master_key <> ''",
            ]
            base_params: list[object] = [ids, asset_type]
            clean_query = str(query or "").strip().lower()
            if clean_query:
                # Pushed to base table so the GIN trgm index can be used.
                clauses.append("lower(payload::text) LIKE %s")
                base_params.append(f"%{clean_query}%")
            clean_system = str(system_name or "").strip().lower()
            if clean_system:
                clauses.append("lower(coalesce(system_name, '')) = %s")
                base_params.append(clean_system)
            clean_status = str(status or "").strip().lower()
            if selected_internal_id and clean_status in {"missing", "present"}:
                presence = "EXISTS" if clean_status == "present" else "NOT EXISTS"
                clauses.append(
                    f"{presence} (SELECT 1 FROM overmind_device_assets x"
                    f" WHERE x.device_internal_id = %s AND x.asset_type = %s AND x.master_key = overmind_device_assets.master_key)"
                )
                base_params.extend([str(selected_internal_id), asset_type])
            selected_param = str(selected_internal_id) if selected_internal_id else None
            where = " AND ".join(clauses)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        WITH filtered AS (
                            SELECT master_key, MIN(sort_key) AS sort_key
                            FROM overmind_device_assets
                            WHERE {where}
                            GROUP BY master_key
                        ),
                        counted AS (
                            SELECT master_key, sort_key, COUNT(*) OVER () AS total_count
                            FROM filtered
                        ),
                        paged AS (
                            SELECT master_key, total_count
                            FROM counted
                            ORDER BY sort_key, master_key
                            LIMIT %s OFFSET %s
                        )
                        SELECT a.device_internal_id, a.payload, a.master_key, NULL::text AS artwork_type,
                               CASE WHEN %s::text IS NULL THEN false ELSE EXISTS (
                                   SELECT 1 FROM overmind_device_assets s
                                   WHERE s.device_internal_id = %s AND s.asset_type = %s
                                     AND s.master_key = a.master_key
                               ) END AS present_on_selected,
                               p.total_count
                        FROM overmind_device_assets a
                        JOIN paged p ON p.master_key = a.master_key
                        WHERE a.device_internal_id = ANY(%s) AND a.asset_type = %s
                        ORDER BY a.sort_key, a.master_key, a.device_internal_id
                        """,
                        [*base_params, per_page, offset, selected_param, selected_param, asset_type, ids, asset_type],
                    )
                    rows = cur.fetchall()
        elif asset_type == "artwork":
            master_key_expr = """
                'artwork:' || lower(coalesce(system_name, payload->>'system', '')) || ':' ||
                lower(coalesce(payload->>'rom_path', payload->>'file_path', payload->>'rom_name', '')) || ':' ||
                lower(artwork_type.value)
            """
            sort_key_expr = """
                lower(coalesce(system_name, payload->>'system', '')) || ':' ||
                lower(coalesce(payload->>'rom_path', payload->>'file_path', payload->>'rom_name', '')) || ':' ||
                lower(artwork_type.value)
            """
            source = """
                overmind_device_assets
                CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(payload->'artwork_types', '[]'::jsonb)) AS artwork_type(value)
            """
            normalized_sql = f"""
                SELECT device_internal_id, device_id, payload, system_name, artwork_type.value AS artwork_type,
                       {master_key_expr} AS master_key,
                       {sort_key_expr} AS sort_key
                FROM {source}
                WHERE device_internal_id = ANY(%s) AND asset_type = %s
            """
            clauses_aw = ["n.master_key <> ''"]
            filters_aw: list[object] = []
            clean_query = str(query or "").strip().lower()
            if clean_query:
                clauses_aw.append("lower(n.payload::text) LIKE %s")
                filters_aw.append(f"%{clean_query}%")
            clean_system = str(system_name or "").strip().lower()
            if clean_system:
                clauses_aw.append("lower(coalesce(n.system_name, n.payload->>'system', '')) = %s")
                filters_aw.append(clean_system)
            clean_artwork_type = str(artwork_type or "").strip().lower()
            if clean_artwork_type:
                clauses_aw.append("lower(coalesce(n.artwork_type, '')) = %s")
                filters_aw.append(clean_artwork_type)
            selected_param = str(selected_internal_id) if selected_internal_id else None
            base_params_aw = [ids, asset_type, *filters_aw]
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        WITH normalized AS ({normalized_sql}),
                        filtered_keys AS (
                            SELECT n.master_key, min(n.sort_key) AS sort_key
                            FROM normalized n
                            WHERE {" AND ".join(clauses_aw)}
                            GROUP BY n.master_key
                        ),
                        counted_keys AS (
                            SELECT master_key, sort_key, COUNT(*) OVER () AS total_count
                            FROM filtered_keys
                        ),
                        paged_keys AS (
                            SELECT master_key, total_count
                            FROM counted_keys
                            ORDER BY sort_key, master_key
                            LIMIT %s OFFSET %s
                        )
                        SELECT n.device_internal_id, n.payload, n.master_key, n.artwork_type,
                               CASE WHEN %s::text IS NULL THEN false ELSE EXISTS (
                                   SELECT 1 FROM normalized selected
                                   WHERE selected.master_key = n.master_key AND selected.device_internal_id = %s
                               ) END AS present_on_selected,
                               p.total_count
                        FROM normalized n
                        JOIN paged_keys p ON p.master_key = n.master_key
                        ORDER BY n.sort_key, n.master_key, n.device_internal_id
                        """,
                        [*base_params_aw, per_page, offset, selected_param, selected_param],
                    )
                    rows = cur.fetchall()
        else:
            return [], 0
        if not rows:
            return [], 0
        total = int(rows[0][5] or 0)
        output = []
        for internal_id, payload, group_key, row_artwork_type, present_on_selected, _ in rows:
            decoded = _decode_state(payload)
            if isinstance(decoded, dict):
                decoded["_device_internal_id"] = internal_id
                decoded["_master_key"] = group_key
                decoded["_artwork_type"] = row_artwork_type
                decoded["_present_on_selected"] = bool(present_on_selected)
                output.append(decoded)
        if _cache:
            _cache.set(cache_key, {"rows": output, "total": total}, ttl=30)
        return output, total

    def summarize_rom_systems(self, device_internal_ids: Iterable[str]) -> list[dict]:
        ids = [str(value) for value in device_internal_ids if value]
        if not ids or not self.assets_enabled():
            return []
        if _cache:
            cache_key = _cache.rom_systems_key(ids)
            cached = _cache.get(cache_key)
            if cached is not None:
                return cached
        conn = self._connect()
        if conn is None:
            return []
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT system_name, count(*),
                        CASE
                            WHEN count(*) FILTER (
                                WHERE lower(coalesce(payload->>'metadata_source', payload->>'source', '')) = 'gamelist.xml'
                            ) = 0
                            THEN count(*)
                            ELSE count(*) FILTER (
                                WHERE lower(coalesce(payload->>'metadata_source', payload->>'source', '')) = 'gamelist.xml'
                            )
                        END,
                        count(DISTINCT device_internal_id)
                    FROM overmind_device_assets
                    WHERE device_internal_id = ANY(%s) AND asset_type = 'rom' AND system_name IS NOT NULL
                    GROUP BY system_name
                    ORDER BY system_name
                    """,
                    (ids,),
                )
                rows = cur.fetchall()
        result = [
            {"system_name": row[0], "rom_count": int(row[1]), "game_count": int(row[2]), "device_count": int(row[3])}
            for row in rows
        ]
        if _cache:
            _cache.set(cache_key, result, ttl=60)
        return result

    def page_device_rom_systems(
        self,
        device_internal_id: str,
        *,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[dict], int]:
        if not device_internal_id or not self.assets_enabled():
            return [], 0
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return [], 0
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 100))
        offset = (page - 1) * per_page
        where = [
            "device_internal_id = %s",
            "asset_type = 'rom'",
            "system_name IS NOT NULL",
        ]
        params: list[object] = [device_internal_id]
        clean_query = str(query or "").strip().lower()
        if clean_query:
            where.append("lower(system_name) LIKE %s")
            params.append(f"%{clean_query}%")
        where_sql = " AND ".join(where)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT count(*) FROM (
                        SELECT 1
                        FROM overmind_device_assets
                        WHERE {where_sql}
                        GROUP BY system_name
                    ) systems
                    """,
                    params,
                )
                total = int((cur.fetchone() or [0])[0] or 0)
                if not total:
                    return [], 0
                cur.execute(
                    f"""
                    WITH selected_systems AS (
                        SELECT system_name
                        FROM overmind_device_assets
                        WHERE {where_sql}
                        GROUP BY system_name
                        ORDER BY system_name
                        LIMIT %s OFFSET %s
                    ),
                    played AS (
                        SELECT system_name, max(played_at) AS last_played_at
                        FROM gameplay_sessions
                        WHERE drone_id = %s
                        GROUP BY system_name
                    )
                    SELECT s.system_name,
                           count(*) AS rom_count,
                           CASE
                               WHEN count(*) FILTER (
                                   WHERE lower(coalesce(a.payload->>'metadata_source', a.payload->>'source', '')) = 'gamelist.xml'
                               ) = 0
                               THEN count(*)
                               ELSE count(*) FILTER (
                                   WHERE lower(coalesce(a.payload->>'metadata_source', a.payload->>'source', '')) = 'gamelist.xml'
                               )
                           END AS game_count,
                           p.last_played_at
                    FROM selected_systems s
                    JOIN overmind_device_assets a
                      ON a.device_internal_id = %s
                     AND a.asset_type = 'rom'
                     AND a.system_name = s.system_name
                    LEFT JOIN played p ON p.system_name = s.system_name
                    GROUP BY s.system_name, p.last_played_at
                    ORDER BY s.system_name
                    """,
                    [*params, per_page, offset, device_internal_id, device_internal_id],
                )
                rows = cur.fetchall()
        return [
            {
                "system_name": row[0],
                "rom_count": int(row[1] or 0),
                "game_count": int(row[2] or 0),
                "last_played_at": row[3],
            }
            for row in rows
        ], total

    def count_device_assets(self, device_id: str, asset_type: str) -> Optional[int]:
        if _cache:
            cache_key = _cache.count_assets_key(device_id, asset_type)
            cached = _cache.get(cache_key)
            if cached is not None:
                return int(cached)
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM overmind_device_assets WHERE device_id = %s AND asset_type = %s",
                    (device_id, asset_type),
                )
                row = cur.fetchone()
                result = int(row[0] or 0) if row else 0
        if _cache:
            _cache.set(cache_key, result, ttl=60)
        return result

    def count_device_games(self, device_id: str) -> Optional[int]:
        """Count distinct games for a device: gamelist entries per system, falling
        back to the rom-file count for systems that have no gamelist. This is the
        number EmulationStation shows, as opposed to the raw rom-file count."""
        if _cache:
            cache_key = _cache.count_games_key(device_id)
            cached = _cache.get(cache_key)
            if cached is not None:
                return int(cached)
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT coalesce(sum(games), 0) FROM (
                        SELECT CASE
                            WHEN count(*) FILTER (
                                WHERE lower(coalesce(payload->>'metadata_source', payload->>'source', '')) = 'gamelist.xml'
                            ) = 0
                            THEN count(*)
                            ELSE count(*) FILTER (
                                WHERE lower(coalesce(payload->>'metadata_source', payload->>'source', '')) = 'gamelist.xml'
                            )
                        END AS games
                        FROM overmind_device_assets
                        WHERE device_id = %s AND asset_type = 'rom'
                        GROUP BY system_name
                    ) per_system
                    """,
                    (device_id,),
                )
                row = cur.fetchone()
                result = int(row[0] or 0) if row else 0
        if _cache:
            _cache.set(cache_key, result, ttl=300)
        return result

    def count_device_games_relational(self, device_id: str) -> Optional[int]:
        """Games count from the relational ``drone_roms`` table (asset store disabled)."""
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT coalesce(sum(games), 0) FROM (
                        SELECT CASE
                            WHEN count(*) FILTER (WHERE lower(coalesce(r.metadata_source, '')) = 'gamelist.xml') = 0
                            THEN count(*)
                            ELSE count(*) FILTER (WHERE lower(coalesce(r.metadata_source, '')) = 'gamelist.xml')
                        END AS games
                        FROM drone_roms r
                        JOIN drones d ON d.id = r.drone_id
                        WHERE d.device_id = %s
                        GROUP BY coalesce(r.system_name, '')
                    ) per_system
                    """,
                    (device_id,),
                )
                row = cur.fetchone()
                return int(row[0] or 0) if row else 0

    def update_rom_hashes(self, device_internal_id: str, patches: Iterable[dict]) -> None:
        if not self.assets_enabled():
            return
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return
        updates = []
        domain_updates = []
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            fingerprint_value = patch.get("rom_fingerprint") or patch.get("fingerprint") or patch.get("hash")
            if not fingerprint_value:
                continue
            item_key = _asset_key("rom", patch)
            if item_key:
                updates.append((str(fingerprint_value), device_internal_id, item_key))
            # The deferred hash patch must also land on the relational drone_roms
            # table; otherwise fingerprint stays NULL there (master list dedup, games count)
            # because the initial inventory wrote those rows before fingerprint existed.
            system_name = str(patch.get("system_name") or patch.get("system") or "").strip()
            normalized_path = _domain_path(patch, "rom")
            if system_name and normalized_path:
                domain_updates.append((str(fingerprint_value), device_internal_id, system_name, normalized_path))
        if not updates and not domain_updates:
            return
        with conn:
            with conn.cursor() as cur:
                if updates:
                    cur.executemany(
                        """
                        UPDATE overmind_device_assets
                        SET payload = jsonb_set(jsonb_set(payload, '{rom_fingerprint}', to_jsonb(%s::text), true), '{fingerprint}', to_jsonb(%s::text), true),
                            updated_at = now()
                        WHERE device_internal_id = %s AND asset_type = 'rom' AND item_key = %s
                        """,
                        [(fingerprint, fingerprint, internal_id, key) for fingerprint, internal_id, key in updates],
                    )
                if domain_updates:
                    cur.executemany(
                        """
                        UPDATE drone_roms
                        SET rom_fingerprint = %s, last_seen = now()
                        WHERE drone_id = %s AND lower(system_name) = lower(%s) AND normalized_path = %s
                        """,
                        domain_updates,
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


def _coerce_param_text(value) -> str:
    """Render a stored drone-action parameter (JSON text) as a plain display string."""
    if value is None:
        return ""
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(value)
    if isinstance(decoded, str):
        return decoded
    if isinstance(decoded, bool) or isinstance(decoded, (int, float)):
        return str(decoded)
    if isinstance(decoded, (list, dict)):
        return json.dumps(decoded)
    return "" if decoded is None else str(decoded)


def _merge_state_dicts(base: dict, overlay: dict) -> None:
    if not isinstance(overlay, dict):
        return
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            if key in {"users", "devices"}:
                for row_id, row_value in value.items():
                    if isinstance(row_value, dict) and isinstance(base[key].get(row_id), dict):
                        base[key][row_id].update({field: item for field, item in row_value.items() if item is not None})
                    else:
                        base[key][row_id] = row_value
            elif key in {"swarm_memberships"}:
                for row_id, row_value in value.items():
                    base[key].setdefault(row_id, {})
                    if isinstance(row_value, dict):
                        base[key][row_id].update(row_value)
                    else:
                        base[key][row_id] = row_value
            else:
                base[key].update(value)
        elif value:
            base[key] = value


def _strip_json_only_device_status(state: dict) -> None:
    devices = state.get("devices")
    if not isinstance(devices, dict):
        return
    for device in devices.values():
        if not isinstance(device, dict):
            continue
        device.pop("last_known_status", None)
        device.pop("last_status_checked_at", None)


def _asset_key(asset_type: str, row: dict) -> str:
    if asset_type == "rom":
        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
        path = str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_file") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return f"{system}:{path}" if system and path else ""
    if asset_type == "bios":
        fingerprint = str(row.get("bios_md5") or row.get("md5") or row.get("hash") or "").strip().lower()
        path = str(row.get("file_path") or row.get("relative_path") or row.get("path") or row.get("bios_name") or row.get("name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return f"fingerprint:{fingerprint}" if fingerprint else f"path:{path}" if path else ""
    if asset_type == "artwork":
        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
        path = str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        types = row.get("artwork_types") if isinstance(row.get("artwork_types"), list) else []
        type_key = ",".join(sorted(str(value).strip().lower() for value in types if str(value).strip()))
        return f"{system}:{path}:{type_key}" if system and path and type_key else ""
    if asset_type == "saves":
        system = str(row.get("system_name") or row.get("system") or "").strip().lower()
        path = str(row.get("file_path") or row.get("relative_path") or row.get("save_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return f"{system}:{path}" if path else ""
    return ""


def _domain_path(row: dict, asset_type: str) -> str:
    if asset_type == "rom":
        value = row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_file") or row.get("rom_name")
    elif asset_type == "bios":
        value = row.get("file_path") or row.get("relative_path") or row.get("path") or row.get("bios_name") or row.get("name")
    elif asset_type == "saves":
        value = row.get("file_path") or row.get("relative_path") or row.get("save_name")
    else:
        value = row.get("rom_path") or row.get("file_path") or row.get("rom_name")
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def _artwork_types(row: dict) -> list[str]:
    if isinstance(row.get("artwork_types"), list):
        values = row["artwork_types"]
    elif row.get("artwork_type"):
        values = [row.get("artwork_type")]
    else:
        values = []
    return sorted({str(value).strip() for value in values if str(value).strip()})


postgres_store = PostgresMetadataStore()
