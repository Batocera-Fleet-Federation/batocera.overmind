-- depends: 0015.edge_presence

-- One row per coordinated peer transfer. The control plane mints a session here
-- (status 'offered'); the Drones reference its id as the relay session id and
-- report progress/outcome. Used for authorization auditing, the transfer UI, and
-- resume. Written via lean targeted statements (never the full-state mirror).
-- ``asset`` is a JSON string (the AssetRef); kept as TEXT to avoid jsonb casts.

CREATE TABLE IF NOT EXISTS transfer_sessions (
    id              TEXT PRIMARY KEY,
    swarm_id        TEXT,
    from_device     TEXT,
    to_device       TEXT,
    asset           TEXT,
    transport_used  TEXT,
    status          TEXT NOT NULL DEFAULT 'offered',
    bytes_total     BIGINT,
    bytes_done      BIGINT NOT NULL DEFAULT 0,
    token_hash      TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS transfer_sessions_to_device_idx ON transfer_sessions (to_device);
CREATE INDEX IF NOT EXISTS transfer_sessions_status_idx ON transfer_sessions (status);
