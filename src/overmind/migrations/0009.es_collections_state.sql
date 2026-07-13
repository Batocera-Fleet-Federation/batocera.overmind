-- depends: 0008.download_items_sync_id

-- Persist the Drone-reported ES-collections snapshot (music volume, screensaver,
-- systems displayed/grouped, auto/custom collections) so the device-detail page can
-- show it without a live action round-trip.

ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS es_collections_state JSONB;
