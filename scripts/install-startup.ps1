# Install / remove dsh + dsh-im-bridge logon auto-start via the user Startup
# folder (no admin required — scheduled tasks need elevation on this machine).
#
# On logon a hidden VBS launcher runs scripts\start-dsh-stack.ps1, which starts
# dsh web (:10010) if needed, waits for it, then starts the bridge (:8764).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install-startup.ps1            # install
#   powershell -ExecutionPolicy Bypass -File scripts\install-startup.ps1 -Unregister # remove
param(
  [string]$Root = "",          # path to dsh-im-bridge (defaults to script's parent dir)
  [switch]$Unregister          # remove the auto-start entry instead
)
$ErrorActionPreference = "Stop"

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$StartupDir = [Environment]::GetFolderPath('Startup')
$EntryName = "dsh-im-bridge-login.vbs"
$EntryPath = Join-Path $StartupDir $EntryName

if ($Unregister) {
  if (Test-Path $EntryPath) { Remove-Item $EntryPath -Force }
  Write-Host "[startup] removed '$EntryPath'"
  exit 0
}

if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
  Write-Host "[startup] creating venv ..."
  python -m venv "$Root\.venv"
  & "$Root\.venv\Scripts\python.exe" -m pip install -e $Root | Out-Null
}
if (-not (Test-Path "$Root\config.yaml")) {
  Copy-Item "$Root\config.example.yaml" "$Root\config.yaml"
  Write-Host "[startup] created config.yaml from example — edit it, then re-run."
}

$Ps = Join-Path $Root "scripts\start-dsh-stack.ps1"
if (-not (Test-Path $Ps)) {
  Write-Host "[startup] ERROR: $Ps not found" -ForegroundColor Red
  exit 1
}

# VBS launcher: run the stack script in a hidden window, without flashing a
# console on logon. VBS strings escape a double quote by doubling it ("").
$PsEsc = $Ps.Replace('"', '""')
$RootEsc = $Root.Replace('"', '""')
$vbs = @"
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$PsEsc"" -Root ""$RootEsc""", 0, False
"@
Set-Content -Path $EntryPath -Value $vbs -Encoding ASCII
Write-Host "[startup] installed auto-start entry: $EntryPath"
Write-Host "[startup] will start dsh web (:10010) + bridge (:8764) on next logon"
Write-Host "  Test now:     powershell -ExecutionPolicy Bypass -File `"$Ps`""
Write-Host "  Remove:       re-run with -Unregister"
