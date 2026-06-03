"""Optional PostgreSQL storage for collected Drone metadata."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Iterable, Optional

logger = logging.getLogger("overmind.postgres_store")


def _is_excluded_emulator_config_path(value: str) -> bool:
    label = str(value or "").replace("\\", "/").strip("/")
    lowered = label.lower()
    if ".bak" in lowered:
        return True
    return bool({"log", "logs"} & {part for part in lowered.split("/") if part})


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
        return max(0.0, float(os.getenv("OVERMIND_POSTGRES_QUERY_LOG_MIN_MS", "0")))
    except (TypeError, ValueError):
        return 0.0


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
            timeout = max(1, int(os.getenv("OVERMIND_POSTGRES_CONNECT_TIMEOUT_SECONDS", "3")))
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

    def touch_device_last_seen(self, internal_id: str) -> bool:
        """Update a Drone's liveness timestamp with a minimal write path."""
        conn = self._core_connection()
        if conn is None or not internal_id:
            return False
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE drones SET last_seen = now() WHERE id = %s", (internal_id,))
                return cur.rowcount > 0

    def ensure_schema(self) -> None:
        if self._ready or not self.url:
            return
        conn = self._connect()
        if conn is None:
            return
        with conn:
            with conn.cursor() as cur:
                if os.getenv("OVERMIND_RESET_RELATIONAL_SCHEMA", "").lower() == "true":
                    self._drop_existing_schema(cur)
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
                self._ensure_relational_schema(cur)
        self._ready = True
        self.last_error = None

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

    def _ensure_relational_schema(self, cur) -> None:
        """Create the normalized Overmind schema used by the relational repository."""
        statements = [
            """
            CREATE TABLE IF NOT EXISTS overmind_schema_versions (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                email_verified BOOLEAN NOT NULL DEFAULT false,
                is_active BOOLEAN NOT NULL DEFAULT false,
                auth_provider TEXT NOT NULL DEFAULT 'password',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                username TEXT UNIQUE,
                full_name TEXT,
                avatar_data_url TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_auth_identities (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_subject TEXT,
                provider_email TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_login_at TIMESTAMPTZ,
                UNIQUE(provider, provider_subject),
                UNIQUE(user_id, provider)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_fleet_settings (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                auto_sync_roms BOOLEAN NOT NULL DEFAULT true,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_notification_settings (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                notify_slack BOOLEAN NOT NULL DEFAULT false,
                notify_discord BOOLEAN NOT NULL DEFAULT false,
                notify_email BOOLEAN NOT NULL DEFAULT true,
                slack_webhook TEXT,
                discord_webhook TEXT,
                email_address TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_notification_type_settings (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT true,
                PRIMARY KEY (user_id, event_type)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS email_verifications (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                used_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                used_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS swarms (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                is_public BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS swarm_memberships (
                swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('overlord', 'overseer')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (swarm_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS swarm_invitations (
                id TEXT PRIMARY KEY,
                swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('overlord', 'overseer')),
                token_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                invited_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                resent_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ NOT NULL,
                accepted_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drones (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL UNIQUE,
                device_name TEXT NOT NULL,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL,
                approval_status TEXT NOT NULL DEFAULT 'approved',
                swarm_connected BOOLEAN NOT NULL DEFAULT true,
                authorization_token_id TEXT,
                drone_token_hash TEXT,
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ,
                removed_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_network_state (
                drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
                api_port INTEGER,
                scheme TEXT,
                reachable_url TEXT,
                public_resolvable BOOLEAN NOT NULL DEFAULT false,
                public_ip TEXT,
                checked_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_network_addresses (
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                address_type TEXT NOT NULL CHECK (address_type IN ('ipv4', 'ipv6', 'hostname', 'mac')),
                address TEXT NOT NULL,
                interface_name TEXT,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (drone_id, address_type, address)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_system_info (
                drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
                hostname TEXT,
                model TEXT,
                system_name TEXT,
                architecture TEXT,
                cpu_model TEXT,
                cpu_cores INTEGER,
                cpu_threads INTEGER,
                cpu_max_frequency TEXT,
                memory_available TEXT,
                memory_total TEXT,
                batocera_version TEXT,
                container BOOLEAN,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_performance_metrics (
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                metric_group TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value DOUBLE PRECISION,
                metric_text TEXT,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (drone_id, metric_group, metric_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_certificates (
                drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
                status TEXT,
                fingerprint TEXT,
                sha256_fingerprint TEXT,
                public_certificate TEXT,
                subject TEXT,
                issuer TEXT,
                valid_from TIMESTAMPTZ,
                valid_until TIMESTAMPTZ,
                serial_number TEXT,
                overmind_signed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_certificate_sans (
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                san TEXT NOT NULL,
                PRIMARY KEY (drone_id, san)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_auto_sync_policies (
                drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
                enabled BOOLEAN NOT NULL DEFAULT false,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_auto_sync_policy_systems (
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                system_name TEXT NOT NULL,
                PRIMARY KEY (drone_id, system_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS device_admin_claims (
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (drone_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS integration_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                bound_device_id TEXT,
                bound_fingerprint TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_used_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approved_drone_tokens (
                device_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL,
                integration_token_id TEXT REFERENCES integration_tokens(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                revoked_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pending_drone_connections (
                device_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL,
                device_name TEXT NOT NULL,
                batocera_info JSONB,
                authorization_token_id TEXT REFERENCES integration_tokens(id) ON DELETE SET NULL,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS systems (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS asset_inventory_batches (
                id TEXT PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                update_mode TEXT NOT NULL,
                replace_all BOOLEAN NOT NULL DEFAULT false,
                chunk_index INTEGER,
                chunk_total INTEGER,
                inventory_complete BOOLEAN,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_roms (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                system_id BIGINT REFERENCES systems(id) ON DELETE SET NULL,
                system_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                normalized_path TEXT NOT NULL,
                rom_name TEXT,
                rom_md5 TEXT,
                file_size BIGINT,
                entry_type TEXT NOT NULL DEFAULT 'file',
                metadata_source TEXT,
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (drone_id, system_name, normalized_path)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_bios (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                normalized_path TEXT NOT NULL,
                bios_name TEXT,
                bios_md5 TEXT,
                file_size BIGINT,
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (drone_id, normalized_path)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_artwork (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                system_id BIGINT REFERENCES systems(id) ON DELETE SET NULL,
                system_name TEXT NOT NULL,
                rom_path TEXT NOT NULL,
                normalized_rom_path TEXT NOT NULL,
                rom_name TEXT,
                title TEXT,
                artwork_type TEXT NOT NULL,
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (drone_id, system_name, normalized_rom_path, artwork_type)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_actions (
                id TEXT PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                claimed_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                message TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_action_parameters (
                action_id TEXT NOT NULL REFERENCES drone_actions(id) ON DELETE CASCADE,
                parameter_name TEXT NOT NULL,
                parameter_value TEXT,
                PRIMARY KEY (action_id, parameter_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_action_result_records (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT REFERENCES drones(id) ON DELETE CASCADE,
                device_id TEXT,
                action_id TEXT,
                result_type TEXT,
                status TEXT,
                message TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_action_result_fields (
                result_id BIGINT NOT NULL REFERENCES drone_action_result_records(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                field_value TEXT,
                PRIMARY KEY (result_id, field_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS download_snapshots (
                id BIGSERIAL PRIMARY KEY,
                target_drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                concurrency_scope TEXT,
                active_limit INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS download_items (
                id BIGSERIAL PRIMARY KEY,
                snapshot_id BIGINT NOT NULL REFERENCES download_snapshots(id) ON DELETE CASCADE,
                job_id TEXT,
                state_bucket TEXT NOT NULL CHECK (state_bucket IN ('active', 'queued', 'recent')),
                asset_type TEXT,
                status TEXT,
                source_drone_id TEXT,
                system_name TEXT,
                file_path TEXT,
                rom_path TEXT,
                bios_name TEXT,
                artwork_type TEXT,
                file_size BIGINT,
                downloaded_bytes BIGINT,
                percentage DOUBLE PRECISION,
                transfer_speed_bps DOUBLE PRECISION,
                queue_position INTEGER,
                failure_reason TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sync_activity (
                id TEXT PRIMARY KEY,
                target_drone_id TEXT REFERENCES drones(id) ON DELETE CASCADE,
                source_drone_id TEXT,
                asset_type TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'download',
                status TEXT NOT NULL DEFAULT 'pending',
                system_name TEXT,
                file_path TEXT,
                rom_md5 TEXT,
                bios_md5 TEXT,
                artwork_type TEXT,
                bytes_transferred BIGINT,
                file_size BIGINT,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                failure_reason TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS gameplay_sessions (
                id TEXT PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                system_name TEXT,
                game_name TEXT NOT NULL,
                rom_path TEXT,
                rom_md5 TEXT,
                played_at TIMESTAMPTZ,
                duration_seconds INTEGER,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_log_sources (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (drone_id, source)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_log_files (
                id BIGSERIAL PRIMARY KEY,
                source_id BIGINT NOT NULL REFERENCES drone_log_sources(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                content TEXT NOT NULL,
                modified_at TIMESTAMPTZ,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_id, path)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_emulator_configs (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                root TEXT,
                relative_path TEXT NOT NULL,
                current_content TEXT NOT NULL,
                md5 TEXT,
                fingerprint TEXT,
                size_bytes BIGINT,
                truncated BOOLEAN NOT NULL DEFAULT false,
                error TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (drone_id, relative_path)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_emulator_config_versions (
                id BIGSERIAL PRIMARY KEY,
                config_id BIGINT NOT NULL REFERENCES drone_emulator_configs(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                md5 TEXT,
                fingerprint TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_speed_samples (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                upload_mbps DOUBLE PRECISION,
                download_mbps DOUBLE PRECISION,
                latency_ms DOUBLE PRECISION,
                measured_at TIMESTAMPTZ,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_events (
                id BIGSERIAL PRIMARY KEY,
                drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                severity TEXT,
                message TEXT,
                occurred_at TIMESTAMPTZ,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_event_fields (
                event_id BIGINT NOT NULL REFERENCES drone_events(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                field_value TEXT,
                PRIMARY KEY (event_id, field_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_peer_checks (
                id BIGSERIAL PRIMARY KEY,
                source_drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
                target_drone_id TEXT NOT NULL,
                target_address TEXT,
                status TEXT NOT NULL,
                latency_ms DOUBLE PRECISION,
                checked_at TIMESTAMPTZ,
                error TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                delivery_pending BOOLEAN NOT NULL DEFAULT false,
                delivery_completed_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS notification_fields (
                notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
                field_name TEXT NOT NULL,
                field_value TEXT,
                PRIMARY KEY (notification_id, field_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS notification_recipients (
                notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                read_at TIMESTAMPTZ,
                dismissed_at TIMESTAMPTZ,
                delivery_pending BOOLEAN NOT NULL DEFAULT false,
                PRIMARY KEY (notification_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
                id BIGSERIAL PRIMARY KEY,
                notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                error TEXT
            )
            """,
        ]
        for statement in statements:
            cur.execute(statement)
        migrations = [
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved'",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_connected BOOLEAN NOT NULL DEFAULT true",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS authorization_token_id TEXT",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_token_hash TEXT",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ",
            "ALTER TABLE drones ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS api_port INTEGER",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS scheme TEXT",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS reachable_url TEXT",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS public_resolvable BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS public_ip TEXT",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ",
            "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS hostname TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS model TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS system_name TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS architecture TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_model TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_cores INTEGER",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_threads INTEGER",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_max_frequency TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS memory_available TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS memory_total TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS batocera_version TEXT",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS container BOOLEAN",
            "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS md5 TEXT",
            "ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS fingerprint TEXT",
            "ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS size_bytes BIGINT",
            "ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS truncated BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS error TEXT",
            "ALTER TABLE drone_emulator_config_versions ADD COLUMN IF NOT EXISTS md5 TEXT",
            "ALTER TABLE drone_emulator_config_versions ADD COLUMN IF NOT EXISTS fingerprint TEXT",
            "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_pending BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_completed_at TIMESTAMPTZ",
        ]
        for statement in migrations:
            cur.execute(statement)
        cur.execute("ALTER TABLE pending_drone_connections ADD COLUMN IF NOT EXISTS batocera_info JSONB")
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_drones_user_swarm ON drones(user_id, swarm_id)",
            "CREATE INDEX IF NOT EXISTS idx_drones_device_id ON drones(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_roms_swarm_search ON drone_roms(system_name, rom_md5, normalized_path)",
            "CREATE INDEX IF NOT EXISTS idx_roms_drone_system ON drone_roms(drone_id, system_name)",
            "CREATE INDEX IF NOT EXISTS idx_bios_hash ON drone_bios(bios_md5)",
            "CREATE INDEX IF NOT EXISTS idx_artwork_lookup ON drone_artwork(system_name, normalized_rom_path, artwork_type)",
            "CREATE INDEX IF NOT EXISTS idx_actions_drone_status ON drone_actions(drone_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_sync_activity_target ON sync_activity(target_drone_id, received_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_gameplay_drone_system ON gameplay_sessions(drone_id, system_name, played_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_gameplay_drone_received ON gameplay_sessions(drone_id, received_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_speed_samples_drone_received ON drone_speed_samples(drone_id, received_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_events_drone_received ON drone_events(drone_id, received_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_peer_checks_source_received ON drone_peer_checks(source_drone_id, received_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_swarm_created ON notifications(swarm_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_pending_delivery ON notifications(delivery_pending, created_at) WHERE delivery_pending IS TRUE AND delivery_completed_at IS NULL",
        ]
        for statement in indexes:
            cur.execute(statement)
        cur.execute(
            """
            INSERT INTO overmind_schema_versions (id, version, applied_at)
            VALUES ('relational', 1, now())
            ON CONFLICT (id)
            DO UPDATE SET version = GREATEST(overmind_schema_versions.version, EXCLUDED.version),
                          applied_at = EXCLUDED.applied_at
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

    def store_device_emulator_configs(self, internal_device_id: str, payload: dict, max_versions: int = 10) -> None:
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
                        (config_id, config_id, max(1, int(max_versions or 10))),
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

    def get_device_emulator_configs(self, internal_device_id: str, max_versions: int = 10) -> dict:
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
                        (config_id, max(1, int(max_versions or 10))),
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
                   n.api_port, n.scheme, n.reachable_url, n.public_resolvable, n.public_ip, n.checked_at,
                   s.hostname, s.model, s.system_name, s.architecture, s.cpu_model, s.cpu_cores, s.cpu_threads,
                   s.cpu_max_frequency, s.memory_available, s.memory_total, s.batocera_version, s.container
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
                api_port, scheme, reachable_url, public_resolvable, public_ip, checked_at,
                hostname, model, system_name, architecture, cpu_model, cpu_cores, cpu_threads,
                cpu_max_frequency, memory_available, memory_total, batocera_version, container,
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
                    "container": container,
                },
            }
            state["devices"][internal_id] = device
            state["user_devices"].setdefault(user_id, []).append(internal_id)

        active_drone_ids = list(state["devices"])
        history_limit = _env_int("OVERMIND_RELATIONAL_HISTORY_ROWS_PER_DRONE", 200)
        action_limit = _env_int("OVERMIND_RELATIONAL_ACTION_ROWS_PER_DRONE", 100)
        notification_limit = _env_int("OVERMIND_RELATIONAL_NOTIFICATIONS_PER_SWARM", 500)

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
            SELECT g.id, g.drone_id, g.system_name, g.game_name, g.rom_path, g.rom_md5,
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
        for gameplay_id, drone_id, system_name, game_name, rom_path, rom_md5, played_at, duration_seconds, received_at in cur.fetchall():
            state["gamelogs"].setdefault(drone_id, []).append({
                "id": gameplay_id,
                "system_name": system_name,
                "game_name": game_name,
                "name": game_name,
                "rom_path": rom_path,
                "rom_md5": rom_md5,
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
                   a.status, a.system_name, a.file_path, a.rom_md5, a.bios_md5, a.artwork_type,
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
        for sync_id, target_internal_id, target_device_id, source_drone_id, asset_type, action, activity_status, system_name, file_path, rom_md5, bios_md5, artwork_type, bytes_transferred, file_size, started_at, completed_at, failure_reason, received_at in cur.fetchall():
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
                "rom_md5": rom_md5,
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
            SELECT device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id, requested_at, status
            FROM pending_drone_connections
            WHERE user_id = ANY(%s) OR swarm_id = ANY(%s)
            """,
            (active_user_ids, active_swarm_ids),
        )
        for device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id, requested_at, status in cur.fetchall():
            state["pending_drone_connections"][device_id] = {
                "id": device_id,
                "user_id": user_id,
                "swarm_id": swarm_id,
                "device_id": device_id,
                "device_name": device_name,
                "batocera_info": _decode_state(batocera_info) if isinstance(batocera_info, dict) else {},
                "authorization_token_id": authorization_token_id,
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
            api_port, scheme, reachable_url, public_resolvable, public_ip, checked_at,
            hostname, model, system_name, architecture, cpu_model, cpu_cores, cpu_threads,
            cpu_max_frequency, memory_available, memory_total, batocera_version, container,
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
            "api_port": api_port,
            "scheme": scheme or "https",
            "reachable_url": reachable_url,
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
                "container": container,
            },
            "batocera_info": {},
            "certificate": certificate,
            "auto_sync_policy": {"enabled": bool(auto_sync_enabled), "systems": list(auto_sync_systems or [])},
            "rom_systems": list(auto_sync_systems or []),
            "last_speed_sample": _decode_state(last_speed_sample) if isinstance(last_speed_sample, dict) else last_speed_sample,
        }

    def _select_device_sql(self, where_clause: str) -> str:
        return f"""
            SELECT d.id, d.device_id, d.device_name, d.user_id, d.swarm_id, d.approval_status,
                   d.swarm_connected, d.authorization_token_id, d.drone_token_hash,
                   d.registered_at, d.last_seen, d.removed_at,
                   ns.api_port, ns.scheme, ns.reachable_url, ns.public_resolvable, ns.public_ip, ns.checked_at,
                   si.hostname, si.model, si.system_name, si.architecture, si.cpu_model, si.cpu_cores,
                   si.cpu_threads, si.cpu_max_frequency, si.memory_available, si.memory_total,
                   si.batocera_version, si.container,
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
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_device_sql("d.id = %s"), (internal_id,))
                return self._device_from_row(cur.fetchone())

    def get_device_by_device_id(self, device_id: str) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_device_sql("d.device_id = %s"), (device_id,))
                return self._device_from_row(cur.fetchone())

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
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
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
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_device_sql(where) + " ORDER BY d.device_name ASC, d.device_id ASC", tuple(params))
                return [device for device in (self._device_from_row(row) for row in cur.fetchall()) if device]

    def user_can_access_device(self, user_id: str, device_id: str, swarm_id: Optional[str] = None) -> Optional[dict]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
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
        with conn:
            with conn.cursor() as cur:
                cur.execute(self._select_device_sql(where), tuple(params))
                return self._device_from_row(cur.fetchone())

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

    def list_user_notifications(self, user_id: str, limit: int = 50) -> Optional[list[dict]]:
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        row_limit = max(1, min(int(limit or 50), 500))
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
                    LIMIT %s
                    """,
                    (user_id, user_id, row_limit),
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

    def list_device_actions(self, user_id: str, device_id: str) -> Optional[list[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device or device.get("user_id") != user_id:
            return None
        conn = self._core_connection(ensure_schema=False)
        if conn is None:
            return None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.id, a.action, a.status, a.created_at, a.claimed_at, a.completed_at, a.message
                    FROM drone_actions a
                    WHERE a.drone_id = %s AND a.status IN ('pending', 'claimed', 'in_progress')
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
                     authorization_token_id, drone_token_hash, registered_at, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    device_name = EXCLUDED.device_name,
                    user_id = EXCLUDED.user_id,
                    swarm_id = EXCLUDED.swarm_id,
                    approval_status = EXCLUDED.approval_status,
                    swarm_connected = EXCLUDED.swarm_connected,
                    authorization_token_id = EXCLUDED.authorization_token_id,
                    drone_token_hash = EXCLUDED.drone_token_hash,
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
        cur.execute(
            """
            INSERT INTO drone_network_state
                (drone_id, api_port, scheme, reachable_url, public_resolvable, public_ip, checked_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (drone_id) DO UPDATE SET
                api_port = EXCLUDED.api_port,
                scheme = EXCLUDED.scheme,
                reachable_url = EXCLUDED.reachable_url,
                public_resolvable = EXCLUDED.public_resolvable,
                public_ip = EXCLUDED.public_ip,
                checked_at = EXCLUDED.checked_at,
                updated_at = now()
            """,
            (
                device.get("id"),
                device.get("api_port"),
                device.get("scheme") or "https",
                device.get("reachable_url"),
                bool(reachability.get("resolvable")),
                reachability.get("public_ip") or network.get("public_ip") or network.get("public"),
                self._dt(reachability.get("checked_at")),
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
        cur.execute(
            """
            INSERT INTO drone_system_info
                (drone_id, hostname, model, system_name, architecture, cpu_model, cpu_cores, cpu_threads,
                 cpu_max_frequency, memory_available, memory_total, batocera_version, container, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
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
                info.get("container"),
            ),
        )
        if isinstance(info.get("performance"), dict):
            cur.execute("DELETE FROM drone_performance_metrics WHERE drone_id = %s", (device.get("id"),))
            for group, values in info["performance"].items():
                if not isinstance(values, dict):
                    continue
                for name, value in values.items():
                    number = value if isinstance(value, (int, float)) else None
                    text = None if number is not None else str(value)
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
        pending_device_ids = [
            str(conn.get("device_id"))
            for conn in pending.values()
            if isinstance(conn, dict) and conn.get("device_id")
        ]
        if pending_device_ids:
            cur.execute("DELETE FROM pending_drone_connections WHERE NOT (device_id = ANY(%s))", (pending_device_ids,))
        else:
            cur.execute("DELETE FROM pending_drone_connections")
        for conn in pending.values():
            if not isinstance(conn, dict) or not conn.get("device_id") or not conn.get("user_id"):
                continue
            cur.execute(
                """
                INSERT INTO pending_drone_connections
                    (device_id, user_id, swarm_id, device_name, batocera_info, authorization_token_id, requested_at, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    swarm_id = EXCLUDED.swarm_id,
                    device_name = EXCLUDED.device_name,
                    batocera_info = EXCLUDED.batocera_info,
                    authorization_token_id = EXCLUDED.authorization_token_id,
                    status = EXCLUDED.status
                """,
                (
                    conn.get("device_id"),
                    conn.get("user_id"),
                    conn.get("swarm_id"),
                    conn.get("device_name") or conn.get("device_id"),
                    self._json(conn.get("batocera_info") if isinstance(conn.get("batocera_info"), dict) else {}),
                    conn.get("authorization_token_id"),
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
                        (id, drone_id, system_name, game_name, rom_path, rom_md5, played_at, duration_seconds, received_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        system_name = EXCLUDED.system_name,
                        game_name = EXCLUDED.game_name,
                        rom_path = EXCLUDED.rom_path,
                        rom_md5 = EXCLUDED.rom_md5,
                        played_at = EXCLUDED.played_at,
                        duration_seconds = EXCLUDED.duration_seconds
                    """,
                    (
                        row.get("id") or f"{internal_id}:{row.get('played_at')}:{row.get('game_name')}",
                        internal_id,
                        row.get("system_name"),
                        row.get("game_name") or row.get("name") or "Unknown game",
                        row.get("rom_path"),
                        row.get("rom_md5"),
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
                 rom_md5, bios_md5, artwork_type, bytes_transferred, file_size, started_at, completed_at,
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
                row.get("rom_md5") or row.get("md5"),
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
                    for kind in ("rom", "bios", "artwork"):
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
                        for artwork_type in _artwork_types(row):
                            cur.execute(
                                """
                                DELETE FROM drone_artwork
                                WHERE drone_id = %s
                                  AND lower(system_name) = %s
                                  AND normalized_rom_path = %s
                                  AND lower(artwork_type) = %s
                                """,
                                (device_internal_id, system, path, artwork_type.lower()),
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
                    self._upsert_domain_assets(cur, device_internal_id, asset_type, [row for row in rows if isinstance(row, dict)])
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
        table = {"rom": "drone_roms", "bios": "drone_bios", "artwork": "drone_artwork"}.get(asset_type)
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
                        (drone_id, system_id, system_name, file_path, normalized_path, rom_name, rom_md5,
                         file_size, entry_type, metadata_source, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (drone_id, system_name, normalized_path) DO UPDATE SET
                        system_id = EXCLUDED.system_id,
                        file_path = EXCLUDED.file_path,
                        rom_name = EXCLUDED.rom_name,
                        rom_md5 = EXCLUDED.rom_md5,
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
                        row.get("rom_md5") or row.get("md5"),
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
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[dict], int]:
        if not self.assets_enabled():
            return [], 0
        self.ensure_schema()
        conn = self._connect()
        if conn is None:
            return [], 0
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        offset = (page - 1) * per_page
        where = ["device_internal_id = %s", "asset_type = %s"]
        params: list[object] = [device_internal_id, asset_type]
        if system_name:
            where.append("lower(coalesce(system_name, '')) = lower(%s)")
            params.append(system_name)
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
                    [*params, per_page, offset],
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


def _domain_path(row: dict, asset_type: str) -> str:
    if asset_type == "rom":
        value = row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_file") or row.get("rom_name")
    elif asset_type == "bios":
        value = row.get("file_path") or row.get("relative_path") or row.get("path") or row.get("bios_name") or row.get("name")
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
