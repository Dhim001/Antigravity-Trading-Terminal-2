# Check IB Gateway/TWS API port before starting the IB terminal instance.
param(
    [string]$HostName = '127.0.0.1',
    [int]$Port = 4002
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\terminal-profiles.ps1"

Write-Host "IB preflight: ${HostName}:${Port} ..." -ForegroundColor Cyan

if (Test-TcpPort -HostName $HostName -Port $Port) {
    Write-Host 'OK - Gateway API port is reachable.' -ForegroundColor Green
    exit 0
}

Write-Host @"

FAIL - cannot reach IB Gateway on ${HostName}:${Port}.

Start IB Gateway (paper default port 4002, live 4001) and enable API connections:
  Configuration -> API -> Settings -> Enable ActiveX and Socket Clients

Then re-run:  .\scripts\start-ib.ps1

"@ -ForegroundColor Red
exit 1
