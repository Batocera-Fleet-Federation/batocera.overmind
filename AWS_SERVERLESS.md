# Overmind AWS Serverless Deployment

Overmind now supports a Lambda-first AWS deployment while keeping the same
FastAPI codebase and the existing EC2/Docker deployment as a rollback path.

## Local Development

Local workflows are unchanged:

```bash
python3 -m uvicorn src.overmind.main:app --reload --host 0.0.0.0 --port 8000
USE_FAKE_DATA=true python3 -m uvicorn src.overmind.main:app --reload --host 0.0.0.0 --port 8000
docker-compose up
```

Scheduled jobs can be tested locally without AWS:

```bash
scripts/run-scheduled-job.sh public-reachability
scripts/run-scheduled-job.sh notification-delivery
scripts/run-scheduled-job.sh device-status
```

## Lambda Image

Build and push the Lambda-compatible image:

```bash
TAG=lambda-latest scripts/docker-publish-lambda-ecr.sh
```

After the Lambda functions exist, refresh them to the pushed image:

```bash
TAG=lambda-latest scripts/update-lambda-functions.sh
```

The standard `Dockerfile` remains for local/EC2 use. `Dockerfile.lambda` uses
the AWS Lambda Python base image and starts `overmind.lambda_handler.handler`.

## Endpoint Tiers

Lambda CPU is configured indirectly through memory. The Terraform stack creates
three API functions from the same image:

- `low`: 1024 MB, short timeout, default route for simple requests.
- `medium`: 2048 MB, admin/device/system/log/config routes.
- `high`: 4096 MB, ROM metadata, master asset, and bulk sync routes.

The explicit route map lives in `.github/terraform/aws/us-east-1/locals.tf`.
Routes not listed there fall through to the low tier through API Gateway's
`$default` route.

## Background Jobs

Long-running startup threads are disabled in Lambda. EventBridge invokes
`overmind.lambda_handler.scheduled_handler` for:

- `public-reachability`
- `notification-delivery`
- `device-status`

Local Uvicorn/Docker still starts the existing pollers unless the interval env
vars disable them.

## Terraform Remote State

Bootstrap the S3 state bucket and DynamoDB lock table first:

```bash
cd ../.github/terraform/bootstrap/us-east-1
terraform init
terraform apply
```

Then migrate the existing Overmind state:

```bash
cd ../../aws/us-east-1
terraform init -migrate-state
```

The backend is configured in `.github/terraform/aws/us-east-1/backend.tf`.

## Terraform Apply

Apply the main stack after the Lambda image has been pushed at least once:

```bash
cd ../.github/terraform/aws/us-east-1
terraform init
terraform plan
terraform apply
```

The current EC2 deployment remains in place. The Lambda endpoint is available
from the `lambda_api_endpoint` Terraform output and can be tested before moving
DNS from EC2.

## Networking Notes

Lambda functions run inside the Overmind VPC so they can reach RDS. Direct RDS
connectivity is the default because some AWS free-plan accounts block RDS
Proxy. Set `enable_rds_proxy = true` in Terraform when the account supports it.
The stack adds a Secrets Manager VPC endpoint for cold-start secret loading.
External outbound access for SMTP, OAuth, Slack/Discord webhooks, or other
public APIs still requires NAT or a provider-specific VPC endpoint. The existing
EC2 deployment is not changed.
