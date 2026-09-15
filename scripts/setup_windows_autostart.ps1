# Configura el bot para correr 24/7 en Windows:
# 1. Desactiva suspension del equipo (con corriente).
# 2. Registra tareas programadas que arrancan al inicio y se reinician si fallan.
#
# Ejecutar UNA VEZ como Administrador:
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_autostart.ps1
#
# Para incluir el loop de IBKR (requiere IB Gateway abierto):
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_autostart.ps1 -IncludeIBKR

param(
    [switch]$IncludeIBKR,
    [int]$SleepSeconds = 300
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $RepoDir "venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "No se encontro $Python. Verifica el venv."
}

Write-Host "Repositorio: $RepoDir"

# --- 1. Evitar que el equipo se suspenda (conectado a corriente) ---
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change disk-timeout-ac 0
Write-Host "Suspension desactivada (AC)."

function Register-BotTask {
    param(
        [string]$Name,
        [string]$Arguments
    )
    $action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $RepoDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 2) `
        -ExecutionTimeLimit (New-TimeSpan -Days 0) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "Tarea registrada: $Name"
}

# --- 2. Loop live de Binance (trading + Earn) ---
Register-BotTask -Name "BotTrading-BinanceLive" `
    -Arguments "main.py live-loop --confirm-live I_UNDERSTAND_LIVE_RISK --sleep-seconds $SleepSeconds"

# --- 3. Loop de IBKR (opcional) ---
if ($IncludeIBKR) {
    Register-BotTask -Name "BotTrading-IBKR" `
        -Arguments "main.py ibkr --sleep-seconds $SleepSeconds"
    Write-Host "NOTA: IB Gateway debe estar abierto y logueado para que el loop IBKR opere."
}

# --- 4. Panel unificado (Binance + Earn + IBKR) en http://127.0.0.1:8770 ---
Register-BotTask -Name "BotTrading-Panel" `
    -Arguments "main.py panel --host 127.0.0.1 --port 8770"

# --- 5. Arrancar ahora sin reiniciar ---
Start-ScheduledTask -TaskName "BotTrading-BinanceLive"
Start-ScheduledTask -TaskName "BotTrading-Panel"
if ($IncludeIBKR) { Start-ScheduledTask -TaskName "BotTrading-IBKR" }
Write-Host "Panel unificado: abre http://127.0.0.1:8770 en tu navegador."

Write-Host ""
Write-Host "Listo. Los loops arrancan al encender la PC y se reinician solos si fallan."
Write-Host "Ver estado:   Get-ScheduledTask -TaskName 'BotTrading-*' | Get-ScheduledTaskInfo"
Write-Host "Detener:      Stop-ScheduledTask -TaskName 'BotTrading-BinanceLive'"
Write-Host "Eliminar:     Unregister-ScheduledTask -TaskName 'BotTrading-BinanceLive'"
