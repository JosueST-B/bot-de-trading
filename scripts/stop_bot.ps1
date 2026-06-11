param(
    [switch]$Dashboard,
    [switch]$Paper,
    [switch]$Live,
    [switch]$Watchdog,
    [switch]$All
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not ($Dashboard -or $Paper -or $Live -or $Watchdog -or $All)) {
    $All = $true
}

function Get-MatchingProcessIds {
    param([string]$Pattern)

    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -and
            $_.CommandLine -like "*$Pattern*"
        } |
        Select-Object -ExpandProperty ProcessId
}

function Stop-TrackedProcess {
    param(
        [string]$Name,
        [string]$PidPath
    )

    if (-not (Test-Path $PidPath)) {
        Write-Host "$Name is not running."
        return
    }

    $PidFromFile = Get-Content $PidPath -ErrorAction SilentlyContinue
    if (-not $PidFromFile) {
        Remove-Item $PidPath -Force -ErrorAction SilentlyContinue
        Write-Host "$Name PID file was empty and has been removed."
        return
    }

    $ProcessId = [int]$PidFromFile
    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $Process) {
        Remove-Item $PidPath -Force -ErrorAction SilentlyContinue
        Write-Host "$Name PID file was stale and has been removed."
        return
    }

    & taskkill /PID $ProcessId /T /F | Out-Null
    Start-Sleep -Milliseconds 500
    Remove-Item $PidPath -Force -ErrorAction SilentlyContinue
    Write-Host "$Name stopped. Root PID $ProcessId"
}

if ($All -or $Dashboard) {
    Stop-TrackedProcess "Dashboard" (Join-Path $Root "dashboard.pid")
}
if ($All -or $Paper) {
    Stop-TrackedProcess "Paper bot" (Join-Path $Root "paper.pid")
}
if ($All -or $Live) {
    Stop-TrackedProcess "Live bot" (Join-Path $Root "live.pid")
}
if ($All -or $Watchdog) {
    Stop-TrackedProcess "Watchdog" (Join-Path $Root "watchdog.pid")
}
