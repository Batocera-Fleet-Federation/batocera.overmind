-- depends: 0005.drone_games_entry_type

-- Raw per-action result audit log, one row per POST .../actions/{id}/complete
-- report that carries a result payload. postgres_store.store_action_result has
-- written to this table since it was introduced, but the schema-squash baseline
-- (0001) never created it: every action completion whose caller passed a result
-- hit psycopg.errors.UndefinedTable, turning a clean 200 into a 500. The action's
-- own status/message update (drone_actions, in the same complete_device_action
-- call) commits in an earlier transaction and is NOT affected -- only this
-- secondary audit insert was broken -- but the caller (the Drone) sees a 500 and
-- logs "Failed to report Overmind action completion", so a real drone-side
-- failure and this harmless-but-throwing insert become indistinguishable from
-- the Drone's logs alone.
CREATE TABLE IF NOT EXISTS drone_action_results (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    result_type TEXT,
    result JSONB,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_drone_action_results_action_id ON drone_action_results (action_id);
CREATE INDEX IF NOT EXISTS idx_drone_action_results_device_id ON drone_action_results (device_id);
