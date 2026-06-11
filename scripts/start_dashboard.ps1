param(
    [int]$Port = 8765,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"
$PidFile = Join-Path $Root "dashboard.pid"
$OutFile = Join-Path $Root "dashboard.out.log"
$ErrFile = Join-Path $Root "dashboard.err.log"

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
        Write-Host "Dashboard already running with PID $ExistingPid"
        exit 0
    }
}

$Args = @("main.py", "dashboard", "--host", $HostAddress, "--port", "$Port")
$Process = Start-Process -FilePath $Python `
    -ArgumentList $Args `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutFile `
    -RedirectStandardError $ErrFile `
    -PassThru

$Process.Id | Set-Content $PidFile
Write-Host "Dashboard started with PID $($Process.Id)"
Write-Host "Open http://${HostAddress}:$Port"
