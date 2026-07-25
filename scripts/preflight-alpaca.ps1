# Check Alpaca API credentials before starting the Alpaca terminal instance.
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\terminal-profiles.ps1"

Write-Host 'Alpaca preflight: checking ALPACA_API_KEY / ALPACA_SECRET_KEY ...' -ForegroundColor Cyan

$key = $env:ALPACA_API_KEY
$secret = $env:ALPACA_SECRET_KEY
$rootEnv = Join-Path $script:TerminalRoot '.env'
if (Test-Path $rootEnv) {
    Get-Content $rootEnv | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line -match '^ALPACA_API_KEY=(.+)$') {
            if (-not $key) { $key = $Matches[1].Trim() }
        }
        if ($line -and -not $line.StartsWith('#') -and $line -match '^ALPACA_SECRET_KEY=(.+)$') {
            if (-not $secret) { $secret = $Matches[1].Trim() }
        }
    }
}

if ($key -and $secret) {
    Write-Host 'OK - Alpaca API credentials are set.' -ForegroundColor Green
    exit 0
}

Write-Host @"

FAIL - Alpaca API credentials are incomplete.

Add both keys to repo-root .env:
  ALPACA_API_KEY=your_key_here
  ALPACA_SECRET_KEY=your_secret_here
  ALPACA_BASE_URL=https://paper-api.alpaca.markets

Get keys at: https://app.alpaca.markets/paper/dashboard/overview

Then re-run:  .\scripts\start-alpaca.ps1

"@ -ForegroundColor Red
exit 1
