-- depends: 0011.drone_screen_mode_volume

-- Persistent audit log of significant Overmind events (user/drone registrations,
-- approvals, deletions, sync triggers, remote actions) surfaced on the Super Admin
-- page. Additive and idempotent.

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    actor_user_id TEXT,
    actor_email TEXT,
    target_type TEXT,
    target_id TEXT,
    target_label TEXT,
    details TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at ON admin_audit_log (created_at DESC, id DESC);
