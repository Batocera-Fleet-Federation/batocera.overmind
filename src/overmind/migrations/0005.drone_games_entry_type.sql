-- depends: 0004.idle_game_exit_system_info

-- entry_type: 'file' | 'folder'. Folder-unit games (marker/disc systems, e.g. Sega
-- Lindbergh's .game marker or a Dreamcast .gdi + its .bin tracks) report the game
-- folder's TOTAL bytes in file_size so the UI shows the real download size; drones
-- without the feature never send the field and keep the 'file' default.
ALTER TABLE drone_games ADD COLUMN IF NOT EXISTS entry_type TEXT NOT NULL DEFAULT 'file';
