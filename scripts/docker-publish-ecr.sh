#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="${ECR_REPO:-batocera-overmind}"
TAG="${TAG:-latest}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER_NAME="${BUILDER_NAME:-batocera-ecr-builder}"
DOCKERFILE="${DOCKERFILE:-Dockerfile}"
PUSH_LATEST="${PUSH_LATEST:-true}"

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_IMAGE="${ECR_REGISTRY}/${ECR_REPO}"

cd "${REPO_ROOT}"

if [[ ! -f "${DOCKERFILE}" ]]; then
  echo "ERROR: ${DOCKERFILE} not found at repo root: ${REPO_ROOT}" >&2
  echo "Run this script from anywhere; it will automatically build from ${REPO_ROOT}." >&2
  exit 1
fi

echo "Repo root:    ${REPO_ROOT}"
echo "AWS Account:  ${AWS_ACCOUNT_ID}"
echo "AWS Region:   ${AWS_REGION}"
echo "ECR Repo:     ${ECR_REPO}"
echo "ECR Image:    ${ECR_IMAGE}:${TAG}"
echo "Dockerfile:   ${DOCKERFILE}"
echo "Platforms:    ${PLATFORMS}"
echo "Push latest:  ${PUSH_LATEST}"
echo

echo "Checking ECR repository exists..."
aws ecr describe-repositories \
  --repository-names "${ECR_REPO}" \
  --region "${AWS_REGION}" >/dev/null

echo "Logging in to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "Ensuring Docker buildx builder exists..."
if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
  docker buildx create --name "${BUILDER_NAME}" --use >/dev/null
else
  docker buildx use "${BUILDER_NAME}" >/dev/null
fi

docker buildx inspect --bootstrap >/dev/null

echo "Building and pushing multi-arch Docker image..."
docker buildx build \
  --platform "${PLATFORMS}" \
  -f "${DOCKERFILE}" \
  -t "${ECR_IMAGE}:${TAG}" \
  --push \
  .

if [[ "${TAG}" != "latest" && "${PUSH_LATEST}" == "true" ]]; then
  echo "Also tagging and pushing latest multi-arch image..."
  docker buildx build \
    --platform "${PLATFORMS}" \
    -f "${DOCKERFILE}" \
    -t "${ECR_IMAGE}:latest" \
    --push \
    .
fi

echo
echo "Verifying image in ECR..."
aws ecr describe-images \
  --repository-name "${ECR_REPO}" \
  --region "${AWS_REGION}" \
  --image-ids imageTag="${TAG}" \
  --query 'imageDetails[0].[imageTags,imagePushedAt,imageSizeInBytes,imageManifestMediaType]' \
  --output table

echo
echo "Done: ${ECR_IMAGE}:${TAG}"
