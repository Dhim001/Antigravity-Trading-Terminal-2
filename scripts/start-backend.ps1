param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sim', 'Ib', 'Massive', 'Alpaca')]
    [string]$Profile
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\terminal-profiles.ps1"

$key = $Profile.ToLower()
$ports = Get-ProfilePorts -ProfileKey $key

if (-not (Test-BackendPortFree -Port $ports.Ws)) {
    if (Test-BackendHealth -HttpPort $ports.Http) {
        Write-Host "Backend already running on WS :$($ports.Ws), HTTP :$($ports.Http)." -ForegroundColor Yellow
        exit 0
    }
    Write-Host "Port $($ports.Ws) (WS) in use but backend unhealthy - recycling..." -ForegroundColor Yellow
    Stop-ProfileBackend -ProfileKey $key
    if (-not (Test-BackendPortFree -Port $ports.Ws)) {
        Write-Host "Port $($ports.Ws) (WS) still in use after recycle. Stop the process manually or use -Recycle." -ForegroundColor Red
        exit 1
    }
}
if (-not (Test-BackendPortFree -Port $ports.Http)) {
    if (Test-BackendHealth -HttpPort $ports.Http) {
        Write-Host "Backend already running on HTTP :$($ports.Http)." -ForegroundColor Yellow
        exit 0
    }
    Write-Host "Port $($ports.Http) (HTTP) in use but backend unhealthy - recycling..." -ForegroundColor Yellow
    Stop-ProfileBackend -ProfileKey $key
    if (-not (Test-BackendPortFree -Port $ports.Http)) {
        Write-Host "Port $($ports.Http) (HTTP) still in use after recycle." -ForegroundColor Red
        exit 1
    }
}

$env:TERMINAL_PROFILE = $key
Write-Host "Starting $($ports.Label) backend (WS :$($ports.Ws), HTTP :$($ports.Http)) ..." -ForegroundColor Cyan
Write-Host "Profile: env.profiles\$key.env (overrides repo-root .env for this process)" -ForegroundColor DarkGray

# Heavy-job sidecar is spawned by the API lifespan when BACKTEST_HEAVY_SIDECAR=1 (default).
# To run it manually alongside the API instead, unset spawn and start:
#   $env:BACKTEST_HEAVY_SIDECAR = '0'
#   Start-Process $python -ArgumentList '-m','app.services.bots.heavy_job_worker' -WorkingDirectory (Join-Path $script:TerminalRoot 'backend')
if (-not $env:BACKTEST_HEAVY_SIDECAR) {
    $env:BACKTEST_HEAVY_SIDECAR = '1'
}

Push-Location (Join-Path $script:TerminalRoot 'backend')
try {
    $python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        Write-Host "Warning: .venv not found - using PATH python." -ForegroundColor Yellow
        $python = 'python'
    }
    & $python main.py
    $exitCode = $LASTEXITCODE
    if ($exitCode -and $exitCode -ne 0) {
        Write-Host "Backend exited with code $exitCode" -ForegroundColor Red
    }
} finally {
    Pop-Location
}
