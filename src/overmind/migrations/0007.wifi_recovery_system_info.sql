-- depends: 0006.drone_action_results

-- Track whether the Drone's one-minute Wi-Fi recovery automation is enabled.

ALTER TABLE drone_system_info
    ADD COLUMN IF NOT EXISTS wifi_recovery_enabled BOOLEAN;
