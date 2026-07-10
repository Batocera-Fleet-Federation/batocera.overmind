-- depends: 0003.pixen_system_info

-- Track idle-game-exit automation (exit the running game after a period of no
-- input), mirroring the existing idle-volume automation columns.

ALTER TABLE drone_system_info
    ADD COLUMN IF NOT EXISTS idle_game_exit_enabled BOOLEAN;

ALTER TABLE drone_system_info
    ADD COLUMN IF NOT EXISTS idle_game_exit_idle_minutes INTEGER;
