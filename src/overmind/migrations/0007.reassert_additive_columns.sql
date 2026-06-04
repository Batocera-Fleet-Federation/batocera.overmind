-- depends: 0006.rom_inventory_fingerprint_columns
-- Re-assert every additive column from 0002.column_migrations.sql.
--
-- The migration runner records each migration by id and skips an already-applied
-- file. Columns appended to 0002 *after* it had been applied in an environment
-- therefore never ran there. This has bitten several columns
-- (drones.rom_inventory_fingerprint*, pending_drone_connections.drone_token_hash, ...).
--
-- Re-running all of 0002's ADD COLUMN IF NOT EXISTS statements here is idempotent
-- (a no-op where the column already exists) and brings any drifted database fully
-- up to date in one place.

ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved';
ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_connected BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS authorization_token_id TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_token_hash TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS rom_inventory_fingerprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_rom_inventory_fingerprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS rom_inventory_fingerprint_algorithm TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS rom_inventory_fingerprint_at TIMESTAMPTZ;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_rom_inventory_fingerprint_at TIMESTAMPTZ;
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
