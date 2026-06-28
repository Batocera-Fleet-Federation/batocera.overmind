---
name: overmind-edge-networking
description: Use this when designing, reviewing, debugging, or modifying the Overmind Edge service and control-plane networking — the asyncio mux MuxServer, drone presence/registry, the relay hub + bandwidth limiting, STUN/signaling for hole punching, the mux wire protocol, transfer-token minting/validation, transfer_sessions lifecycle + the /api/admin/transfers monitoring, or Edge deployment (edge_app, Dockerfile.edge, edge.tf).
---

# Overmind Edge & Control-Plane Networking Skill

## Goal

Run the always-on **Edge** that lets every Drone connect **outbound-only** (no
router config) and move assets peer-to-peer, with the Edge **relaying bytes only as
a last resort**. Keep the **control plane** (the FastAPI app) authoritative for
auth/RBAC/tokens and **never carrying ROM bytes**.

## Project context

Three planes:
- **Control plane** = the FastAPI app (Lambda/uvicorn). Auth, RBAC, registration,
  CA/cert signing, device records, UI, and **transfer-token mint**
  (`POST /api/devices/{id}/transfers`). Never carries ROM bytes.
- **Edge plane** = `src/overmind/edge/`, an always-on asyncio process. Terminates
  each Drone's persistent mux, tracks presence, brokers signaling, reports
  reflexive addresses (STUN), and **relays** bytes only when direct fails.
- **Data plane** = Drone↔Drone. Bytes touch the Edge only on relay fallback and
  never touch the control plane.

Why a separate process: Lambda + API-Gateway-HTTP cannot hold persistent sockets or
stream relayed bytes. The Edge runs on ECS Fargate (`edge.tf`, gated on
`var.enable_edge`) or co-located for self-host; it reuses the same store/CA/secrets.

```text
src/overmind/edge/
  server.py    # asyncio TLS MuxServer: HELLO auth, presence, ping, relay, OFFER, signaling
  auth.py      # DbAuthenticator (verify_drone_token) / AllowAllAuthenticator
  registry.py  # PresenceRegistry (+ persistence via update_device_edge_presence)
  relay.py     # RateLimiter, RelayHub, RelaySession/RelayLeg
  stun.py      # UDP STUN reflector for hole-punch candidate gathering
  protocol.py  # frame codec — byte-identical to drone app/transport/mux.py
  edge_app.py  # EdgeConfig.from_env, ssl context, builders, off-loop DB writers, main()
overmind/transfer_tokens.py  # mint/verify HMAC-SHA256 transfer tokens
```

## Core rules

1. **Control plane never carries ROM bytes.** Authorize + mint tokens only; the
   data plane moves bytes.
2. **Drones connect outbound only.** The Edge accepts; it never dials a Drone.
   Nothing expects an inbound Drone API.
3. **Relay is last resort.** The Edge offers/pairs and relays only when direct/
   hole-punch fail. It forwards `DATA` frames; it does not interpret ROM bytes.
4. **Validate transfer tokens offline.** When `transfer_secret` is set, the Edge
   verifies the HMAC token and checks it binds `{sid, to, from, asset}` before
   sending a `TRANSFER_OFFER`. With no secret (local/dev), tokens are not enforced —
   never ship that to prod.
5. **Never block the event loop.** DB writes (presence, transfer status) go off-loop
   (executor / `run_in_executor`) and are **best-effort, fire-and-forget** — a
   slow/flaky DB must never stall or break a relay or a Drone's connection.
6. **Lean DB path.** The Edge (like Lambda) uses targeted lean methods
   (`update_device_edge_presence`, `create/update/expire/list_recent_transfer_sessions`),
   never `store_app_state` (a partial snapshot would clobber other writers).
7. **One wire format.** `edge/protocol.py` must stay byte-identical to the Drone's
   `app/transport/mux.py` (golden-vector test). Add message types in both.
8. **Reuse the existing CA/auth.** `DbAuthenticator` uses `verify_drone_token`;
   don't invent a parallel trust model.
9. **TLS, but terminable upstream.** The Edge speaks TLS, or runs
   `EDGE_ALLOW_INSECURE` behind an NLB/proxy that terminates TLS in-VPC. Never
   expose plaintext to the internet.
10. **Migrations are additive + idempotent.** New `NNNN.name.sql` only
    (`edge_presence`, `transfer_sessions`); see `overmind-db-management`.

## Mux server rules

`MuxServer` (asyncio, one `_Connection` per Drone, per-connection write lock):

1. First frame must be `HELLO`; authenticate via the injected `Authenticator`,
   reply `HELLO_ACK` with `session_id` + the Drone's reflexive address.
2. Register presence on connect, deregister on disconnect **guarded by session id**
   (a reconnect must not let a stale close wipe the new entry).
3. Drive liveness with `PING`/`PONG` + missed-ping timeout.
4. Map `device_id → connection` so a receiver's `TRANSFER_REQUEST` can push a
   `TRANSFER_OFFER` to the sender's mux.
5. Relay: `RELAY_OPEN` registers a leg; when both legs pair, emit `RELAY_READY` and
   forward `DATA` frames peer→peer; `RELAY_CLOSE`/teardown notifies the peer.
6. Report transfer lifecycle via the optional `transfer_status(session_id, status)`
   hook: `active` on pair, `completed` on graceful close, `aborted` on drop. It is
   best-effort/fire-and-forget (`_emit_transfer_status` swallows hook errors and
   schedules coroutine hooks without awaiting them).
7. Forward `SIGNAL` frames between paired legs for hole-punch candidate exchange.

## Relay hub rules

- `RelayHub.open_leg` pairs `sender`/`receiver` legs by `session_id`;
  `forward(session_id, conn_id, payload)` sends to the peer leg.
- Bandwidth limiting is **per session** (`RateLimiter` token bucket via
  `limiter_factory`, `EDGE_RELAY_BW_LIMIT_BPS`); the limit excludes the 32-byte
  session-id prefix. Keep it per-session so one transfer can't starve others.
- A dropped connection's legs are cleaned up and the peer is notified; a
  double-close / late teardown of an already-closed session is a no-op (don't
  re-emit lifecycle or crash).

## Transfer token + monitoring rules

- Mint at `POST /api/devices/{id}/transfers` (`mint_transfer_token`,
  HMAC-SHA256 `{sid,from,to,asset,exp}`, signed with `SECRET_KEY`). Dual-auth: a
  receiver-drone token **or** a user; for drone-auth, enforce same-swarm source.
- Tokens are short-lived; sessions expire on `exp` + Edge idle. The `device-status`
  job calls `expire_transfer_sessions` to mark stale `offered`/`active` rows.
- `transfer_sessions` is the monitoring surface: `create` at mint, Edge updates
  status, `list_recent_transfer_sessions` powers `GET /api/admin/transfers`
  (super-admin) + the Super-Admin "Peer Transfers" UI panel. Show `transport_used`
  + status so the data plane is observable without router/log access.

## Deployment rules

- Entry point: `python -m overmind.edge.edge_app`; `Dockerfile.edge` mirrors the
  REST image, exposes 9443. `EdgeConfig.from_env` reads `EDGE_*`.
- Key env: `EDGE_PORT`, `EDGE_STUN_PORT` (0 disables STUN/hole-punch),
  `EDGE_AUTH` (`db`/`allow-all`), `EDGE_ALLOW_INSECURE` (TLS upstream),
  `EDGE_TLS_SELF_SIGNED`, `EDGE_RELAY_BW_LIMIT_BPS`, plus DB creds + `SECRET_KEY`.
- AWS: `edge.tf` (ECS Fargate + NLB + ACM + Route53 + SG + ECR + IAM), gated on
  `var.enable_edge` (default false → no-op). `terraform fmt`/`validate` must pass;
  the user runs `plan`/`apply`. Follow-ups: publish the edge image to ECR; add a
  STUN/UDP NLB listener for prod hole-punch.

## Testing rules

- Edge pieces are asyncio/pure and unit-testable in `tests/test_edge.py`: protocol
  round-trip + golden vector, registry (session-guarded deregister), auth, relay
  pairing + lifecycle hook + bandwidth, an end-to-end `build_server` handshake.
- Postgres-only methods (presence, transfer sessions) use a fake cursor/connection
  (see `tests/test_edge.py`, `tests/test_transfer_tokens.py`).
- The real cross-repo relay E2E lives in
  `.github/tests/test_edge_relay_integration.py` — keep it green on any wire/relay
  change.

## Common failure patterns

Look for these first:

- A DB call awaited on the event loop (stalls all connections/relays).
- Presence deregister not guarded by session id (reconnect race wipes the live row).
- `store_app_state`/full-state used from the Edge (clobbers other writers).
- `transfer_secret` unset in a non-dev deploy (tokens unenforced).
- `edge/protocol.py` drifts from the Drone `mux.py` (golden-vector test breaks).
- Relay bandwidth made global instead of per-session (one transfer starves others).
- Double-close / teardown of an already-closed session re-emits or crashes.
- Plaintext exposed to the internet (must be TLS or TLS-terminated upstream).
- A new migration reuses an `NNNN` (runner skips it silently).
- `/api/admin/transfers` not super-admin gated.

## Expected output format

When completing Edge / control-plane networking work, respond using this format:

```text
Objective:
...
Mux server / protocol changes:
...
Relay / bandwidth changes:
...
Presence / signaling / STUN changes:
...
Transfer tokens / authorization changes:
...
transfer_sessions / monitoring changes:
...
Deployment (edge_app / Dockerfile.edge / edge.tf):
...
DB (lean methods / migrations):
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

- carry ROM bytes through the control plane,
- expect inbound connections to a Drone,
- await DB calls on the event loop or let DB failures break a relay/connection,
- use `store_app_state`/full-state from the Edge,
- ship with `transfer_secret` unset (tokens unenforced),
- let `edge/protocol.py` drift from the Drone codec,
- make relay bandwidth global instead of per-session,
- expose plaintext to the internet,
- reuse a migration number,
- un-gate the admin transfers endpoint.

## Default bias

When unsure, choose the option that keeps the control plane bytes-free, the Edge
relaying only as last resort, DB writes off-loop + best-effort, tokens enforced and
short-lived, the wire format identical across repos, bandwidth limited per session,
and presence/transfer state on the lean DB path — durable but never blocking.
