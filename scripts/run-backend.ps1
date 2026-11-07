# Runs the backend in the foreground (Ctrl+C to stop). Serves the dashboard at http://127.0.0.1:3000
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
& (Join-Path $root ".venv\Scripts\python.exe") -m app
