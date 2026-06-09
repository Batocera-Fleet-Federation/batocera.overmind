-- depends: 0007.reassert_additive_columns
-- Rename the ROM file-identity columns from rom_md5 to rom_fingerprint.
--
-- The Drone now computes a sampled content fingerprint (sample-fp-v1) instead of a
-- full-file md5 as the cross-drone ROM identity key, and the codebase/UI were renamed
-- to match. BIOS files are intentionally NOT renamed: they keep a true full-file
-- bios_md5 so emulators can match them exactly against known-good BIOS dumps.
--
-- Postgres has no "RENAME COLUMN IF EXISTS", so each rename is guarded by an
-- information_schema check, making this migration idempotent and safe on databases
-- at any prior state (fresh DBs where 0001 just created rom_md5, or already-migrated
-- DBs where the rename has run). Indexes follow the renamed column automatically, so
-- 0004's indexes keep working under the new column name.
--
-- NOTE: emulator-config md5 columns (drone_emulator_configs.md5,
-- drone_emulator_config_versions.md5) and all bios_md5 columns are left unchanged.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'drone_roms' AND column_name = 'rom_md5')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'drone_roms' AND column_name = 'rom_fingerprint') THEN
        ALTER TABLE drone_roms RENAME COLUMN rom_md5 TO rom_fingerprint;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'sync_activity' AND column_name = 'rom_md5')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'sync_activity' AND column_name = 'rom_fingerprint') THEN
        ALTER TABLE sync_activity RENAME COLUMN rom_md5 TO rom_fingerprint;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'gameplay_sessions' AND column_name = 'rom_md5')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'gameplay_sessions' AND column_name = 'rom_fingerprint') THEN
        ALTER TABLE gameplay_sessions RENAME COLUMN rom_md5 TO rom_fingerprint;
    END IF;
END $$;
