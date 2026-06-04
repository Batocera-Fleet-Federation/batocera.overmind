-- depends: 0004.indexes
-- One-time cleanup: remove legacy 'roms', 'bios', and 'artwork' keys from
-- overmind_app_state that were stored before the relational schema was introduced.

UPDATE overmind_app_state
SET state = state - 'roms' - 'bios' - 'artwork'
WHERE id = 'default'
  AND (state ? 'roms' OR state ? 'bios' OR state ? 'artwork');

-- rollback
-- Data is non-recoverable after removal; rollback is intentionally omitted.
