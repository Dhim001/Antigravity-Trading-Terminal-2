param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sim', 'Ib', 'Massive')]
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
    Write-Host "Port $($ports.Ws) (WS) is already in use - is another backend running?" -ForegroundColor Red
    exit 1
}
if (-not (Test-BackendPortFree -Port $ports.Http)) {
    if (Test-BackendHealth -HttpPort $ports.Http) {
        Write-Host "Backend already running on HTTP :$($ports.Http)." -ForegroundColor Yellow
        exit 0
    }
    Write-Host "Port $($ports.Http) (HTTP) is already in use - is another backend running?" -ForegroundColor Red
    exit 1
}

$env:TERMINAL_PROFILE = $key
Write-Host "Starting $($ports.Label) backend (WS :$($ports.Ws), HTTP :$($ports.Http)) ..." -ForegroundColor Cyan
Write-Host "Profile: env.profiles\$key.env (overrides repo-root .env for this process)" -ForegroundColor DarkGray

Push-Location (Join-Path $script:TerminalRoot 'backend')
try {
    python main.py
} finally {
    Pop-Location
}
