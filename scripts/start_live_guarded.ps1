param(
    [int]$Cycles = 0,
    [int]$SleepSeconds = 300
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"
$PidFile = Join-Path $Root "live.pid"
$OutFile = Join-Path $Root "live.out.log"
$ErrFile = Join-Path $Root "live.err.log"

Set-Location $Root
$PathValue = [System.Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $PathValue) {
    $PathValue = [System.Environment]::GetEnvironmentVariable("PATH", "Process")
}
[System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
if ($PathValue) {
    [System.Environment]::SetEnvironmentVariable("Path", $PathValue, "Process")
}
[System.Environment]::SetEnvironmentVariable("HTTP_PROXY", $null, "Process")
[System.Environment]::SetEnvironmentVariable("HTTPS_PROXY", $null, "Process")
[System.Environment]::SetEnvironmentVariable("ALL_PROXY", $null, "Process")
[System.Environment]::SetEnvironmentVariable("http_proxy", $null, "Process")
[System.Environment]::SetEnvironmentVariable("https_proxy", $null, "Process")
[System.Environment]::SetEnvironmentVariable("all_proxy", $null, "Process")

if (Test-Path $PidFile) {
    $ExistingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($ExistingPid -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Live bot already running with PID $ExistingPid"
        exit 0
    }
}

$Args = @(
    "main.py", "live-loop",
    "--confirm-live", "I_UNDERSTAND_LIVE_RISK",
    "--cycles", "$Cycles",
    "--sleep-seconds", "$SleepSeconds"
)
$Process = Start-Process -FilePath $Python `
    -ArgumentList $Args `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutFile `
    -RedirectStandardError $ErrFile `
    -PassThru

$Process.Id | Set-Content $PidFile
Write-Host "Live guarded bot started with PID $($Process.Id)"
