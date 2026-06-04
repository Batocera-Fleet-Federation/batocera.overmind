-- depends: 0002.column_migrations
-- no-transaction
-- v2: add master_key and sort_key computed columns to overmind_device_assets
-- and back-fill them from existing payload data.
-- no-transaction: the back-fill UPDATE on a large table must run outside a
-- transaction so it doesn't hold table locks, and so partial progress is
-- preserved if the Lambda process is killed mid-way.

ALTER TABLE overmind_device_assets ADD COLUMN IF NOT EXISTS master_key TEXT;
ALTER TABLE overmind_device_assets ADD COLUMN IF NOT EXISTS sort_key TEXT;

UPDATE overmind_device_assets SET
    master_key = CASE
        WHEN asset_type = 'rom' THEN
            CASE WHEN nullif(lower(coalesce(payload->>'rom_md5', '')), '') IS NOT NULL
                 THEN 'md5:' || lower(payload->>'rom_md5')
            ELSE 'path:' || lower(coalesce(system_name, '')) || ':' ||
                            lower(coalesce(payload->>'file_path', payload->>'rom_name', ''))
            END
        WHEN asset_type = 'bios' THEN
            CASE WHEN nullif(lower(coalesce(payload->>'bios_md5', payload->>'md5', '')), '') IS NOT NULL
                 THEN 'md5:' || lower(coalesce(payload->>'bios_md5', payload->>'md5'))
            ELSE 'path:' || lower(coalesce(payload->>'file_path', payload->>'relative_path', payload->>'bios_name', ''))
            END
        ELSE NULL
    END,
    sort_key = CASE
        WHEN asset_type = 'rom'  THEN lower(coalesce(system_name, '')) || ':' ||
                                       lower(coalesce(payload->>'file_path', payload->>'rom_name', ''))
        WHEN asset_type = 'bios' THEN lower(coalesce(payload->>'file_path',
                                              payload->>'relative_path', payload->>'bios_name', ''))
        ELSE NULL
    END
WHERE master_key IS NULL AND asset_type IN ('rom', 'bios');

-- rollback
ALTER TABLE overmind_device_assets DROP COLUMN IF EXISTS master_key;
ALTER TABLE overmind_device_assets DROP COLUMN IF EXISTS sort_key;
