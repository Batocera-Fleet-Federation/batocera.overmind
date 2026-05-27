#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DOCKERFILE="${DOCKERFILE:-Dockerfile.lambda}" \
TAG="${TAG:-lambda-latest}" \
"${SCRIPT_DIR}/docker-publish-ecr.sh"
