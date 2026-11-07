# Starts the backend in the background (no window). Dashboard: http://127.0.0.1:<port>
. (Join-Path $PSScriptRoot "_common.ps1")
Assert-Venv

$existing = Get-BackendProcess
if ($existing) {
    Write-Host "Already running (PID $($existing.Id)) on port $(Get-BackendPort)."
    exit 0
}

$exe = if (Test-Path $PythonW) { $PythonW } else { $Python }
$proc = Start-Process -FilePath $exe -ArgumentList "-m", "app" -WorkingDirectory $Backend -WindowStyle Hidden -PassThru

# Note: the venv python.exe is a launcher that starts the real interpreter as a child process,
# so the PID that owns the port is looked up instead of using $proc.Id.
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    $listener = Get-BackendProcess
    if ($listener -and (Test-BackendHealth)) {
        Write-Host "Smart Home backend started (PID $($listener.Id)): http://127.0.0.1:$(Get-BackendPort)"
        exit 0
    }
    if ($proc.HasExited -and -not $listener) { break }
}
Write-Host "Backend did not start. Check the log: $HOME\.smarthome-control\logs\backend.log" -ForegroundColor Red
exit 1
