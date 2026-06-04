-- depends: 0003.v2_master_sort_keys
-- no-transaction
-- All application indexes.
-- no-transaction: index creation must run outside a transaction to avoid
-- holding exclusive table locks during Lambda cold starts, which causes
-- connection exhaustion when many instances cold-start simultaneously.
-- IF NOT EXISTS guards make these safe to re-apply if this migration
-- is interrupted and retried.

CREATE INDEX IF NOT EXISTS idx_overmind_device_assets_device_type
    ON overmind_device_assets (device_internal_id, asset_type);

CREATE INDEX IF NOT EXISTS idx_overmind_device_asset_staging_inventory
    ON overmind_device_asset_staging (device_internal_id, inventory_id, asset_type);

CREATE INDEX IF NOT EXISTS idx_overmind_device_assets_type_system
    ON overmind_device_assets (asset_type, system_name);

CREATE INDEX IF NOT EXISTS idx_overmind_device_assets_device_id
    ON overmind_device_assets (device_id, asset_type);

CREATE INDEX IF NOT EXISTS idx_oda_master_key_cover
    ON overmind_device_assets (device_internal_id, asset_type, master_key, sort_key)
    WHERE master_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_drones_user_swarm
    ON drones (user_id, swarm_id);

CREATE INDEX IF NOT EXISTS idx_drones_device_id
    ON drones (device_id);

CREATE INDEX IF NOT EXISTS idx_roms_swarm_search
    ON drone_roms (system_name, rom_md5, normalized_path);

CREATE INDEX IF NOT EXISTS idx_roms_drone_system
    ON drone_roms (drone_id, system_name);

CREATE INDEX IF NOT EXISTS idx_bios_hash
    ON drone_bios (bios_md5);

CREATE INDEX IF NOT EXISTS idx_artwork_lookup
    ON drone_artwork (system_name, normalized_rom_path, artwork_type);

CREATE INDEX IF NOT EXISTS idx_actions_drone_status
    ON drone_actions (drone_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_sync_activity_target
    ON sync_activity (target_drone_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_gameplay_drone_system
    ON gameplay_sessions (drone_id, system_name, played_at DESC);

CREATE INDEX IF NOT EXISTS idx_gameplay_drone_received
    ON gameplay_sessions (drone_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_speed_samples_drone_received
    ON drone_speed_samples (drone_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_drone_received
    ON drone_events (drone_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_peer_checks_source_received
    ON drone_peer_checks (source_drone_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_swarm_created
    ON notifications (swarm_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_pending_delivery
    ON notifications (delivery_pending, created_at)
    WHERE delivery_pending IS TRUE AND delivery_completed_at IS NULL;

-- rollback
DROP INDEX IF EXISTS idx_notifications_pending_delivery;
DROP INDEX IF EXISTS idx_notifications_swarm_created;
DROP INDEX IF EXISTS idx_peer_checks_source_received;
DROP INDEX IF EXISTS idx_events_drone_received;
DROP INDEX IF EXISTS idx_speed_samples_drone_received;
DROP INDEX IF EXISTS idx_gameplay_drone_received;
DROP INDEX IF EXISTS idx_gameplay_drone_system;
DROP INDEX IF EXISTS idx_sync_activity_target;
DROP INDEX IF EXISTS idx_actions_drone_status;
DROP INDEX IF EXISTS idx_artwork_lookup;
DROP INDEX IF EXISTS idx_bios_hash;
DROP INDEX IF EXISTS idx_roms_drone_system;
DROP INDEX IF EXISTS idx_roms_swarm_search;
DROP INDEX IF EXISTS idx_drones_device_id;
DROP INDEX IF EXISTS idx_drones_user_swarm;
DROP INDEX IF EXISTS idx_oda_master_key_cover;
DROP INDEX IF EXISTS idx_overmind_device_assets_device_id;
DROP INDEX IF EXISTS idx_overmind_device_assets_type_system;
DROP INDEX IF EXISTS idx_overmind_device_asset_staging_inventory;
DROP INDEX IF EXISTS idx_overmind_device_assets_device_type;
