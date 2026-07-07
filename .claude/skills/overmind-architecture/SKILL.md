---
name: overmind-architecture
description: Use this when designing, reviewing, debugging, or modifying Overmind's FastAPI application architecture, module layout (main.py, db.py, postgres_store.py, models.py, edge/), the dual in-memory/relational data-layer split, authentication (JWT, bcrypt, OAuth via user_auth_identities, drone mTLS/CA signing, drone device tokens, HMAC transfer tokens), route groups and Lambda-tier routing, Lambda-vs-uvicorn runtime switching, or request/response Pydantic contracts.
---

# Overmind Application Architecture Skill

## Goal

Keep the mental model of "what Overmind is" current. It is **not** a toy FastAPI
app with an in-memory store and Postgres as future work — it's a control plane
(auth/RBAC/registration/CA) with Postgres as the relational source of truth, plus
an Edge plane for outbound-only Drone networking. `ARCHITECTURE.md` at the repo
root is a stale, unmaintained snapshot (in-memory-only, 4 modules, ~9 routes,
JWT+bcrypt+CORS-only) — do not trust it; this skill supersedes it.

## Project context

`src/overmind/` (~16,500 lines across ~22 top-level modules + the `edge/`
subpackage + `migrations/`):

```text
src/overmind/
  main.py                  # 3,292 lines — FastAPI app, ~90+ route decorators
  db.py                    # 4,054 lines — in-memory facade (OvermindDatabase)
  postgres_store.py        # 5,885 lines — PostgresMetadataStore, the real store
  models.py                #   965 lines — ~110 Pydantic request/response models
  auth.py                  # JWT + bcrypt: create/verify tokens, password hashing
  drone_ca.py              # local CA: sign_drone_csr() issues per-drone mTLS certs
  tls_server.py            # ensure_self_signed_cert / run_https_app (uvicorn TLS)
  drone_security.py        # generate/hash/verify_drone_token (drone device tokens)
  transfer_tokens.py       # mint/verify_transfer_token (HMAC-SHA256, P2P handoff)
  access_policy.py         # require_swarm_role / require_device_admin / require_super_admin
  notification_delivery.py # 489 lines — per-channel digest aggregation
  emailer.py                # renders templates/emails/, sends via SMTP
  managed_configs.py        # curated Batocera-config registry (green/yellow availability)
  account_notifications.py  # superadmin alerts via a hidden system swarm
  cache.py                  # generic TTL cache helper
  device_snapshots.py       # 248 lines — point-in-time device/system snapshots
  networking.py              # public-reachability probe helpers
  presenters.py               # response-shaping helpers shared across endpoints
  runtime_metrics.py / runtime_secrets.py  # AWS-side metrics + Secrets Manager
  lambda_handler.py         # Mangum adapter: handler() (API GW) + scheduled_handler()
  edge/                     # always-on Edge process — see overmind-edge-networking
  migrations/               # Yoyo SQL, NNNN.name.sql — see overmind-db-management
```

Two runtimes share the same FastAPI `app`:
- **uvicorn** (Docker/EC2) — long-lived process, in-memory `db.py` state persists
  between requests, background pollers run as threads.
- **Lambda** (`OVERMIND_RUNTIME=lambda`, `lambda_handler.py` via Mangum) —
  API Gateway → `handler()`; EventBridge → `scheduled_handler({"job": ...})` →
  `run_scheduled_job` → `poll_*_once`. **In-memory state is empty on every cold
  invocation** — anything a request/job needs must come from a `load_*`/`hydrate_*`
  Postgres call, never assumed to already be in `db.py`'s dicts.

## Dual data-layer rule

`db.py` is a **repository facade** holding in-memory dicts (`users`, `devices`,
`roms`, …) — it is not the source of truth. `postgres_store.py`
(`postgres_store` singleton, class `PostgresMetadataStore`) is. Two access
patterns coexist:

- **Full-state path**: `db._persist_state()` → `postgres_store.store_app_state()`
  mirrors the *entire* in-memory snapshot (`_mirror_*`, upsert/`ON CONFLICT`,
  never deletes). Dangerous if driven by a partial snapshot — clobbers columns
  owned by another writer (has caused real bugs, e.g. reachability-field resets).
- **Lean path**: scheduled jobs + hot endpoints write/read targeted rows
  directly (`insert_swarm_notification`, `update_device_reachability`,
  `update_device_edge_presence`, `create/update/expire/list_recent_transfer_sessions`,
  …). Lambda runs almost entirely on this path.

Schema/query/migration depth (join tables, indexes, pagination patterns) lives in
`overmind-db-management` — don't duplicate those rules here, just know which path
a given piece of code is on.

## Authentication & authorization

Four distinct trust mechanisms exist today (the old doc only covers the first):

1. **User auth** — JWT (`auth.py`, `create_access_token`/`decode_access_token`) +
   bcrypt password hashing. `SUPER_ADMIN_EMAIL` (hardcoded in `main.py`) auto-grants
   super-admin on signup with that exact email.
2. **OAuth** — Google/GitHub, `user_auth_identities` table (added in
   `0001.initial_schema.sql` alongside `auth_provider`), `/api/auth/{provider}/start`
   + `/api/auth/{provider}/callback`; state must be verifiable **statelessly**
   across Lambda invocations (no shared in-memory state to hold a CSRF nonce).
3. **Drone trust** — two layers: mTLS via a local CA (`drone_ca.sign_drone_csr`,
   issued at drone registration, verified in `tls_server.py`/Drone-side transport)
   **and** a per-device bearer token (`drone_security.generate_drone_token` /
   `verify_drone_token`, hashed at rest, rotatable).
4. **Transfer tokens** — short-lived HMAC-SHA256 tokens
   (`transfer_tokens.mint_transfer_token`/`verify_transfer_token`) minted at
   `POST /api/devices/{id}/transfers`, binding `{sid, from, to, asset, exp}`;
   validated **offline** by the Edge (see `overmind-edge-networking`).

Authorization (not authentication) is centralized in `access_policy.py`:
`ensure_active_user`, `require_swarm_role` (role-gated swarm actions),
`require_device_admin`, `require_super_admin`. Route handlers should call into
these, not hand-roll role checks.

## Route map

`main.py` has ~90+ `@app.get/post/put/delete` decorators — far more than any
static doc should try to enumerate. Group by area when reasoning about it:
`/api/auth/*` (register/login/refresh/verify-email/forgot-password/oauth),
`/api/devices*` (register, list, detail, roms/bios/artwork sync + master-*
paginated reads, gameplay/game-logs, emulator-configs), `/api/swarms*`
(create/invite/members/access/roles), `/api/notifications*`,
`/api/admin/*` (overview, sync-actions, audit-log, transfers, runtime-metrics/logs,
run-job, per-user/per-drone actions — super-admin gated),
`/api/integration-tokens`, `/api/hive`, `/api/downloads`, `/api/bulk-sync`.

For the **authoritative current list with Lambda tier assignment**, don't rely on
a hand-maintained enumeration — read
`.github/terraform/aws/us-east-1/locals.tf`'s `lambda_route_tiers` map (`high` =
ROM-metadata/master-asset/bulk-sync routes; `medium` = admin/device/swarm/log
routes; anything unlisted falls through to `low` via API Gateway's `$default`).
Adding a new route usually means adding it to one of these tiers too — see
`overmind-aws-deployment`.

## Notifications & migrations (brief — depth lives elsewhere)

Notifications: created via `db.add_swarm_notification` or
`postgres_store.insert_swarm_notification` (lean), delivered as an aggregated
per-channel digest by `notification_delivery.deliver_pending_notifications`
(rolling window, `should_notify_user`, unread state), rendered by `emailer.py` +
`templates/emails/`.

Migrations: Yoyo SQL in `migrations/NNNN.name.sql` with `-- depends:` headers.
Current count is **3** (`0001.initial_schema`, `0002.bios_system_association`,
`0003.pixen_system_info`) — reconfirm with `ls src/overmind/migrations/` before
relying on this, it grows often. Never edit an applied migration; full rules in
`overmind-db-management`.

## Edge plane (pointer only)

`src/overmind/edge/` is an always-on asyncio process (added 2026-06-29) that lets
Drones connect **outbound-only** and move assets peer-to-peer, with the Edge
relaying bytes only as a last resort. It's architecturally a third plane
alongside control-plane (this skill) and data-plane (Drone↔Drone). Full depth —
mux wire protocol, presence, relay, STUN, deployment — lives in
`overmind-edge-networking`; don't duplicate those rules here, just know it exists
and that the control plane never carries ROM bytes itself.

## Common failure patterns

- Treating `db.py`'s in-memory dicts as durable state (they're empty per Lambda
  cold invocation; only Postgres persists).
- Assuming Postgres is "future work" — it's the current source of truth.
- Adding a route without adding it to a `locals.tf` tier — it silently falls to
  `low`, which may be the wrong memory/timeout budget for a heavy endpoint.
- Bypassing `access_policy.py` and hand-rolling a role check in a route handler.
- Forgetting OAuth state must survive statelessly across Lambda invocations (no
  in-memory nonce cache).
- Using `store_app_state`/full-state mirroring from a partial snapshot (clobbers
  another writer's columns).
- Confusing drone mTLS (`drone_ca.py`) with drone bearer tokens
  (`drone_security.py`) — they're independent, complementary trust layers, not
  alternatives to each other.

## Expected output format

When completing architecture-level work, respond using this format:

```text
Objective:
...
Module(s) touched:
...
Data-layer path (full-state vs. lean):
...
Auth/authorization mechanism affected:
...
Routes added/changed (+ Lambda tier, if any):
...
Runtime considerations (Lambda vs. uvicorn):
...
Tests:
...
Risks:
...
Files changed:
...
```

## Safety rules

Do not:

- reintroduce in-memory-only durable state (Postgres must stay authoritative),
- bypass `access_policy.py`'s centralized role/authorization checks,
- add a route without deciding its Lambda tier in `locals.tf`,
- assume single-runtime (uvicorn-only) behavior for anything Lambda also serves,
- duplicate Edge wire-protocol/mux/relay rules here — those live in
  `overmind-edge-networking`.

## Default bias

When unsure, keep Postgres authoritative, keep authorization centralized in
`access_policy.py`, keep route additions explicitly tiered in `locals.tf`, keep
Edge/data-plane depth out of this skill (defer to `overmind-edge-networking`),
and keep schema/migration depth out of this skill (defer to
`overmind-db-management`).
