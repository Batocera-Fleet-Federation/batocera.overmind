-- depends: 0013.landing_visits

-- Idle-volume automation settings the Drone reports in its heartbeat and that
-- admins can configure from the per-Drone admin screen in Overmind. The Drone
-- lowers its output volume after a period with no controller/keyboard input.

ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS idle_volume_enabled BOOLEAN;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS idle_volume_idle_minutes INTEGER;
ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS idle_volume_target INTEGER;
