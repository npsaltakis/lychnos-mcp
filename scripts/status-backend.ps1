# Shows whether the backend and the autostart task are active.
. (Join-Path $PSScriptRoot "_common.ps1")

$port = Get-BackendPort
$proc = Get-BackendProcess
if ($proc -and (Test-BackendHealth)) {
    Write-Host "Backend:   running (PID $($proc.Id)) -> http://127.0.0.1:$port" -ForegroundColor Green
} elseif ($proc) {
    Write-Host "Backend:   port $port is used by $($proc.ProcessName) (PID $($proc.Id)) but /api/health does not answer" -ForegroundColor Yellow
} else {
    Write-Host "Backend:   not running" -ForegroundColor Yellow
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Autostart: installed (state: $($task.State))"
} else {
    Write-Host "Autostart: not installed (run scripts\install-autostart.ps1)"
}
Write-Host "Log:       $HOME\.smarthome-control\logs\backend.log"
