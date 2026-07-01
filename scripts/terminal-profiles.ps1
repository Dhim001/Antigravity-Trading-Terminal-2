# Shared helpers for dual-instance launchers (dot-source from sibling scripts).

$ErrorActionPreference = 'Stop'
$script:TerminalRoot = Split-Path -Parent $PSScriptRoot

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 1500)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ok -and $client.Connected) {
            $client.EndConnect($iar)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Test-BackendPortFree {
    param([int]$Port)
    -not (Test-TcpPort -HostName '127.0.0.1' -Port $Port)
}

function Import-FrontendProfileEnv {
    param([ValidateSet('sim', 'ib', 'massive')][string]$ProfileKey)
    $path = Join-Path $script:TerminalRoot "frontend\env.profiles\$ProfileKey.env"
    if (-not (Test-Path $path)) {
        throw "Missing frontend profile: $path"
    }
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line -match '^([^=]+)=(.*)$') {
            Set-Item -Path "Env:$($Matches[1].Trim())" -Value $Matches[2].Trim()
        }
    }
}

function Test-DevPortInUse {
    param([int]$Port)
    # Prefer Listen state (reliable on Windows); fall back to TCP connect probe.
    $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $listen) {
        return $true
    }
    return Test-TcpPort -HostName '127.0.0.1' -Port $Port
}

function Write-DevPortBusyMessage {
    param([int]$Port, [string]$Label)
    Write-Host @"

$Label frontend port $Port is already in use.

The UI is probably already running - open:  http://127.0.0.1:$Port

To start a fresh dev server, stop the other Vite process first, then re-run this script.
  Find process:  Get-NetTCPConnection -LocalPort $Port | Select OwningProcess
  Stop it:       Stop-Process -Id <pid>

"@ -ForegroundColor Yellow
}

function Get-ProfilePorts {
    param([ValidateSet('sim', 'ib', 'massive')][string]$ProfileKey)
    if ($ProfileKey -eq 'sim') {
        return @{ Http = 8766; Ws = 8765; Dev = 5173; Label = 'Simulated' }
    }
    if ($ProfileKey -eq 'ib') {
        return @{ Http = 8776; Ws = 8775; Dev = 5174; Label = 'IB (LIVE_IB)' }
    }
    return @{ Http = 8786; Ws = 8785; Dev = 5175; Label = 'Massive (LIVE_MASSIVE)' }
}

function Stop-ListenerOnPort {
    param([int]$Port)
    $pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -gt 0 })
    foreach ($procId in $pids) {
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "  Stopping $($proc.ProcessName) (PID $procId) on port $Port" -ForegroundColor DarkYellow
                # Prefer graceful close so the backend can flush checkpoints (avoid safe-mode churn).
                Stop-Process -Id $procId -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 800
                if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
                    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {
            # best-effort
        }
    }
    # Wait until the port is actually released (Windows can hold TIME_WAIT briefly).
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-BackendPortFree -Port $Port) { return }
        Start-Sleep -Milliseconds 250
    }
}

function Test-BackendHealth {
    param(
        [int]$HttpPort,
        [int]$TimeoutSec = 3
    )
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$HttpPort/health" -UseBasicParsing -TimeoutSec $TimeoutSec
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Stop-ProfileBackend {
    param(
        [ValidateSet('sim', 'ib', 'massive')][string]$ProfileKey,
        [int]$GraceSec = 2
    )
    $ports = Get-ProfilePorts -ProfileKey $ProfileKey
    $http = [int]$ports.Http
    $ws = [int]$ports.Ws
    if (Test-BackendHealth -HttpPort $http) {
        Write-Host "  Requesting graceful backend shutdown on :$http..." -ForegroundColor DarkGray
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$http/api/v1/admin/shutdown" -Method POST -UseBasicParsing -TimeoutSec 5 | Out-Null
        } catch {
            # Process may exit before the response completes — that is fine.
        }
        for ($i = 0; $i -lt ($GraceSec * 4); $i++) {
            if (Test-BackendPortFree -Port $ws -and (Test-BackendPortFree -Port $http)) { return }
            Start-Sleep -Milliseconds 250
        }
    }
    Stop-ListenerOnPort -Port $ws
    Stop-ListenerOnPort -Port $http
}

function Stop-TerminalProfileListeners {
    param([ValidateSet('sim', 'ib', 'massive')][string]$ProfileKey)
    $ports = Get-ProfilePorts -ProfileKey $ProfileKey
    Write-Host "Stopping $($ports.Label) listeners (WS :$($ports.Ws), HTTP :$($ports.Http), UI :$($ports.Dev))..." -ForegroundColor Yellow
    Stop-ProfileBackend -ProfileKey $ProfileKey
    Stop-ListenerOnPort -Port ([int]$ports.Dev)
    Start-Sleep -Seconds 1
}
