# Run the dsh-im-bridge on Windows.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_bridge.ps1 [-Config config.yaml] [-Verbose]
param(
  [string]$Config = "",
  [switch]$Verbose
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "[run_bridge] creating venv ..."
  python -m venv .venv
  & ".venv\Scripts\python.exe" -m pip install -e . | Out-Null
}

$args = @()
if ($Config) { $args += "--config", $Config }
if ($Verbose) { $args += "--verbose" }

& ".venv\Scripts\python.exe" -m dsh_im_bridge @args
