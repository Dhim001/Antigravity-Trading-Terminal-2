param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Sim', 'Ib', 'Massive')]
    [string]$Profile
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\terminal-profiles.ps1"

$key = $Profile.ToLower()
$ports = Get-ProfilePorts -ProfileKey $key
$devPort = [int]$ports.Dev

if (Test-DevPortInUse -Port $devPort) {
    Write-DevPortBusyMessage -Port $devPort -Label $ports.Label
    exit 0
}

Import-FrontendProfileEnv -ProfileKey $key

Write-Host "Starting $($ports.Label) frontend (http://127.0.0.1:$($ports.Dev) -> backend :$($ports.Http)) ..." -ForegroundColor Cyan

Push-Location (Join-Path $script:TerminalRoot 'frontend')
try {
    npm run dev
} finally {
    Pop-Location
}
