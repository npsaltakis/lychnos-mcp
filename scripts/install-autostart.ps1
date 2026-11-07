# Starts the backend automatically when you log in to Windows (Task Scheduler, current user only,
# no admin rights, no window). Restarts it up to 3 times if it crashes.
. (Join-Path $PSScriptRoot "_common.ps1")
Assert-Venv

$exe = if (Test-Path $PythonW) { $PythonW } else { $Python }
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $exe -Argument "-m app" -WorkingDirectory $Backend
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Smart Home Control backend (http://127.0.0.1:$(Get-BackendPort))" -Force | Out-Null
Write-Host "Autostart installed: task '$TaskName' runs at every logon of $user."

$running = Get-BackendProcess
if ($running) {
    Write-Host "A backend is already running (PID $($running.Id)); it was left as is."
    Write-Host "Stop it (Ctrl+C in its window, or scripts\stop-backend.ps1) and run: Start-ScheduledTask $TaskName"
} else {
    Start-ScheduledTask -TaskName $TaskName
    for ($i = 0; $i -lt 40 -and -not (Test-BackendHealth); $i++) { Start-Sleep -Milliseconds 500 }
    if (Test-BackendHealth) {
        Write-Host "Backend started: http://127.0.0.1:$(Get-BackendPort)" -ForegroundColor Green
    } else {
        Write-Host "The task was started but the backend does not answer yet. Check scripts\status-backend.ps1" -ForegroundColor Yellow
    }
}
