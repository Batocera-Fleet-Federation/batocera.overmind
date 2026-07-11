---
name: overmind-live-debugging
description: Use this when debugging Overmind's live production behavior in AWS — a 500, a request that silently fails, data that doesn't show up where expected. Covers finding the right CloudWatch Lambda log group (routes are split across low/medium/high tiers), tight epoch-windowed log queries, reading tracebacks to the real exception, checking migration/schema state, and the account/RDS/ECR context needed to interpret what you find.
---

# Debugging Live Overmind in AWS

For the cross-repo methodology this plugs into (starting on the Drone, the
in-memory-dict-vs-Lambda bug class, reporting standards), see the
`bff-live-debugging` skill in `.github`. This skill is the AWS-specific depth.

## Goal

Get a real stack trace or log line from production before proposing a fix. Overmind
runs as three separate Lambda functions split by route (plus a `scheduled` one for
EventBridge jobs) — the single biggest way to waste time here is querying the wrong
one and concluding "no logs" when you just looked in the wrong place.

## Account/runtime context

```text
Account: 439024108811, region us-east-1
Lambda functions: bff-overmind-prod-{low,medium,high,scheduled}
Log groups:        /aws/lambda/bff-overmind-prod-{low,medium,high,scheduled}
RDS:               bff-overmind-prod.c2r6u6mq0c2n.us-east-1.rds.amazonaws.com (Postgres 16.3)
Runtime secret:     AWS Secrets Manager bff-overmind/prod/runtime (OVERMIND_POSTGRES_PASSWORD, etc.)
```

AWS credentials: `.github/.credentials` (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_DEFAULT_REGION`). Always go through the repo's helper so credentials are sourced
consistently:

```bash
.github/scripts/run-with-aws-credentials.sh aws sts get-caller-identity
.github/scripts/run-with-aws-credentials.sh <any aws ... command>
```

## Step 1 — find the right Lambda tier *before* querying logs

Routes are statically split in Terraform, not inferable from the route's "size" or
importance:

```bash
grep -n "your/route/path" .github/terraform/aws/us-east-1/locals.tf
```

`lambda_route_tiers` in that file lists every `high` and `medium` route explicitly.
**Anything not listed in either falls through to `low`.** Two calls a browser fires
one second apart can easily land on two different Lambda functions (e.g. `/sync-rom`
is `medium`, but `/actions/{id}/complete` isn't listed anywhere and is `low`) — check
every route you care about individually, don't assume neighbors share a tier.

`scheduled` is separate again — that's EventBridge-triggered jobs
(`notification-delivery`, `device-status`, `public-reachability`), not
request-triggered; check it for cron-job misbehavior, not user-facing 500s.
Reading `scheduled` logs correctly:

- **Some jobs log only their failures.** `public-reachability` prints a line per
  *unreachable* drone ("`Public reachability: <id> not resolvable -- ...`") and
  nothing for drones that probed fine — absence of a drone from the output means
  it PASSED, not that it wasn't probed. Don't conclude a device was flapping just
  because it never appears.
- **Shared public IPs are graded by certificate, not by HTTP success.** Multiple
  drones behind one NAT share a public IP but only one can own the :443
  port-forward; the probe hits the IP and identifies the answering drone by its
  TLS cert. The others get "`not resolvable -- public IP X answered by a different
  Drone (<id>)`" — that's correct behavior for a shared-IP site, not an error.
- **Verify the real cadence from the log timestamps, not from `locals.tf`.** The
  deployed EventBridge rule can differ from the current Terraform (observed:
  `locals.tf` said `rate(15 minutes)` while the deployed rule fired every minute —
  a later tf apply hadn't happened). The interval matters when you're reasoning
  about how stale a reachability/status flag could have been at a failure instant.

## Step 2 — query CloudWatch with a tight epoch window

Get a precise timestamp first (from the Drone's own logs — see
`drone-live-debugging` in `batocera.drone` — or from whatever the user reports).
Compute the UTC epoch portably (macOS `date -j` vs Linux `date -d`):

```bash
start_epoch=$(date -u -j -f "%Y-%m-%dT%H:%M:%S" "2026-07-11T03:41:00" +%s 2>/dev/null || date -u -d "2026-07-11T03:41:00" +%s)
end_epoch=$(date -u -j -f "%Y-%m-%dT%H:%M:%S" "2026-07-11T03:42:00" +%s 2>/dev/null || date -u -d "2026-07-11T03:42:00" +%s)

.github/scripts/run-with-aws-credentials.sh aws logs filter-log-events \
  --log-group-name /aws/lambda/bff-overmind-prod-low \
  --start-time "${start_epoch}000" --end-time "${end_epoch}000" \
  --max-items 300 --query "events[].message" --output text
```

Notes from experience:
- **Keep the window to 30–90 seconds** once you have a real anchor timestamp. Wide
  windows (hours/days) combined with a restrictive `--filter-pattern` on a busy log
  group can simply time out the CLI call — that's a tooling limitation, not evidence
  the event didn't happen.
- **Prefer pulling the window unfiltered and grepping locally** over trusting
  `--filter-pattern` for anything with punctuation (hyphens, slashes) — CloudWatch's
  filter-pattern syntax tokenizes on non-alphanumeric characters in ways that don't
  match plain-substring intuition (e.g. a pattern like `sync-rom` can match `sync`
  AND `rom` as separate terms rather than the literal substring).
- Every Overmind request logs one line via `mangum.http`:
  `<METHOD> <path with real device_id substituted> <status>`, e.g.
  `POST /api/devices/84:47:09:72:c0:df/sync-rom 200`. Grep for the device id or
  action id directly rather than guessing the exact path string.
- Each invocation is bracketed by `START RequestId: ...` / `END RequestId: ...` /
  `REPORT RequestId: ... Duration: ... Init Duration: ...` — an `Init Duration`
  present means that invocation was a **cold start** (fresh container, empty
  in-memory `db` state) — directly relevant if you're chasing an
  in-memory-dict-visibility bug (see `.github`'s `bff-live-debugging` skill, Step 6).

For a broader first-look sweep instead of a targeted query (route table dump, all
three functions' recent config/logs, ECR image info, CloudWatch alarms), the
existing script does a wider pull in one shot:

```bash
.github/scripts/run-with-aws-credentials.sh .github/scripts/debug-overmind-lambda.sh
# SINCE=6h RUN_AUTH_PROBE=true DEBUG_AUTH_EMAIL=... DEBUG_AUTH_PASSWORD=... for more
```

Use it for "is anything obviously on fire" triage; use the targeted `filter-log-events`
query above once you have a specific incident to chase — it's faster and far less
noisy.

## Step 3 — read the traceback to the innermost frame, then verify against the repo

CloudWatch prints the full Python traceback for unhandled exceptions (look for
`ERROR [overmind.main] Unhandled request error` or a second `ERROR [mangum.http]`
block). The **last few lines** are the real error — e.g.:

```text
psycopg.errors.UndefinedTable: relation "drone_action_results" does not exist
LINE 2:                     INSERT INTO drone_action_results (device...
```

not the dozen Starlette/FastAPI middleware frames above it. The traceback also
gives you the exact `/var/task/src/overmind/<file>.py:<line>` — open that file in
the checked-out repo and confirm it still matches (a deployed Lambda image can lag
behind `main` if a deploy hasn't run since the relevant commit).

**Trace what already committed vs. what threw.** A single route handler can run
several DB operations in sequence inside separate `with conn:` blocks; an earlier
one can commit cleanly and a *later*, unrelated one can then throw, producing a 500
that overstates what actually failed. Read the whole function, not just the line
the traceback points at, before concluding "nothing happened."

## Step 4 — cross-reference the schema

If the traceback names a missing table/column, check the migrations directly —
don't assume the schema matches what the code expects:

```bash
ls src/overmind/migrations/
grep -rn "table_or_column_name" src/overmind/migrations/*.sql
```

If nothing creates it, that's the bug (commonly a squash-migration regression: code
still references something the old migration history had, that the new baseline
never re-added). Fix with a **new** sequential migration — `NNNN.description.sql`
with a `-- depends: <previous file's basename without extension>` header, all
statements idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).
**Never edit an already-applied migration file** — the runner skips by file id, so
editing one that already ran on production has no effect there and just diverges
local/prod schema.

Migrations **auto-apply on Lambda cold start** (`ensure_schema()` runs them; you'll
see `Background migrations applied: [...]` in the logs for the ones registered as
non-blocking, or they simply run inline for everything else) — so deploying the new
Lambda image is enough to pick up a new migration file, no separate migration-run
step is required. Whether a specific migration ID has actually reached the deployed
image is still worth confirming after a deploy (see Step 6).

## Step 5 — Postgres/RDS (only when a CloudWatch trace genuinely isn't enough)

Connection instructions are in `.github/.credentials`. **Do not run ad hoc
`psql` against the production database on your own initiative** — prefer the
CloudWatch-log route above for evidence, since it shows you exactly what the
running code did rather than a snapshot you have to interpret. If a direct read is
genuinely necessary (e.g. confirming current row state that no log captured), ask
the user first; this is treated as a higher-risk action than log reading.

If explicitly authorized:

```bash
curl -o /tmp/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
export RDSHOST="bff-overmind-prod.c2r6u6mq0c2n.us-east-1.rds.amazonaws.com"
# password: aws secret bff-overmind/prod/runtime, key OVERMIND_POSTGRES_PASSWORD
psql "host=$RDSHOST port=5432 dbname=overmind user=overmind sslmode=verify-full sslrootcert=/tmp/global-bundle.pem"
```

Read-only exploration only (`\dt`, `SELECT ... LIMIT`, `EXPLAIN`) unless the user has
explicitly asked for a write.

## Step 6 — confirm what's actually deployed

Before concluding a fix isn't working, confirm the fix is actually live:

```bash
.github/scripts/run-with-aws-credentials.sh aws lambda get-function-configuration \
  --function-name bff-overmind-prod-medium --query "{LastModified:LastModified,State:State}"
.github/scripts/run-with-aws-credentials.sh aws lambda get-function \
  --function-name bff-overmind-prod-medium --query "Code.ResolvedImageUri" --output text
```

All four functions (`low`/`medium`/`high`/`scheduled`) share one image built from
the same commit — checking one confirms the deploy generation, but if you suspect a
partial/failed deploy, check all four (`update-overmind-lambdas.sh` updates them in
a loop; one can fail independently of the others). Deploying is
`.github/scripts/run-with-aws-credentials.sh .github/scripts/update-overmind-lambdas.sh`
— note this is a production deploy action, not something to run without the user's
go-ahead.

## Step 7 — the recurring bug class (know this before you start)

`db.py` keeps a lot of state in per-process in-memory dicts, mirrored to Postgres
only through a full-state snapshot whose *read* side is skipped once real
relational Postgres is configured. Anything relying solely on that dict is
invisible across Lambda containers/functions — see `.github`'s `bff-live-debugging`
skill, Step 6, for the full pattern and the fix shape (`is_peer_resolvable`,
`record_sync_activity`/`list_sync_activity_for_device`, and
`list_peer_resolvable_targets` — which fixed the heartbeat swarm payload — are the
existing examples to mirror). If a symptom is "shows up once then disappears" or
"works sometimes", check for this before looking anywhere else.

Two refinements from live incidents:

- **Response builders count, not just GET endpoints.** The bug isn't limited to
  "read an in-memory dict and return it" — any handler that *derives* fields for a
  response payload from in-memory state is affected. `get_swarm_for_device` graded
  every peer's `public_resolvable` from the in-memory `peer_checks` dict while
  assembling the heartbeat response, so which container served a drone's heartbeat
  determined whether its peers looked reachable — the flag then flapped in the
  drone's own cache once a minute. When a *drone-side* cached value flaps, suspect
  the Overmind handler that produces it.
- **Correlate flapping with cold starts.** Each invocation's `REPORT` line carries
  `Init Duration` only on a cold start (fresh container = completely empty
  in-memory dicts). A failure that lines up with an `Init Duration` invocation —
  or with a burst of new `START RequestId` container ids — is strong evidence for
  this class even before you find the guilty dict.

## Safety rules

- Read-only by default. Deploys, migrations reaching production, and direct RDS
  writes all need explicit user approval — you generally cannot run
  `update-overmind-lambdas.sh` yourself in this environment; hand it back with the
  exact command.
- Never print secrets (DB password, `SECRET_KEY`, tokens) from Secrets Manager or
  `.github/.credentials` into shared output.
- Don't restart/redeploy a Lambda "to see if it helps" — that's a production
  action taken to dodge diagnosis, not a diagnosis.

## Expected output format

```text
Lambda tier(s) checked:
... (which function, and how you confirmed the route belongs there)

Time window queried:
... (UTC start/end, log group)

Evidence:
... (exact log lines / traceback, file:line cross-referenced against repo source)

Schema check:
... (migration grep result, if relevant)

Root cause:
... (PROVEN vs LIKELY)

Deploy state:
... (confirmed via get-function-configuration, or "not checked")
```
