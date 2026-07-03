-- depends: 0016.transfer_sessions
-- no-transaction
-- Supports the device summary "Games" count, which groups ROM asset rows by
-- system and checks whether each system has gamelist.xml metadata rows.
-- Also supports the selected-Drone Systems UI page, which pages systems by
-- device before loading per-system ROM rows on demand.
-- This must be concurrent on production-sized asset tables.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_oda_rom_device_system_source
    ON overmind_device_assets (
        device_id,
        system_name,
        lower(coalesce(payload->>'metadata_source', payload->>'source', ''))
    )
    WHERE asset_type = 'rom';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_oda_rom_internal_system
    ON overmind_device_assets (device_internal_id, system_name)
    WHERE asset_type = 'rom' AND system_name IS NOT NULL;

-- rollback
DROP INDEX CONCURRENTLY IF EXISTS idx_oda_rom_internal_system;
DROP INDEX CONCURRENTLY IF EXISTS idx_oda_rom_device_system_source;
