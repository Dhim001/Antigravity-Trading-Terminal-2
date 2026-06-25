# Start simulated terminal (backend + frontend in separate windows).
# Sim:  WS 8765, HTTP 8766, UI http://127.0.0.1:5173
#
# Usage:
#   .\scripts\start-sim.ps1           # skip if ports already in use
#   .\scripts\start-sim.ps1 -Restart  # stop existing listeners, then start fresh

param(
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. "$here\terminal-profiles.ps1"

$simPorts = Get-ProfilePorts -ProfileKey 'sim'
$simDev = [int]$simPorts.Dev
$simWs = [int]$simPorts.Ws
$simHttp = [int]$simPorts.Http

if ($Restart) {
    Stop-TerminalProfileListeners -ProfileKey 'sim'
}

Write-Host @"
=== Simulated terminal ===
  UI:      http://127.0.0.1:$simDev
  Backend: ws://127.0.0.1:$simWs  http://127.0.0.1:$simHttp
  DB:      backend/trading-sim.db

Opening backend and frontend windows...
"@ -ForegroundColor Green

$backendBusy = (-not (Test-BackendPortFree -Port $simWs)) -or (-not (Test-BackendPortFree -Port $simHttp))
if ($backendBusy) {
    Write-Host "Sim backend ports $simWs/$simHttp already in use - skipping backend window." -ForegroundColor Yellow
    Write-Host "Health: http://127.0.0.1:$simHttp/health" -ForegroundColor DarkGray
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-backend.ps1'), '-Profile', 'Sim'
    )
    Start-Sleep -Seconds 2
}

if (Test-DevPortInUse -Port $simDev) {
    Write-Host "Sim UI already on http://127.0.0.1:$simDev - skipping frontend window." -ForegroundColor Yellow
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-frontend.ps1'), '-Profile', 'Sim'
    )
}

Write-Host 'Done. Keep both windows open while using the sim terminal.' -ForegroundColor DarkGray
