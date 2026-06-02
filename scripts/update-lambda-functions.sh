#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="${PROJECT_NAME:-bff-overmind}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
ECR_REPO="${ECR_REPO:-batocera-overmind}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_TAG="$(cat "${REPO_ROOT}/VERSION" 2>/dev/null || echo lambda-latest)"
TAG="${TAG:-$DEFAULT_TAG}"

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${TAG}"

functions=(
  "${PROJECT_NAME}-${ENVIRONMENT}-low"
  "${PROJECT_NAME}-${ENVIRONMENT}-medium"
  "${PROJECT_NAME}-${ENVIRONMENT}-high"
  "${PROJECT_NAME}-${ENVIRONMENT}-scheduled"
)

for fn in "${functions[@]}"; do
  echo "Updating ${fn} -> ${IMAGE_URI}"
  aws lambda update-function-code \
    --region "${AWS_REGION}" \
    --function-name "${fn}" \
    --image-uri "${IMAGE_URI}" >/dev/null
done

echo "Done."
