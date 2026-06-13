-- depends: 0010.saves_inventory

-- Lightweight Batocera settings reported in every Drone heartbeat and displayed in
-- the per-Drone admin screen.

ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS screen_mode TEXT;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS audio_volume INTEGER;
