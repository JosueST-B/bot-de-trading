$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"

Set-Location $Root
$PathValue = [System.Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $PathValue) {
    $PathValue = [System.Environment]::GetEnvironmentVariable("PATH", "Process")
}
[System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
if ($PathValue) {
    [System.Environment]::SetEnvironmentVariable("Path", $PathValue, "Process")
}

& $Python "main.py" "healthcheck"
