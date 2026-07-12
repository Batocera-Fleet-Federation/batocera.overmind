-- depends: 0007.wifi_recovery_system_info

-- Downloads/Sync-Activity UI consolidation: the Drone already carries sync_id
-- through every download job snapshot (minted by Overmind when it queues a
-- sync_rom/sync_bios/sync_system action), but it was dropped on the way into
-- download_items. Without it, a "pending" placeholder row written at request
-- time (before the Drone has even claimed the action) can't be matched against
-- the real download job once it appears, so a duplicate row would show up.

ALTER TABLE download_items ADD COLUMN IF NOT EXISTS sync_id TEXT;
