# Start IB feed terminal (backend + frontend in separate windows).
# IB:  WS 8775, HTTP 8776, UI http://127.0.0.1:5174
#
# Usage:
#   .\scripts\start-ib.ps1           # skip if ports already in use
#   .\scripts\start-ib.ps1 -Restart  # stop existing listeners, then start fresh

param(
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. "$here\terminal-profiles.ps1"

$ibPorts = Get-ProfilePorts -ProfileKey 'ib'
$ibDev = [int]$ibPorts.Dev
$ibWs = [int]$ibPorts.Ws
$ibHttp = [int]$ibPorts.Http

& (Join-Path $here 'preflight-ib.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Restart) {
    Stop-TerminalProfileListeners -ProfileKey 'ib'
}

Write-Host @"
=== IB terminal (feed-only) ===
  UI:      http://127.0.0.1:$ibDev
  Backend: ws://127.0.0.1:$ibWs  http://127.0.0.1:$ibHttp
  DB:      backend/trading-ib.db
  Gateway: 127.0.0.1:4002 (edit env.profiles/ib.env to change)

Opening backend and frontend windows...
"@ -ForegroundColor Green

$backendBusy = (-not (Test-BackendPortFree -Port $ibWs)) -or (-not (Test-BackendPortFree -Port $ibHttp))
if ($backendBusy) {
    Write-Host "IB backend ports $ibWs/$ibHttp already in use - skipping backend window." -ForegroundColor Yellow
    Write-Host "Health: http://127.0.0.1:$ibHttp/health" -ForegroundColor DarkGray
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-backend.ps1'), '-Profile', 'Ib'
    )
    Start-Sleep -Seconds 2
}

if (Test-DevPortInUse -Port $ibDev) {
    Write-Host "IB UI already on http://127.0.0.1:$ibDev - skipping frontend window." -ForegroundColor Yellow
} else {
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-frontend.ps1'), '-Profile', 'Ib'
    )
}

Write-Host 'Done. Sim terminal can keep running on :5173 in parallel.' -ForegroundColor DarkGray
