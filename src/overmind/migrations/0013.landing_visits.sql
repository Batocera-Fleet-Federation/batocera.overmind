-- depends: 0012.admin_audit_log

-- Anonymous landing-page visitor counter. One row per unique client IP that loaded
-- the landing page while not logged in. Additive and idempotent.

CREATE TABLE IF NOT EXISTS landing_visits (
    ip TEXT PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    visit_count BIGINT NOT NULL DEFAULT 1,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_landing_visits_last_seen ON landing_visits (last_seen DESC);
