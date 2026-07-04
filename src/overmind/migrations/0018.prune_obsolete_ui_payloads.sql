-- depends: 0017.game_count_asset_index
-- Keep only the UI data Overmind still needs from Drone metadata uploads.

DELETE FROM overmind_device_asset_staging
WHERE asset_type IN ('artwork', 'saves');

DELETE FROM overmind_device_assets
WHERE asset_type IN ('artwork', 'saves');

DELETE FROM drone_artwork;

DELETE FROM drone_saves;

DELETE FROM drone_emulator_config_versions
WHERE id NOT IN (
    SELECT id
    FROM (
        SELECT DISTINCT ON (config_id) id
        FROM drone_emulator_config_versions
        ORDER BY config_id, received_at DESC, id DESC
    ) latest
);

UPDATE drones
SET saves_files_thumbprint = NULL,
    saves_files_thumbprint_at = NULL
WHERE saves_files_thumbprint IS NOT NULL
   OR saves_files_thumbprint_at IS NOT NULL;
