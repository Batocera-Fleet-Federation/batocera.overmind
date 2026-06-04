-- depends: 0005.cleanup_legacy_app_state
-- ROM inventory fingerprint columns on drones.
--
-- These columns were originally added to 0002.column_migrations.sql, but the
-- migration runner skips any migration whose id is already recorded in
-- _overmind_migrations. Deployments that had already applied 0002 therefore
-- never received these columns, which caused the device-load query to fail
-- with UndefinedColumn and wiped every Drone from the in-memory state.
--
-- Re-applying them here (idempotently) brings already-migrated databases up to
-- date. All statements use IF NOT EXISTS so this is safe to re-run and a no-op
-- on fresh installs that got the columns from 0001/0002.

ALTER TABLE drones ADD COLUMN IF NOT EXISTS rom_inventory_fingerprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_rom_inventory_fingerprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS rom_inventory_fingerprint_algorithm TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS rom_inventory_fingerprint_at TIMESTAMPTZ;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_rom_inventory_fingerprint_at TIMESTAMPTZ;
