#!/bin/bash
set -euo pipefail

echo "Starting Batocera Overmind"

if ! python3 -c "import fastapi, uvicorn, jose, passlib, pydantic, email_validator" >/dev/null 2>&1; then
  python3 -m pip install --user -r requirements.txt
fi

export USE_FAKE_DATA="${USE_FAKE_DATA:-false}"
python3 app/main.py
