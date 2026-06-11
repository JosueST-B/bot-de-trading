param(
    [int]$Port = 8767
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"
$PidFile = Join-Path $Root "dashboard.pid"
$OutFile = Join-Path $Root "dashboard.out.log"
$ErrFile = Join-Path $Root "dashboard.err.log"

Set-Location $Root

if (Test-Path $PidFile) {
    $ExistingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($ExistingPid -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
        & taskkill /PID $ExistingPid /T /F | Out-Null
        Start-Sleep -Milliseconds 500
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$Process = Start-Process -WindowStyle Hidden `
    -FilePath $Python `
    -ArgumentList @("main.py", "dashboard", "--host", "0.0.0.0", "--port", "$Port") `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutFile `
    -RedirectStandardError $ErrFile `
    -PassThru

$Process.Id | Set-Content $PidFile

$Ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -ExpandProperty IPAddress

Write-Host "Dashboard LAN started with PID $($Process.Id)"
foreach ($Ip in $Ips) {
    Write-Host "Open from phone: http://${Ip}:$Port"
}

