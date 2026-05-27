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
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (drone_id, relative_path)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drone_emulator_config_versions (
                id BIGSERIAL PRIMARY KEY,
                config_id BIGINT NOT NULL REFERENCES drone_emulator_configs(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
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
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
            "CREATE INDEX IF NOT EXISTS idx_notifications_swarm_created ON notifications(swarm_id, created_at DESC)",
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
