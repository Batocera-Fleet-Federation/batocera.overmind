-- depends: 0008.rename_md5_to_fingerprint
-- Wholistic per-asset-class "thumbprints" round-tripped with each Drone.
--
-- The Drone computes one thumbprint over its whole ROM set and another over its whole
-- BIOS set, sends them up, and Overmind stores them VERBATIM (it does not recompute).
-- Overmind echoes the stored thumbprints back in the heartbeat response so the Drone can
-- compare them against what it holds on disk and push a resync only when they differ.
--
-- Storing the Drone-supplied value verbatim (rather than recomputing a fingerprint from
-- Overmind's own stored rows, as the legacy rom_inventory_fingerprint path did) is what
-- breaks the prior purge->full-refresh loop: the two sides can no longer disagree about
-- the same asset set. All statements use IF NOT EXISTS so this is safe to re-run.

ALTER TABLE drones ADD COLUMN IF NOT EXISTS romset_files_thumbprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS bios_files_thumbprint TEXT;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS romset_files_thumbprint_at TIMESTAMPTZ;
ALTER TABLE drones ADD COLUMN IF NOT EXISTS bios_files_thumbprint_at TIMESTAMPTZ;
