# Overmind Relational Storage Refactor

Overmind is moving away from document-style persistence in `overmind_app_state`
and JSON-heavy asset rows. The target model is a relational PostgreSQL schema
with explicit Drone request contracts at the API boundary.

## Reset

For a no-migration rebuild, set this only during an intentional maintenance
window:

```bash
OVERMIND_RESET_RELATIONAL_SCHEMA=true
```

On startup, Overmind drops the known legacy and relational tables, then recreates
the normalized schema. Leave this unset in normal runtime.

## Schema Domains

- Identity: `users`, `user_profiles`, `user_auth_identities`,
  `email_verifications`, `password_resets`
- Access: `swarms`, `swarm_memberships`, `swarm_invitations`
- Drone onboarding: `integration_tokens`, `approved_drone_tokens`,
  `pending_drone_connections`, `device_admin_claims`
- Drones: `drones`, `drone_network_state`, `drone_network_addresses`,
  `drone_system_info`, `drone_performance_metrics`, `drone_certificates`,
  `drone_certificate_sans`
- Assets: `systems`, `asset_inventory_batches`, `drone_roms`, `drone_bios`,
  `drone_artwork`
- Operations: `drone_actions`, `drone_action_parameters`,
  `drone_action_result_records`, `drone_action_result_fields`,
  `download_snapshots`, `download_items`, `sync_activity`
- Observability: `gameplay_sessions`, `drone_log_sources`, `drone_log_files`,
  `drone_emulator_configs`, `drone_emulator_config_versions`,
  `drone_speed_samples`, `drone_events`, `drone_event_fields`,
  `drone_peer_checks`
- Notifications: `notifications`, `notification_fields`,
  `notification_recipients`, `notification_delivery_attempts`

## Drone Contracts

Drone-facing endpoints now use Pydantic request models instead of raw `dict`
payloads for heartbeat, asset metadata, downloads, action completion, peer
checks, speed samples, game logs, log sources, and emulator configs. Top-level
contracts reject unknown fields. Row-level asset and telemetry records remain
extensible while the Drone client matures, but their known fields are named in
`models.py`.

## Remaining Cutover Work

The normalized schema is created today, but the repository still needs to move
domain methods out of the legacy in-memory/document persistence path. The next
steps should be:

1. Move identity/profile/swarm methods to SQL.
2. Move Drone registration, heartbeat, network, certificate, and token methods
   to SQL.
3. Move asset metadata writes from `overmind_device_assets.payload` to
   `drone_roms`, `drone_bios`, and `drone_artwork`.
4. Rebuild master-list queries from relational asset tables.
5. Move logs, emulator configs, downloads, actions, sync activity, and
   notifications to their domain tables.
6. Delete `overmind_app_state` and legacy JSON asset storage after all methods
   use relational repositories.
