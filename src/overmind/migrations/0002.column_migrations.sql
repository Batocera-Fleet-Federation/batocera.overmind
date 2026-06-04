-- depends: 0001.initial_schema
-- Additive column migrations on existing tables.
-- These columns were added after the initial schema was deployed.
-- All statements use IF NOT EXISTS so they are safe to re-apply.

ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved';
ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_connected BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS authorization_token_id TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_token_hash TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE drones ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;

ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS api_port INTEGER;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS scheme TEXT;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS reachable_url TEXT;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS public_resolvable BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS public_ip TEXT;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS hostname TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS system_name TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS architecture TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_model TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_cores INTEGER;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_threads INTEGER;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS cpu_max_frequency TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS memory_available TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS memory_total TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS batocera_version TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS container BOOLEAN;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS md5 TEXT;
ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS fingerprint TEXT;
ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS truncated BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS error TEXT;

ALTER TABLE drone_emulator_config_versions ADD COLUMN IF NOT EXISTS md5 TEXT;
ALTER TABLE drone_emulator_config_versions ADD COLUMN IF NOT EXISTS fingerprint TEXT;

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_pending BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_completed_at TIMESTAMPTZ;

ALTER TABLE pending_drone_connections ADD COLUMN IF NOT EXISTS batocera_info JSONB;
ALTER TABLE pending_drone_connections ADD COLUMN IF NOT EXISTS drone_token_hash TEXT;
ALTER TABLE pending_drone_connections ADD COLUMN IF NOT EXISTS recovery_reason TEXT;
ALTER TABLE pending_drone_connections ALTER COLUMN user_id DROP NOT NULL;

-- rollback
-- Note: safely removing added columns can cause data loss; rollback is intentionally omitted.
-- To revert, drop the columns manually after confirming no data loss is acceptable.
