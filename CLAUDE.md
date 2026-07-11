# CLAUDE.md — Batocera Overmind

Guidance for Claude Code when working in **this repo** (the central hub). This repo
is one of three in the Batocera Fleet Federation; the Drone agent
(`batocera.drone/`) and shared infra (`.github/`) are sibling repos. A networking
change often spans this repo **and** the Drone repo — cross-reference both.

## What this is

FastAPI app + Postgres, served locally/EC2 via Docker (uvicorn) or on AWS Lambda.
Source in `src/overmind/`. It is the **control plane** (auth, RBAC, registration,
CA/cert signing, device records, UI) and now also ships the **Edge** service
(`src/overmind/edge/`) — the always-on process that terminates each Drone's
persistent outbound connection and relays transfers.

## Commands

```bash
make run                              # uvicorn dev server on :8000
make test                             # pytest tests/ -v
python3 -m pytest tests/test_api.py -k <expr>   # single test / subset
python3 -m pytest tests/test_edge.py            # Edge mux/relay/presence unit tests
EDGE_AUTH=allow-all EDGE_ALLOW_INSECURE=1 python -m overmind.edge.edge_app   # run the Edge locally
make lint                             # ruff check src/ + mypy src/
make format                           # black src/ (line-length 100)
```

Tests use an in-memory store by default (no Postgres needed); they prepend `src/`
to `sys.path`. `test_api.py` is the large end-to-end FastAPI suite. Postgres-only
methods are unit-tested with a fake cursor/connection (see
`tests/test_notification_store_paths.py`, `tests/test_edge.py`).

## Architecture (the parts that need multiple files)

**Dual data layer.** `db.py` is a repository facade holding in-memory dicts
(`users`, `devices`, `roms`, …). `postgres_store.py` (`postgres_store` singleton)
is the relational source of truth. Two access patterns coexist:
- **Full-state path:** `db._persist_state()` → `postgres_store.store_app_state()`
  mirrors the whole snapshot (`_mirror_*`, upsert/`ON CONFLICT`, never deletes).
  Dangerous if driven by a *partial* snapshot — it can clobber columns owned by
  another writer (caused real bugs, e.g. reachability resets).
- **Lean path:** scheduled jobs + hot endpoints write/read targeted rows directly
  (`insert_swarm_notification`, `update_device_reachability`,
  `update_device_edge_presence`, `create/update/expire/list_recent_transfer_sessions`,
  …). **Lambda runs almost entirely on the lean path** (in-memory store empty per
  invocation), so anything a job/digest needs must be loaded by the relevant
  `load_*`/`hydrate_*` method, not assumed present in memory.

**Runtimes.** Same FastAPI `app` runs via uvicorn (container/EC2) and Lambda
(`lambda_handler.py` via Mangum); `OVERMIND_RUNTIME=lambda` switches behavior. API
Gateway → `handler`; EventBridge → `scheduled_handler({"job": ...})` →
`run_scheduled_job` → `poll_*_once`. Scheduled jobs (`notification-delivery`,
`device-status`, `public-reachability`) + cadences live in
`.github/terraform/aws/us-east-1/locals.tf`. **`public-reachability` defaults
conditional on the Edge** (`_resolve_public_reachability_enabled`): OFF when
`OVERMIND_EDGE_ENABLED` (set by Terraform when `enable_edge=true`, outbound-only),
ON without an Edge so cross-network Drones keep a direct WAN path.
`OVERMIND_PUBLIC_REACHABILITY_ENABLED` overrides either way.

**Edge service (`src/overmind/edge/`).** Lambda/API-Gateway-HTTP can't hold
persistent sockets or stream relayed bytes, so the Edge is a **separate always-on
process** (`python -m overmind.edge.edge_app`, `Dockerfile.edge`, AWS via `edge.tf`
gated on `var.enable_edge`). It reuses the same store/CA/secrets. Pieces:
- `server.py` — asyncio TLS `MuxServer`: authenticates `HELLO`, tracks presence,
  brokers signaling, pairs relay legs, pings, pushes `TRANSFER_OFFER`. Optional
  `transfer_status` hook records the session lifecycle (best-effort,
  fire-and-forget — a slow/flaky DB must never stall or break a relay).
- `auth.py` — `DbAuthenticator` (reuses `verify_drone_token`) / `AllowAllAuthenticator`.
- `registry.py` — `PresenceRegistry`; persisted via `update_device_edge_presence`
  (surfaced as `edge_online`, "Online via Edge" badge).
- `relay.py` — `RelayHub` pairs the two legs of a session and forwards `DATA`
  frames; **per-session** token-bucket bandwidth limit (`EDGE_RELAY_BW_LIMIT_BPS`).
- `stun.py` — UDP STUN reflector for hole-punch candidate gathering.
- `protocol.py` — frame codec, **byte-identical** to the Drone's
  `app/transport/mux.py` (golden-vector tested).
- `edge_app.py` — `EdgeConfig.from_env`, TLS context (`EDGE_TLS_SELF_SIGNED` /
  `EDGE_ALLOW_INSECURE` when TLS is terminated upstream, e.g. behind the NLB),
  builders, off-loop DB writers, `main()`.

**Transfer authorization & monitoring.** The control plane mints a short-lived
HMAC-SHA256 token at `POST /api/devices/{id}/transfers` (`transfer_tokens.py`,
`{sid,from,to,asset,exp}`, validated offline by the Edge with the shared
`SECRET_KEY`). `transfer_sessions` rows track each handoff; the Edge marks
`active`→`completed`/`aborted`, the `device-status` job expires stragglers past
token expiry, and `GET /api/admin/transfers` (super-admin) + the Super-Admin
"Peer Transfers" UI panel surface them. See `overmind-edge-networking` skill.

**Notifications.** Created via `db.add_swarm_notification` or
`postgres_store.insert_swarm_notification` (lean). Delivery is **aggregated**:
`notification_delivery.deliver_pending_notifications` builds one per-channel digest
per user, filtered by `should_notify_user` (needs `notification_settings` on the
user), a rolling window, and unread state. Rendering in `emailer.py` +
`templates/emails/`.

**Migrations.** Yoyo SQL files in `src/overmind/migrations/NNNN.name.sql` with
`-- depends:` headers. **Never edit an applied migration to add schema** — add a
new sequential `NNNN` file; all statements idempotent (`IF NOT EXISTS` /
`ADD COLUMN IF NOT EXISTS`). The runner skips by file id, so a reused number is
silently ignored. See `overmind-db-management` skill.

**Asset inventory.** Drones upload to `POST /api/devices/{id}/rom-metadata`
(`store_rom_metadata`); `update_mode` ∈ `inventory` (full, `replace_all` clears
ALL asset types), `inventory_chunk`, `inventory_delta` (honors a `deleted` set),
`rom_hash_patch`. Per-asset-class **thumbprints** are stored *verbatim* (never
recomputed) and echoed in the heartbeat so the Drone decides when to resync.

## How the networking fits together

Three planes: **control plane** (this app — authorize + mint tokens, never carries
ROM bytes) → **Edge plane** (`edge/` — persistent mux, presence, signaling, relay
of last resort) → **data plane** (Drone↔Drone, bytes touch the Edge only on relay
fallback). A pull: receiver authorizes → token → `TRANSFER_REQUEST` over its mux →
Edge offers the sender → tier selection `LAN → direct-public → hole-punch → relay`.
Full flow + wire protocol: see the Drone repo's `app/transport/` and the
`overmind-edge-networking` skill.

## Conventions

- **UI:** Bootstrap 5.3 dark theme; `table table-sm align-middle` in
  `table-responsive`; always `escapeHtml` user data. Drone/Overmind share
  branding + paging/filter patterns. See `bff-ui-theme-functionality` skill.
- Match surrounding comment density + idiom; `db.py`, `postgres_store.py`,
  `main.py` are large — prefer targeted edits over restructuring.
- Add focused tests beside existing ones (`tests/test_api.py` patterns; fake
  cursor for Postgres-only paths).
- `make format` covers `src/` only (not `tests/`); keep lines ≤100.

## Skills (`.claude/skills/`, auto-surfaced)

`overmind-db-management`, `overmind-edge-networking`, `overmind-architecture`,
`overmind-aws-deployment`, `overmind-live-debugging` (debugging live Overmind in
AWS — finding the right Lambda tier/log group, tight CloudWatch epoch queries,
reading tracebacks to the real exception, migration/schema cross-checks),
`bff-ui-theme-functionality`. Consult the matching skill before non-trivial work in
that area.
