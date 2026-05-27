#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <public-reachability|notification-delivery|device-status>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 - "$1" <<'PY'
import json
import sys

from overmind.main import initialize_runtime, run_scheduled_job

initialize_runtime(start_pollers=False)
print(json.dumps(run_scheduled_job(sys.argv[1]), sort_keys=True))
PY
