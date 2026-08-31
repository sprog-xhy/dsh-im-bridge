# Install dsh-im-bridge as a Windows scheduled task (starts on user logon).
# Starts BOTH dsh web (:10010) and the bridge (:8764) via start-dsh-stack.ps1.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install-windows-task.ps1
param(
  [string]$Root = "",          # path to dsh-im-bridge (defaults to script's parent dir)
  [switch]$Unregister          # remove the scheduled task instead
)
$ErrorActionPreference = "Stop"

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$TaskName = "dsh-im-bridge"

if ($Unregister) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "[install] removed scheduled task '$TaskName'"
  exit 0
}

if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
  Write-Host "[install] creating venv ..."
  python -m venv "$Root\.venv"
  & "$Root\.venv\Scripts\python.exe" -m pip install -e $Root | Out-Null
}

if (-not (Test-Path "$Root\config.yaml")) {
  Copy-Item "$Root\config.example.yaml" "$Root\config.yaml"
  Write-Host "[install] created config.yaml from example — edit it, then re-run."
}

$StackScript = "$Root\scripts\start-dsh-stack.ps1"
$LogsDir = "$Root\logs"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StackScript`" -Root `"$Root`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
  -Settings $Settings -Description "dsh-im-bridge: starts dsh web (:10010) + DeepSeek Harness <-> IM bridge (:8764) at logon" -Force | Out-Null

Write-Host "[install] scheduled task '$TaskName' registered (runs dsh web + bridge at logon, logs -> $LogsDir)."
Write-Host "  Start now:     Start-ScheduledTask -TaskName $TaskName"
Write-Host "  View logs:     Get-Content $LogsDir\bridge.log -Tail 50"
Write-Host "  Remove:        re-run with -Unregister"
