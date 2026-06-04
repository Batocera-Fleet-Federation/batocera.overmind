-- Initial Overmind schema: all tables created from scratch.
-- Legacy asset-store tables (drone_action_results, overmind_app_state, overmind_device_assets,
-- overmind_device_asset_staging) are included here alongside the full relational schema.

CREATE TABLE IF NOT EXISTS overmind_schema_versions (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Legacy asset store tables -------------------------------------------------------

CREATE TABLE IF NOT EXISTS drone_action_results (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL,
    action_id TEXT,
    result_type TEXT,
    result JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS overmind_app_state (
    id TEXT PRIMARY KEY,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS overmind_device_assets (
    device_internal_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    system_name TEXT,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_internal_id, asset_type, item_key)
);

CREATE TABLE IF NOT EXISTS overmind_device_asset_staging (
    device_internal_id TEXT NOT NULL,
    inventory_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    system_name TEXT,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_internal_id, inventory_id, asset_type, item_key)
);

-- Users ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    email_verified BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN NOT NULL DEFAULT false,
    auth_provider TEXT NOT NULL DEFAULT 'password',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    username TEXT UNIQUE,
    full_name TEXT,
    avatar_data_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_auth_identities (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_subject TEXT,
    provider_email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    UNIQUE (provider, provider_subject),
    UNIQUE (user_id, provider)
);

CREATE TABLE IF NOT EXISTS user_fleet_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    auto_sync_roms BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_notification_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    notify_slack BOOLEAN NOT NULL DEFAULT false,
    notify_discord BOOLEAN NOT NULL DEFAULT false,
    notify_email BOOLEAN NOT NULL DEFAULT true,
    slack_webhook TEXT,
    discord_webhook TEXT,
    email_address TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_notification_type_settings (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (user_id, event_type)
);

CREATE TABLE IF NOT EXISTS email_verifications (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS password_resets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at TIMESTAMPTZ
);

-- Swarms --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS swarms (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_public BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS swarm_memberships (
    swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('overlord', 'overseer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (swarm_id, user_id)
);

CREATE TABLE IF NOT EXISTS swarm_invitations (
    id TEXT PRIMARY KEY,
    swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('overlord', 'overseer')),
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invited_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resent_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ
);

-- Drones --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS drones (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL UNIQUE,
    device_name TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL,
    approval_status TEXT NOT NULL DEFAULT 'approved',
    swarm_connected BOOLEAN NOT NULL DEFAULT true,
    authorization_token_id TEXT,
    drone_token_hash TEXT,
    rom_inventory_fingerprint TEXT,
    drone_rom_inventory_fingerprint TEXT,
    rom_inventory_fingerprint_algorithm TEXT,
    rom_inventory_fingerprint_at TIMESTAMPTZ,
    drone_rom_inventory_fingerprint_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ,
    removed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS drone_network_state (
    drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
    api_port INTEGER,
    scheme TEXT,
    reachable_url TEXT,
    public_resolvable BOOLEAN NOT NULL DEFAULT false,
    public_ip TEXT,
    checked_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_network_addresses (
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    address_type TEXT NOT NULL CHECK (address_type IN ('ipv4', 'ipv6', 'hostname', 'mac')),
    address TEXT NOT NULL,
    interface_name TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (drone_id, address_type, address)
);

CREATE TABLE IF NOT EXISTS drone_system_info (
    drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
    hostname TEXT,
    model TEXT,
    system_name TEXT,
    architecture TEXT,
    cpu_model TEXT,
    cpu_cores INTEGER,
    cpu_threads INTEGER,
    cpu_max_frequency TEXT,
    memory_available TEXT,
    memory_total TEXT,
    batocera_version TEXT,
    container BOOLEAN,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_performance_metrics (
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    metric_group TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION,
    metric_text TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (drone_id, metric_group, metric_name)
);

CREATE TABLE IF NOT EXISTS drone_certificates (
    drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
    status TEXT,
    fingerprint TEXT,
    sha256_fingerprint TEXT,
    public_certificate TEXT,
    subject TEXT,
    issuer TEXT,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    serial_number TEXT,
    overmind_signed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_certificate_sans (
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    san TEXT NOT NULL,
    PRIMARY KEY (drone_id, san)
);

CREATE TABLE IF NOT EXISTS drone_auto_sync_policies (
    drone_id TEXT PRIMARY KEY REFERENCES drones(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_auto_sync_policy_systems (
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    system_name TEXT NOT NULL,
    PRIMARY KEY (drone_id, system_name)
);

-- Tokens & Auth -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS device_admin_claims (
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (drone_id, user_id)
);

CREATE TABLE IF NOT EXISTS integration_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    bound_device_id TEXT,
    bound_fingerprint TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS approved_drone_tokens (
    device_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    integration_token_id TEXT REFERENCES integration_tokens(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pending_drone_connections (
    device_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    swarm_id TEXT REFERENCES swarms(id) ON DELETE SET NULL,
    device_name TEXT NOT NULL,
    batocera_info JSONB,
    authorization_token_id TEXT REFERENCES integration_tokens(id) ON DELETE SET NULL,
    drone_token_hash TEXT,
    recovery_reason TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'pending'
);

-- Game Assets ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS systems (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS asset_inventory_batches (
    id TEXT PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    update_mode TEXT NOT NULL,
    replace_all BOOLEAN NOT NULL DEFAULT false,
    chunk_index INTEGER,
    chunk_total INTEGER,
    inventory_complete BOOLEAN,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS drone_roms (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    system_id BIGINT REFERENCES systems(id) ON DELETE SET NULL,
    system_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    rom_name TEXT,
    rom_md5 TEXT,
    file_size BIGINT,
    entry_type TEXT NOT NULL DEFAULT 'file',
    metadata_source TEXT,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drone_id, system_name, normalized_path)
);

CREATE TABLE IF NOT EXISTS drone_bios (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    bios_name TEXT,
    bios_md5 TEXT,
    file_size BIGINT,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drone_id, normalized_path)
);

CREATE TABLE IF NOT EXISTS drone_artwork (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    system_id BIGINT REFERENCES systems(id) ON DELETE SET NULL,
    system_name TEXT NOT NULL,
    rom_path TEXT NOT NULL,
    normalized_rom_path TEXT NOT NULL,
    rom_name TEXT,
    title TEXT,
    artwork_type TEXT NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drone_id, system_name, normalized_rom_path, artwork_type)
);

-- Actions & Results ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS drone_actions (
    id TEXT PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    message TEXT
);

CREATE TABLE IF NOT EXISTS drone_action_parameters (
    action_id TEXT NOT NULL REFERENCES drone_actions(id) ON DELETE CASCADE,
    parameter_name TEXT NOT NULL,
    parameter_value TEXT,
    PRIMARY KEY (action_id, parameter_name)
);

CREATE TABLE IF NOT EXISTS drone_action_result_records (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT REFERENCES drones(id) ON DELETE CASCADE,
    device_id TEXT,
    action_id TEXT,
    result_type TEXT,
    status TEXT,
    message TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_action_result_fields (
    result_id BIGINT NOT NULL REFERENCES drone_action_result_records(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    field_value TEXT,
    PRIMARY KEY (result_id, field_name)
);

-- Downloads & Sync ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS download_snapshots (
    id BIGSERIAL PRIMARY KEY,
    target_drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    concurrency_scope TEXT,
    active_limit INTEGER
);

CREATE TABLE IF NOT EXISTS download_items (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES download_snapshots(id) ON DELETE CASCADE,
    job_id TEXT,
    state_bucket TEXT NOT NULL CHECK (state_bucket IN ('active', 'queued', 'recent')),
    asset_type TEXT,
    status TEXT,
    source_drone_id TEXT,
    system_name TEXT,
    file_path TEXT,
    rom_path TEXT,
    bios_name TEXT,
    artwork_type TEXT,
    file_size BIGINT,
    downloaded_bytes BIGINT,
    percentage DOUBLE PRECISION,
    transfer_speed_bps DOUBLE PRECISION,
    queue_position INTEGER,
    failure_reason TEXT
);

CREATE TABLE IF NOT EXISTS sync_activity (
    id TEXT PRIMARY KEY,
    target_drone_id TEXT REFERENCES drones(id) ON DELETE CASCADE,
    source_drone_id TEXT,
    asset_type TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'download',
    status TEXT NOT NULL DEFAULT 'pending',
    system_name TEXT,
    file_path TEXT,
    rom_md5 TEXT,
    bios_md5 TEXT,
    artwork_type TEXT,
    bytes_transferred BIGINT,
    file_size BIGINT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failure_reason TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Gaming & Monitoring -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gameplay_sessions (
    id TEXT PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    system_name TEXT,
    game_name TEXT NOT NULL,
    rom_path TEXT,
    rom_md5 TEXT,
    played_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_log_sources (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drone_id, source)
);

CREATE TABLE IF NOT EXISTS drone_log_files (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES drone_log_sources(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    modified_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, path)
);

CREATE TABLE IF NOT EXISTS drone_emulator_configs (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    root TEXT,
    relative_path TEXT NOT NULL,
    current_content TEXT NOT NULL,
    md5 TEXT,
    fingerprint TEXT,
    size_bytes BIGINT,
    truncated BOOLEAN NOT NULL DEFAULT false,
    error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (drone_id, relative_path)
);

CREATE TABLE IF NOT EXISTS drone_emulator_config_versions (
    id BIGSERIAL PRIMARY KEY,
    config_id BIGINT NOT NULL REFERENCES drone_emulator_configs(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    md5 TEXT,
    fingerprint TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_speed_samples (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    upload_mbps DOUBLE PRECISION,
    download_mbps DOUBLE PRECISION,
    latency_ms DOUBLE PRECISION,
    measured_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_events (
    id BIGSERIAL PRIMARY KEY,
    drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    severity TEXT,
    message TEXT,
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drone_event_fields (
    event_id BIGINT NOT NULL REFERENCES drone_events(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    field_value TEXT,
    PRIMARY KEY (event_id, field_name)
);

CREATE TABLE IF NOT EXISTS drone_peer_checks (
    id BIGSERIAL PRIMARY KEY,
    source_drone_id TEXT NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    target_drone_id TEXT NOT NULL,
    target_address TEXT,
    status TEXT NOT NULL,
    latency_ms DOUBLE PRECISION,
    checked_at TIMESTAMPTZ,
    error TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Notifications -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    swarm_id TEXT NOT NULL REFERENCES swarms(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivery_pending BOOLEAN NOT NULL DEFAULT false,
    delivery_completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS notification_fields (
    notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    field_value TEXT,
    PRIMARY KEY (notification_id, field_name)
);

CREATE TABLE IF NOT EXISTS notification_recipients (
    notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ,
    delivery_pending BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (notification_id, user_id)
);

CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
    id BIGSERIAL PRIMARY KEY,
    notification_id TEXT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error TEXT
);

-- rollback intentionally omitted: dropping all production tables requires
-- explicit DBA action, not an automated migration rollback.
