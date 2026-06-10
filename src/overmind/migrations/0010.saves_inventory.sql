-- depends: 0009.asset_files_thumbprints

-- Game-save sync support. Mirrors the ROM/BIOS inventory model: the Drone scans
-- /userdata/saves, fingerprints every save file, uploads the inventory, and echoes a
-- whole-set "thumbprint" in its heartbeat. Overmind stores the rows per Drone and keeps
-- the Drone-supplied thumbprint verbatim (never recomputed) so the two sides cannot
-- disagree about the same save set. Save conflicts resolve newest-modified-time-wins,
-- so modified_time is stored alongside the fingerprint.
--
-- All statements use IF NOT EXISTS so this migration is safe to re-run.

CREATE TABLE IF NOT EXISTS drone_saves (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    system_id BIGINT REFERENCES systems(id) ON DELETE SET NULL,
    system_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    save_name TEXT,
    fingerprint TEXT,
    file_size BIGINT,
    modified_time BIGINT,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drone_id, system_name, normalized_path)
);

CREATE INDEX IF NOT EXISTS idx_drone_saves_drone ON drone_saves(drone_id);
CREATE INDEX IF NOT EXISTS idx_drone_saves_fingerprint ON drone_saves(fingerprint);

ALTER TABLE drones ADD COLUMN IF NOT EXISTS saves_files_thumbprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS saves_files_thumbprint_at TIMESTAMPTZ;
