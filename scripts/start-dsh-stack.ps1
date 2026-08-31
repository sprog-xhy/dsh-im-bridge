# Start the full dsh + dsh-im-bridge stack on Windows (idempotent).
#
#  1. ensures dsh web is listening on :10010 (starts a hidden node process if not)
#  2. waits until dsh web responds
#  3. ensures the bridge is listening on :8764 (starts a hidden process if not)
#
# Safe to run repeatedly: already-running services are left untouched, so this
# is also the entry point for the Windows logon scheduled task.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start-dsh-stack.ps1
param(
  [string]$Root = ""          # path to dsh-im-bridge (defaults to script's parent dir)
)
$ErrorActionPreference = "Continue"

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$LogsDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$dshPort  = 10010
$bridgePort = 8764
$dshUrl   = "http://127.0.0.1:$dshPort"

Write-Host "[stack] root = $Root"

function Test-Port($port) {
  return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Start-HiddenProcess($exe, $argList, $outLog, $errLog) {
  $outFile = Join-Path $LogsDir $outLog
  $errFile = Join-Path $LogsDir $errLog
  if ($errFile -eq $outFile) { $errFile = "$errFile.err" }
  $p = Start-Process -FilePath $exe -ArgumentList $argList -WindowStyle Hidden `
        -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
  return $p
}

# ---- 1. dsh web -------------------------------------------------------------
if (Test-Port $dshPort) {
  Write-Host "[stack] dsh web already listening on :$dshPort — skipping"
} else {
  $node = (Get-Command node -ErrorAction SilentlyContinue).Source
  if (-not $node) { $node = "D:\software\nodejs\node.exe" }

  # Locate the @deepseek-ai/dsh bin.js: npm global root, then a known fallback.
  $dshBin = ""
  $npmRoot = (& npm root -g 2>$null)
  if ($npmRoot) { $cand = Join-Path $npmRoot "@deepseek-ai\dsh\lib\bin.js"; if (Test-Path $cand) { $dshBin = $cand } }
  if (-not $dshBin) {
    $cand = "C:\Users\WPS\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\lib\bin.js"
    if (Test-Path $cand) { $dshBin = $cand }
  }
  if (-not $dshBin -or -not (Test-Path $dshBin)) {
    Write-Host "[stack] ERROR: cannot locate @deepseek-ai/dsh bin.js" -ForegroundColor Red
    exit 1
  }

  Write-Host "[stack] starting dsh web (node $dshBin web --port $dshPort) ..."
  $p = Start-HiddenProcess $node @("$dshBin", "web", "--port", "$dshPort") "dsh-web.out.log" "dsh-web.err.log"
  Write-Host "[stack] dsh web pid = $($p.Id)"

  # wait until dsh web accepts connections
  $ok = $false
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Port $dshPort) { $ok = $true; break }
    if ($p.HasExited) {
      Write-Host "[stack] ERROR: dsh web exited early (code $($p.ExitCode)). See logs\dsh-web.err.log" -ForegroundColor Red
      break
    }
  }
  if (-not $ok) {
    Write-Host "[stack] ERROR: dsh web did not open port $dshPort in time. See logs\dsh-web.err.log" -ForegroundColor Red
  } else {
    Write-Host "[stack] dsh web is up at $dshUrl"
  }
}

# ---- 2. bridge --------------------------------------------------------------
if (Test-Port $bridgePort) {
  Write-Host "[stack] bridge already listening on :$bridgePort — skipping"
} else {
  $python = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path $python)) {
    Write-Host "[stack] ERROR: no venv at $python — run:  python -m venv .venv && .venv\Scripts\python -m pip install -e ." -ForegroundColor Red
    exit 1
  }
  if (-not (Test-Path (Join-Path $Root "config.yaml"))) {
    Write-Host "[stack] ERROR: config.yaml missing at $Root" -ForegroundColor Red
    exit 1
  }
  Write-Host "[stack] starting bridge ..."
  $p = Start-HiddenProcess $python @("-m", "dsh_im_bridge", "--config", (Join-Path $Root "config.yaml"), "--log-file", (Join-Path $LogsDir "bridge.log")) "bridge.out.log" "bridge.err.log"
  Write-Host "[stack] bridge pid = $($p.Id)"
  Start-Sleep -Seconds 3
  if (Test-Port $bridgePort) {
    Write-Host "[stack] bridge is up on :$bridgePort"
  } else {
    Write-Host "[stack] WARNING: bridge not yet listening on :$bridgePort — check logs\bridge.log" -ForegroundColor Yellow
  }
}

Write-Host "[stack] done. dsh=$dshUrl  bridge=http://127.0.0.1:$bridgePort/status  logs=$LogsDir"
