---
name: overmind-aws-deployment
description: Use this when designing, reviewing, debugging, or modifying Overmind's AWS deployment — Lambda image build/publish, tiered Lambda endpoints (low/medium/high in locals.tf), EventBridge scheduled jobs, Terraform (main.tf, edge.tf, locals.tf, backend.tf), VPC/RDS/NAT/RDS-Proxy networking, ElastiCache/Redis wiring, the Edge Fargate/NLB stack, or Terraform remote-state bootstrap and apply.
---

# Overmind AWS Deployment Skill

## Goal

Keep the AWS deployment picture complete. This is no longer "3 Lambda tiers +
local dev" — it now also includes an always-on Edge Fargate service and
ElastiCache/Redis. `AWS_SERVERLESS.md` at the repo root predates both and is
missing them entirely; this skill supersedes it for anything Edge- or
Redis-related, and carries forward the parts of that doc that are still accurate.

## Project context

```text
.github/terraform/aws/us-east-1/
  main.tf        # 900+ lines — VPC, RDS, Lambda functions/API GW, ElastiCache, IAM
  edge.tf         # 288 lines — ECS Fargate + NLB + ACM + Route53 for the Edge
  locals.tf       # route→tier map, lambda_function_tiers, scheduled_jobs
  backend.tf      # S3 + DynamoDB remote-state config

batocera.overmind/
  Dockerfile          # standard image, local Docker/uvicorn use
  Dockerfile.lambda    # AWS Lambda Python base image, starts lambda_handler.handler
  Dockerfile.edge      # Edge image, starts overmind.edge.edge_app
  scripts/
    docker-publish-lambda-ecr.sh   # build+push the Lambda image
    update-lambda-functions.sh     # point Lambda functions at the pushed image
    run-scheduled-job.sh <job>     # run a scheduled job locally without AWS
```

## Local development

Unchanged from the original doc — still accurate:

```bash
python3 -m uvicorn src.overmind.main:app --reload --host 0.0.0.0 --port 8000
USE_FAKE_DATA=true python3 -m uvicorn src.overmind.main:app --reload --host 0.0.0.0 --port 8000
docker-compose up
scripts/run-scheduled-job.sh public-reachability   # or notification-delivery / device-status
```

## Lambda image & tiers

Build/publish: `scripts/docker-publish-lambda-ecr.sh` then
`scripts/update-lambda-functions.sh` (both default to the version in `VERSION`,
override with `TAG=<version>`). Three API Lambda functions share one image, sized
via `locals.tf`'s `lambda_function_tiers` (`low`/`medium`/`high` → memory +
timeout, tunable via `var.lambda_{low,medium,high}_{memory_mb,timeout_seconds}`).

Route→tier assignment is explicit in `locals.tf`'s `lambda_route_tiers`:

- **high** (heaviest payloads): `rom-metadata`, `drones/rom-metadata`,
  `master-roms`, `master-bios`, `master-artwork`, `sync-artwork-bulk`,
  `sync-system`, `bulk-sync`.
- **medium**: `admin/{proxy+}`, device CRUD/list, `systems`, `roms`, `bios`,
  `sync-rom`, `sync-bios`, `sync-artwork`, `gameplay`, `game-logs`,
  `log-sources`, `emulator-configs`, `gamelogs`, `downloads`, `sync-activity`,
  `hive`, `swarms*`.
- **low** (everything else): the implicit default via API Gateway's `$default`
  route — a route not listed under `high`/`medium` lands here. When adding a
  route that's payload-heavy or latency-sensitive, add it to the right tier
  explicitly; don't rely on the default for anything but genuinely light routes.

## Scheduled jobs (EventBridge)

`locals.tf`'s `scheduled_jobs` map, all invoking
`overmind.lambda_handler.scheduled_handler({"job": ...})`:

- `notification-delivery` — `rate(3 minutes)`. Must stay at or below
  `NOTIFICATION_AGGREGATION_WINDOW_MINUTES` (default 3) so every queued
  notification gets multiple delivery chances before aging out of the window.
- `device-status` — `rate(5 minutes)`. Also expires stale `offered`/`active`
  `transfer_sessions` rows past their token `exp`.
- `public-reachability` — `rate(15 minutes)`. **Conditional default, the single
  biggest gap in the old doc**: OFF when `OVERMIND_EDGE_ENABLED` is set
  (Terraform sets it from `var.enable_edge`, since outbound-only Edge Drones
  don't need the legacy inbound-reachability probe), ON without an Edge so
  cross-network Drones keep a direct WAN path. The EventBridge rule always
  fires; the job just no-ops cheaply when disabled.
  `OVERMIND_PUBLIC_REACHABILITY_ENABLED` overrides either way if you need to
  force it.

Local Docker/uvicorn still runs the equivalent pollers as background threads
unless their interval env vars disable them.

## Core networking (VPC/RDS/NAT/RDS-Proxy)

Still accurate from the original doc: Lambda functions run inside the Overmind
VPC to reach RDS. Direct RDS connectivity is the default (some free-tier AWS
accounts block RDS Proxy) — set `enable_rds_proxy = true` when the account
supports it (`local.rds_proxy_enabled` flips `local.lambda_db_host` to the
`aws_db_proxy` endpoint). A Secrets Manager VPC endpoint speeds cold-start
secret loads. `lambda_create_nat_gateway = true` (prod default) creates private
Lambda subnets + a NAT Gateway + private route table — needed for any outbound
call (SMTP, OAuth callbacks, Slack/Discord webhooks); without it, those calls
hang until Lambda timeout.

## ElastiCache/Redis (missing from the old doc)

Gated on `var.enable_elasticache` (default false, `main.tf` lines ~363-413):
`aws_elasticache_cluster.overmind` (node type `var.elasticache_node_type`) behind
its own security group (`elasticache_from_lambda` ingress rule +
`elasticache_egress`). When enabled, `OVERMIND_REDIS_URL` is injected as an env
var into **both** Lambda tiers (`main.tf` ~lines 707-708, 751-752) **and** the
Edge ECS task (`elasticache_from_edge` SG rule, see `edge.tf`) — if you add a new
Redis consumer anywhere, make sure it's actually wired the env var on every
runtime that needs it, not just one.

## Edge Fargate/NLB deployment (entirely missing from the old doc)

`edge.tf` (288 lines), gated on `var.enable_edge` (default false → no-op):
ECR repo for the edge image, an NLB with a DNS-validated ACM cert (Route53) on
:443 forwarding to the ECS task's :9443, an ECS Fargate cluster/task/service
running `overmind.edge.edge_app`, task/execution IAM roles, and security-group
ingress from the Edge to RDS (5432) and ElastiCache (6379).

**Deploy ordering constraint** — the ECS service references an image in a
Terraform-created ECR repo, and there's no CI pushing that image yet, so order
matters:

```bash
# 1. app code (new endpoints/migrations) via the normal Lambda deploy
.github/scripts/run-with-aws-credentials.sh .github/scripts/update-overmind-lambdas.sh
# 2. set enable_edge=true, create the ECR repo first (before the image exists)
terraform apply -target=aws_ecr_repository.edge
# 3. build + push the edge image (context = batocera.overmind/)
docker build -f batocera.overmind/Dockerfile.edge -t $ECR/batocera-edge:edge-latest batocera.overmind
docker push $ECR/batocera-edge:edge-latest
# 4. full apply (ECS/NLB/ACM/Route53 — a few minutes), then read the endpoint
terraform apply && terraform output edge_endpoint   # -> tls://edge.<domain>:443
# 5. point drones at it: DRONE_EDGE_ENABLED=1, DRONE_EDGE_URL=<edge_endpoint>
```

Cost: **not free-tier** — Fargate (~$18/mo at 0.5 vCPU/1GB default) + NLB
(~$16-21/mo) run 24/7, roughly $35-40/mo plus relay egress. Cheaper alternative:
self-host the `bff-edge` container on existing/cheap compute instead of
Fargate+NLB, or shrink `edge_cpu`/`edge_memory`. This section owns only the
Terraform mechanics of the Edge — for *why* it exists and how it behaves
(mux/relay/presence), see `overmind-edge-networking`.

## Terraform remote state & apply

Unchanged, still accurate:

```bash
# bootstrap (one-time): S3 state bucket + DynamoDB lock table
cd .github/terraform/bootstrap/us-east-1 && terraform init && terraform apply
# migrate existing state, then normal plan/apply
cd .github/terraform/aws/us-east-1
terraform init -migrate-state
terraform plan
terraform apply
```

Backend config is `backend.tf`. `terraform fmt`/`validate` are safe to run
proactively; `plan`/`apply` are the user's call — always run `plan` and let them
review before `apply`, per this repo's established safety norm around
infrastructure changes.

## Common failure patterns

- Assuming `enable_edge`/`enable_elasticache` are on by default — both are
  opt-in, default false.
- Hardcoding `public-reachability` on or off instead of preserving the
  `OVERMIND_EDGE_ENABLED`-conditional default.
- Building/pushing the Edge image before its ECR repo exists (step ordering).
- Adding a Redis consumer without wiring `OVERMIND_REDIS_URL` into every runtime
  that needs it (both Lambda tiers + the Edge task are three separate places).
- Treating a heavy new route as "fine on the default `low` tier" without
  checking its actual payload/latency profile.
- Running `terraform apply` without the user's explicit go-ahead.

## Expected output format

When completing AWS-deployment work, respond using this format:

```text
Objective:
...
Terraform files changed:
...
Lambda tier impact:
...
Scheduled job impact:
...
Networking (VPC/RDS/NAT/ElastiCache) changes:
...
Edge (Fargate/NLB) changes:
...
terraform fmt/validate status:
...
Deploy sequence:
...
Risks:
...
Files changed:
...
```

## Safety rules

Do not:

- run `terraform apply` (or `plan` against prod state) without the user's
  explicit go-ahead — `fmt`/`validate` are fine to run autonomously,
- hardcode the `public-reachability` default instead of keeping it conditional
  on `OVERMIND_EDGE_ENABLED`,
- build/push the Edge image before `aws_ecr_repository.edge` exists,
- commit `.github/.credentials` or print AWS secrets,
- enable `enable_edge`/`enable_elasticache` without flagging the recurring cost
  to the user (~$35-40/mo for the Edge; ElastiCache node cost varies by type).

## Default bias

When unsure, keep Edge/ElastiCache opt-in (default off), keep the
public-reachability conditional intact, keep Lambda route-tier assignment
explicit in `locals.tf` rather than relying on the `low` default, and always
run `terraform plan` before recommending `apply`.
