# Launch Trading Terminal in a native desktop window (Electron).
# Starts backend + Vite dev server if needed, then opens the UI without browser chrome.
#
# Usage:
#   .\scripts\start-desktop.ps1                    # Sim profile (default)
#   .\scripts\start-desktop.ps1 -Profile Massive
#   .\scripts\start-desktop.ps1 -Profile Ib -Recycle

param(
    [ValidateSet('Sim', 'Ib', 'Massive')]
    [string]$Profile = 'Sim',
    [switch]$Restart,
    [switch]$Recycle
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. "$here\terminal-profiles.ps1"

$key = $Profile.ToLower()
$ports = Get-ProfilePorts -ProfileKey $key
$dev = [int]$ports.Dev
$http = [int]$ports.Http
$ws = [int]$ports.Ws
$uiUrl = "http://127.0.0.1:$dev"

if ($key -eq 'ib') {
    & (Join-Path $here 'preflight-ib.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if ($key -eq 'massive') {
    & (Join-Path $here 'preflight-massive.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Restart -or $Recycle) {
    Stop-TerminalProfileListeners -ProfileKey $key
}

function Wait-DevServer {
    param([string]$Url, [int]$TimeoutSec = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -lt 500) { return $true }
        } catch {
            # not ready yet
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-FrontendDevServer {
    Write-Host "Starting $($ports.Label) Vite dev server on $uiUrl ..." -ForegroundColor Cyan
    # Reuse start-frontend.ps1 (hidden npm.cmd launches are unreliable on Windows).
    Start-Process powershell -ArgumentList @(
        '-WindowStyle', 'Minimized',
        '-NoExit',
        '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-frontend.ps1'),
        '-Profile', $Profile
    )
}

function Ensure-DesktopDeps {
    $desktopDir = Join-Path $script:TerminalRoot 'desktop'
    $nodeModules = Join-Path $desktopDir 'node_modules'
    if (-not (Test-Path $nodeModules)) {
        Write-Host 'Installing desktop shell (Electron) - one-time setup...' -ForegroundColor Cyan
        Push-Location $desktopDir
        try {
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed in desktop/" }
        } finally {
            Pop-Location
        }
    }
}

# --- Backend ---
$backendHealthy = Test-BackendHealth -HttpPort $http
$needsBackendStart = -not $backendHealthy

if ($needsBackendStart) {
    if (Test-TcpPort -HostName '127.0.0.1' -Port $ws) {
        Write-Host "Recycling unhealthy $($ports.Label) backend..." -ForegroundColor DarkYellow
        Stop-ProfileBackend -ProfileKey $key
    }
    Write-Host "Starting $($ports.Label) backend (WS :$ws, HTTP :$http)..." -ForegroundColor DarkGray
    Start-Process powershell -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'start-backend.ps1'), '-Profile', $Profile
    )
    Start-Sleep -Seconds 2
    for ($i = 0; $i -lt 40; $i++) {
        if (Test-BackendHealth -HttpPort $http) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-BackendHealth -HttpPort $http)) {
        Write-Host "Backend did not become healthy on :$http - check the backend window." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "$($ports.Label) backend already healthy on :$http" -ForegroundColor DarkGray
}

# --- Vite dev server ---
if (-not (Test-DevPortInUse -Port $dev)) {
    Start-FrontendDevServer
    Start-Sleep -Seconds 3
} else {
    Write-Host "$($ports.Label) UI dev server already on $uiUrl" -ForegroundColor DarkGray
}

if (-not (Wait-DevServer -Url $uiUrl)) {
    Write-Host "Vite dev server did not respond at $uiUrl in time." -ForegroundColor Red
    Write-Host "Check the minimized PowerShell window running Vite, or run:" -ForegroundColor Yellow
    Write-Host "  .\scripts\start-frontend.ps1 -Profile $Profile" -ForegroundColor Yellow
    exit 1
}

# --- Electron window ---
Ensure-DesktopDeps

$desktopDir = Join-Path $script:TerminalRoot 'desktop'
$env:TERMINAL_PROFILE = $key

Write-Host ''
Write-Host "=== $($ports.Label) desktop window ===" -ForegroundColor Green
Write-Host "  UI:      $uiUrl" -ForegroundColor Green
Write-Host "  Backend: ws://127.0.0.1:$ws  http://127.0.0.1:$http" -ForegroundColor Green
Write-Host ''
Write-Host 'Opening desktop shell (close the window to exit; backend + Vite keep running).' -ForegroundColor Green

Push-Location $desktopDir
try {
    $electronCli = Join-Path $desktopDir 'node_modules\electron\cli.js'
    if (-not (Test-Path $electronCli)) {
        throw "Electron not installed. Run: cd desktop; npm install"
    }
    & node $electronCli $desktopDir "--profile=$key"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
