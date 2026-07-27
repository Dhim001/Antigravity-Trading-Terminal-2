# Start Alpaca live-data terminal (backend + frontend in separate windows).
# Alpaca:  WS 8795, HTTP 8796, UI http://127.0.0.1:5176
# Assets:  equities (SIP/IEX) + crypto (v1beta3) + options (indicative/OPRA)
#
# Usage:
#   .\scripts\start-alpaca.ps1           # start backend if down; keep running backend if healthy
#   .\scripts\start-alpaca.ps1 -Recycle  # force restart backend (code changes)
#   .\scripts\start-alpaca.ps1 -Restart  # force restart backend + frontend

param(
    [switch]$Restart,
    [switch]$Recycle
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. "$here\terminal-profiles.ps1"

$ports = Get-ProfilePorts -ProfileKey 'alpaca'
$dev = [int]$ports.Dev
$ws = [int]$ports.Ws
$http = [int]$ports.Http

& (Join-Path $here 'preflight-alpaca.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$backendHealthy = Test-BackendHealth -HttpPort $http
$needsBackendStart = $true

if ($Restart -or $Recycle) {
    Stop-TerminalProfileListeners -ProfileKey 'alpaca'
    $backendHealthy = $false
} elseif ($backendHealthy) {
    Write-Host "Alpaca backend already healthy on :$http - skipping restart." -ForegroundColor DarkGray
    $needsBackendStart = $false
} elseif (Test-TcpPort -HostName '127.0.0.1' -Port $ws) {
    Write-Host "Recycling unhealthy Alpaca backend (WS :$ws, HTTP :$http)..." -ForegroundColor DarkYellow
    Stop-ProfileBackend -ProfileKey 'alpaca'
    $backendHealthy = $false
} else {
    Write-Host "Starting Alpaca backend (WS :$ws, HTTP :$http)..." -ForegroundColor DarkGray
}

Write-Host @"
=== Alpaca terminal (live data; OMS via ALPACA_OMS_ENABLED) ===
  UI:      http://127.0.0.1:$dev
  Backend: ws://127.0.0.1:$ws  http://127.0.0.1:$http
  DB:      backend/trading-alpaca.db
  Assets:  equities + crypto + options
  Keys:    repo-root .env ALPACA_API_KEY / ALPACA_SECRET_KEY
  OMS:     alpaca.env default false = Sim OMS; true = Alpaca broker

Opening backend and frontend windows...
"@ -ForegroundColor Green

if ($needsBackendStart) {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-backend.ps1'), '-Profile', 'Alpaca'
    )
    Start-Sleep -Seconds 2
}

if ($Restart) {
    if (Test-DevPortInUse -Port $dev) {
        Write-Host "Alpaca UI already on http://127.0.0.1:$dev - skipping frontend window." -ForegroundColor Yellow
    } else {
        Start-Process powershell -ArgumentList @(
            '-NoExit', '-ExecutionPolicy', 'Bypass',
            '-File', (Join-Path $here 'start-frontend.ps1'), '-Profile', 'Alpaca'
        )
    }
} elseif (Test-DevPortInUse -Port $dev) {
    Write-Host "Alpaca UI already on http://127.0.0.1:$dev - skipping frontend window." -ForegroundColor Yellow
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-frontend.ps1'), '-Profile', 'Alpaca'
    )
}

Write-Host 'Done. Sim (:5173), IB (:5174), and Massive (:5175) can keep running in parallel.' -ForegroundColor DarkGray
Write-Host 'Tip: use -Recycle to restart backend after code changes; -Restart also recycles the UI.' -ForegroundColor DarkGray
Write-Host 'Note: only one process may hold the Alpaca SIP equity stream per account.' -ForegroundColor DarkGray
