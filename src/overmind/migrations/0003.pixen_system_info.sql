-- Track whether the optional PixN upgrade script is installed on a Drone.

ALTER TABLE drone_system_info
    ADD COLUMN IF NOT EXISTS pixen_installed BOOLEAN;
