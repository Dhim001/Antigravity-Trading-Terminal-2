# Check Massive API key before starting the Massive terminal instance.
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\terminal-profiles.ps1"

Write-Host 'Massive preflight: checking MASSIVE_API_KEY ...' -ForegroundColor Cyan

$key = $env:MASSIVE_API_KEY
if (-not $key) {
    $rootEnv = Join-Path $script:TerminalRoot '.env'
    if (Test-Path $rootEnv) {
        Get-Content $rootEnv | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith('#') -and $line -match '^MASSIVE_API_KEY=(.+)$') {
                $key = $Matches[1].Trim()
            }
        }
    }
}

if ($key) {
    Write-Host 'OK - MASSIVE_API_KEY is set.' -ForegroundColor Green
    exit 0
}

Write-Host @"

FAIL - MASSIVE_API_KEY is not set.

Add your Massive (Polygon.io) API key to repo-root .env:
  MASSIVE_API_KEY=your_key_here

Get a key at: https://massive.com/dashboard/api-keys

Then re-run:  .\scripts\start-massive.ps1

"@ -ForegroundColor Red
exit 1
