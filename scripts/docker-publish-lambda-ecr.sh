#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_TAG="$(cat "${REPO_ROOT}/VERSION" 2>/dev/null || echo lambda-latest)"

DOCKERFILE="${DOCKERFILE:-Dockerfile.lambda}" \
TAG="${TAG:-$DEFAULT_TAG}" \
PLATFORMS="${PLATFORMS:-linux/amd64}" \
PUSH_LATEST="${PUSH_LATEST:-false}" \
PROVENANCE="${PROVENANCE:-false}" \
"${SCRIPT_DIR}/docker-publish-ecr.sh"
