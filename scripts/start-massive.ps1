# Start Massive live-data terminal (backend + frontend in separate windows).
# Massive:  WS 8785, HTTP 8786, UI http://127.0.0.1:5175
#
# Usage:
#   .\scripts\start-massive.ps1           # restart backend; keep UI if already running
#   .\scripts\start-massive.ps1 -Restart  # restart backend + frontend

param(
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. "$here\terminal-profiles.ps1"

$ports = Get-ProfilePorts -ProfileKey 'massive'
$dev = [int]$ports.Dev
$ws = [int]$ports.Ws
$http = [int]$ports.Http

& (Join-Path $here 'preflight-massive.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Restart) {
    Stop-TerminalProfileListeners -ProfileKey 'massive'
} else {
    # Always recycle backend so code changes and clean shutdown apply (WS + HTTP only).
    Write-Host "Recycling Massive backend (WS :$ws, HTTP :$http)..." -ForegroundColor DarkGray
    Stop-ListenerOnPort -Port $ws
    Stop-ListenerOnPort -Port $http
    Start-Sleep -Seconds 1
}

Write-Host @"
=== Massive terminal (feed-only) ===
  UI:      http://127.0.0.1:$dev
  Backend: ws://127.0.0.1:$ws  http://127.0.0.1:$http
  DB:      backend/trading-massive.db
  API key: repo-root .env MASSIVE_API_KEY

Opening backend and frontend windows...
"@ -ForegroundColor Green

Start-Process powershell -ArgumentList @(
    '-NoExit', '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $here 'start-backend.ps1'), '-Profile', 'Massive'
)
Start-Sleep -Seconds 2

if ($Restart) {
    if (Test-DevPortInUse -Port $dev) {
        Write-Host "Massive UI already on http://127.0.0.1:$dev - skipping frontend window." -ForegroundColor Yellow
    } else {
        Start-Process powershell -ArgumentList @(
            '-NoExit', '-ExecutionPolicy', 'Bypass',
            '-File', (Join-Path $here 'start-frontend.ps1'), '-Profile', 'Massive'
        )
    }
} elseif (Test-DevPortInUse -Port $dev) {
    Write-Host "Massive UI already on http://127.0.0.1:$dev - skipping frontend window." -ForegroundColor Yellow
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-frontend.ps1'), '-Profile', 'Massive'
    )
}

Write-Host 'Done. Sim (:5173) and IB (:5174) can keep running in parallel.' -ForegroundColor DarkGray
Write-Host 'Tip: use -Restart to also recycle the Vite UI on :5175.' -ForegroundColor DarkGray
