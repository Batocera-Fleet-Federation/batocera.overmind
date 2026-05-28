"""Optional PostgreSQL storage for collected Drone metadata."""

from __future__ import annotations

import json
import os
import uuid
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
        state = None
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM overmind_app_state WHERE id = %s", ("default",))
                row = cur.fetchone()
                if row:
                    state = _decode_state(row[0])
                relational = self._load_relational_state(cur)
        if not isinstance(state, dict):
            state = {}
        _merge_state_dicts(state, relational)
        return state if state else None

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

        cur.execute("SELECT user_id, event_type, enabled FROM user_notification_type_settings")
        for user_id, event_type, enabled in cur.fetchall():
            user = state["users"].get(user_id)
            if user:
                user["notification_settings"].setdefault("types", {})[event_type] = bool(enabled)

        cur.execute("SELECT id, owner_user_id, name, created_at FROM swarms")
        for swarm_id, owner_id, name, created_at in cur.fetchall():
            state["swarms"][swarm_id] = {"id": swarm_id, "owner_id": owner_id, "name": name, "created_at": created_at}
        cur.execute("SELECT swarm_id, user_id, role, created_at FROM swarm_memberships")
        for swarm_id, user_id, role, created_at in cur.fetchall():
            state["swarm_memberships"].setdefault(swarm_id, {})[user_id] = {"user_id": user_id, "role": role, "created_at": created_at}

        cur.execute(
            """
            SELECT id, user_id, label, token_hash, bound_device_id, bound_fingerprint, created_at, last_used_at, revoked_at
            FROM integration_tokens
            """
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

        cur.execute("SELECT drone_id, user_id FROM device_admin_claims")
        for drone_id, user_id in cur.fetchall():
            state["device_admin_claims"].setdefault(drone_id, []).append(user_id)

        cur.execute("SELECT device_id, user_id, swarm_id, device_name, authorization_token_id, requested_at, status FROM pending_drone_connections")
        for device_id, user_id, swarm_id, device_name, authorization_token_id, requested_at, status in cur.fetchall():
            state["pending_drone_connections"][device_id] = {
                "id": device_id,
                "user_id": user_id,
                "swarm_id": swarm_id,
                "device_id": device_id,
                "device_name": device_name,
                "authorization_token_id": authorization_token_id,
                "detected_at": requested_at,
                "last_seen": requested_at,
                "status": status,
            }
        return state

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
                self._mirror_app_state_to_relational(cur, state)

    def _json(self, value) -> str:
        return json.dumps(_encode_state(value), default=str)

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
        for conn in pending.values():
            if not isinstance(conn, dict) or not conn.get("device_id") or not conn.get("user_id"):
                continue
            cur.execute(
                """
                INSERT INTO pending_drone_connections
                    (device_id, user_id, swarm_id, device_name, authorization_token_id, requested_at, status)
                VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s)
                ON CONFLICT (device_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    swarm_id = EXCLUDED.swarm_id,
                    device_name = EXCLUDED.device_name,
                    authorization_token_id = EXCLUDED.authorization_token_id,
                    status = EXCLUDED.status
                """,
                (
                    conn.get("device_id"),
                    conn.get("user_id"),
                    conn.get("swarm_id"),
                    conn.get("device_name") or conn.get("device_id"),
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
                    INSERT INTO notifications (id, swarm_id, event_type, title, message, actor_user_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT (id) DO UPDATE SET
                        event_type = EXCLUDED.event_type,
                        title = EXCLUDED.title,
                        message = EXCLUDED.message,
                        actor_user_id = EXCLUDED.actor_user_id
                    """,
                    (
                        note.get("id"),
                        note.get("swarm_id") or swarm_id,
                        note.get("event_type"),
                        note.get("title") or "",
                        note.get("message") or "",
                        note.get("actor_user_id"),
                        self._dt(note.get("created_at")),
                    ),
                )
                cur.execute("DELETE FROM notification_fields WHERE notification_id = %s", (note.get("id"),))
                details = note.get("details") if isinstance(note.get("details"), dict) else {}
                for key, value in details.items():
                    cur.execute(
                        "INSERT INTO notification_fields (notification_id, field_name, field_value) VALUES (%s, %s, %s) ON CONFLICT DO UPDATE SET field_value = EXCLUDED.field_value",
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
                cur.execute(
                    """
                    INSERT INTO drone_speed_samples
                        (drone_id, upload_mbps, download_mbps, latency_ms, measured_at, received_at)
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
                    """,
                    (internal_id, row.get("upload_mbps"), row.get("download_mbps"), row.get("latency_ms"), self._dt(row.get("measured_at") or row.get("sampled_at")), self._dt(row.get("sampled_at"))),
                )
        for internal_id, rows in (state.get("device_events") if isinstance(state.get("device_events"), dict) else {}).items():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO drone_events (drone_id, event_type, severity, message, occurred_at, received_at)
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
                    RETURNING id
                    """,
                    (internal_id, row.get("event_type"), row.get("severity"), row.get("message") or row.get("rom") or row.get("path"), self._dt(row.get("timestamp") or row.get("occurred_at")), self._dt(row.get("received_at"))),
                )
                event_id = (cur.fetchone() or [None])[0]
                for key, value in (row.get("metadata") if isinstance(row.get("metadata"), dict) else {}).items():
                    cur.execute(
                        "INSERT INTO drone_event_fields (event_id, field_name, field_value) VALUES (%s, %s, %s) ON CONFLICT DO UPDATE SET field_value = EXCLUDED.field_value",
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
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
                "INSERT INTO drone_action_result_fields (result_id, field_name, field_value) VALUES (%s, %s, %s) ON CONFLICT DO UPDATE SET field_value = EXCLUDED.field_value",
                (result_id, str(key), self._json(value)),
            )

    def _insert_download_snapshot(self, cur, internal_id: str, state: dict) -> None:
        concurrency = state.get("concurrency") if isinstance(state.get("concurrency"), dict) else {}
        cur.execute(
            """
            INSERT INTO download_snapshots (target_drone_id, reported_at, concurrency_scope, active_limit)
            VALUES (%s, COALESCE(%s, now()), %s, %s)
            RETURNING id
            """,
            (internal_id, self._dt(state.get("received_at")), concurrency.get("scope"), concurrency.get("active_limit")),
        )
        snapshot_id = (cur.fetchone() or [None])[0]
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
                    """
                    SELECT asset_type, payload
                    FROM overmind_device_asset_staging
                    WHERE device_internal_id = %s AND inventory_id = %s
                    """,
                    (device_internal_id, inventory_id),
                )
                rows_by_type: dict[str, list[dict]] = {"rom": [], "bios": [], "artwork": []}
                for asset_type, payload in cur.fetchall():
                    decoded = _decode_state(payload)
                    if isinstance(decoded, dict) and asset_type in rows_by_type:
                        rows_by_type[asset_type].append(decoded)
                for asset_type, rows in rows_by_type.items():
                    self._replace_domain_assets(cur, device_internal_id, asset_type, rows)
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
