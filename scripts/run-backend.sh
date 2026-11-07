#!/usr/bin/env bash
# Runs the backend in the foreground (Ctrl+C to stop). Serves the dashboard at http://127.0.0.1:3000
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
exec "$root/.venv/bin/python" -m app
