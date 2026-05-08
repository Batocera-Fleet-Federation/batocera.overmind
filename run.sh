#!/bin/bash
set -euo pipefail

# Batocera Overmind startup script

echo "🎮 Batocera Overmind"
echo "Checking dependencies..."

if ! python3 -c "import fastapi, uvicorn, jose, passlib, pydantic, email_validator" >/dev/null 2>&1; then
    echo "Installing dependencies to user site-packages..."
    python3 -m pip install --user -r requirements.txt
fi

# Start the server
echo ""
echo "🚀 Starting API server..."
echo "📖 API Documentation: http://localhost:8000/docs"
echo "🏠 UI: http://localhost:8000/"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python3 app/main.py
