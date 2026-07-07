-- depends: 0001.initial_schema
--
-- Associates BIOS files with the system(s) that use them, so the tree UI can show a
-- "BIOS" branch under each system instead of one flat top-level bucket. The Drone
-- resolves this at scan time (against a vendored MD5 -> system_name reference table)
-- and uploads a `systems` list per BIOS entry; most BIOS files won't match anything
-- (0 rows here) and a handful are legitimately shared by more than one system (2+ rows
-- here) -- both cases fall back to the "unassigned/shared" bucket rather than being
-- filed under one system.

CREATE TABLE IF NOT EXISTS drone_bios_systems (
    bios_id BIGINT NOT NULL REFERENCES drone_bios(id) ON DELETE CASCADE,
    system_name TEXT NOT NULL,
    PRIMARY KEY (bios_id, system_name)
);

CREATE INDEX IF NOT EXISTS idx_drone_bios_systems_system_name ON drone_bios_systems (system_name);
