# Stops the backend that listens on the configured port.
. (Join-Path $PSScriptRoot "_common.ps1")

$proc = Get-BackendProcess
if (-not $proc) {
    Write-Host "Backend is not running."
    exit 0
}
if ($proc.ProcessName -notmatch '^python') {
    Write-Host "Port $(Get-BackendPort) is used by '$($proc.ProcessName)' (PID $($proc.Id)), not by the backend. Not stopping it." -ForegroundColor Yellow
    exit 1
}
Stop-Process -Id $proc.Id
Write-Host "Smart Home backend stopped (PID $($proc.Id))."
