# Removes the autostart task. The running backend (if any) keeps running; stop it with stop-backend.ps1.
. (Join-Path $PSScriptRoot "_common.ps1")

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Autostart removed."
} else {
    Write-Host "Autostart was not installed."
}
