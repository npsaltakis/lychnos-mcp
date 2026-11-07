# Shared helpers for the Windows scripts (dot-sourced).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PythonW = Join-Path $Root ".venv\Scripts\pythonw.exe"   # no console window
$TaskName = "SmartHomeControl"

function Get-BackendPort {
    $envFile = Join-Path $Backend ".env"
    if ($env:SHC_PORT) { return [int]$env:SHC_PORT }
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*SHC_PORT\s*=\s*(\d+)' } | Select-Object -First 1
        if ($line -and $line -match '(\d+)\s*$') { return [int]$Matches[1] }
    }
    return 3000
}

function Get-BackendProcess {
    $port = Get-BackendPort
    # Listening sockets have no remote end. (Filtering with -State Listen is unreliable on some
    # localized Windows installations, so it is not used.)
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Where-Object { $_.RemotePort -eq 0 -and $_.OwningProcess -ne 0 } | Select-Object -First 1
    if (-not $conn) { return $null }
    return Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
}

function Test-BackendHealth {
    $port = Get-BackendPort
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 2
        return $r.status -eq "ok"
    } catch { return $false }
}

function Assert-Venv {
    if (-not (Test-Path $Python)) {
        throw "Python environment not found at $Python. See README → Quick start."
    }
}
