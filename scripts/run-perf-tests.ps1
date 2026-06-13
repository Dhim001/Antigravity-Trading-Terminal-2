# Medium performance & stress test runner (Windows)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "=== Backend perf/stress (WebSocket + HTTP) ===" -ForegroundColor Cyan
Push-Location "$Root\backend"
python scripts/perf_stress_test.py @args
$backendExit = $LASTEXITCODE
Pop-Location

Write-Host "`n=== Frontend perf (Playwright) ===" -ForegroundColor Cyan
Push-Location "$Root\frontend"

$previewJob = $null
$baseUrl = $env:E2E_BASE_URL
if (-not $baseUrl) {
  Write-Host "Starting vite preview on :4173 ..."
  $previewJob = Start-Job { Set-Location $using:Root\frontend; npm run preview -- --host 127.0.0.1 --port 4173 2>&1 }
  Start-Sleep -Seconds 3
  $env:E2E_BASE_URL = "http://127.0.0.1:4173"
}

npm run test:e2e -- e2e/performance.spec.js
$frontendExit = $LASTEXITCODE

if ($previewJob) {
  Stop-Job $previewJob -ErrorAction SilentlyContinue
  Remove-Job $previewJob -Force -ErrorAction SilentlyContinue
}

Pop-Location

if ($backendExit -ne 0 -or $frontendExit -ne 0) {
  Write-Host "`nPerf suite: FAIL" -ForegroundColor Red
  exit 1
}
Write-Host "`nPerf suite: PASS" -ForegroundColor Green
exit 0
