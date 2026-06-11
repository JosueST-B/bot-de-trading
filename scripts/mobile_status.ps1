$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"

Set-Location $Root

Write-Host "=== Bot health ==="
& $Python "main.py" "healthcheck" "--dashboard-url" "http://127.0.0.1:8767/api/summary"

Write-Host "`n=== Live processes ==="
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like "python*" -and $_.CommandLine -match "bot de trading.*main.py live" } |
    Select-Object ProcessId, ParentProcessId, CommandLine |
    Format-List

Write-Host "`n=== Local dashboard URLs ==="
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
    ForEach-Object { "http://$($_.IPAddress):8767" }

