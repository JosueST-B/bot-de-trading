@echo off
setlocal
REM ============================================================
REM  Instalador unico: bot 24/7 + IBKR (Fase 12)
REM  CLICK DERECHO sobre este archivo -> "Ejecutar como administrador"
REM ============================================================
cd /d "%~dp0"

REM --- Verificar permisos de administrador ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Debes ejecutarlo como Administrador:
    echo         click derecho sobre INSTALAR_TODO.bat ^> "Ejecutar como administrador"
    echo.
    pause
    exit /b 1
)

echo.
echo [1/5] Instalando dependencia de Interactive Brokers (ib_async)...
venv\Scripts\pip.exe install ib_async
if %errorlevel% neq 0 (
    echo [AVISO] Fallo pip con ib_async, probando ib_insync como alternativa...
    venv\Scripts\pip.exe install ib_insync
)

echo.
echo [2/5] Registrando tareas 24/7 (arranque automatico + reinicio si falla)...
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_autostart.ps1 -IncludeIBKR

echo.
echo [3/5] Descargando instalador oficial de IB Gateway...
if exist "%TEMP%\ibgateway-installer.exe" (
    echo        Ya estaba descargado.
) else (
    powershell -Command "try { Invoke-WebRequest -Uri 'https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-windows-x64.exe' -OutFile '%TEMP%\ibgateway-installer.exe' } catch { exit 1 }"
    if %errorlevel% neq 0 echo [AVISO] No se pudo descargar; baja IB Gateway manualmente del sitio de IBKR.
)
if exist "%TEMP%\ibgateway-installer.exe" (
    echo        Abriendo instalador de IB Gateway ^(siguiente ^> siguiente ^> instalar^)...
    start "" "%TEMP%\ibgateway-installer.exe"
)

echo.
echo [4/5] Probando el modulo Earn en simulacion (dry-run)...
venv\Scripts\python.exe main.py earn --dry-run

echo.
echo [5/5] Enviando reporte Earn por Telegram...
venv\Scripts\python.exe main.py earn-report --send

echo.
echo ============================================================
echo  LISTO. Estado final:
echo   - Loop Binance (trading + Earn): corriendo 24/7, arranca solo
echo   - Loop IBKR: registrado; operara cuando IB Gateway este logueado
echo.
echo  TE FALTA SOLO 1 PASO MANUAL (por tus credenciales):
echo   1. Termina la instalacion de IB Gateway que se abrio
echo   2. Inicia sesion con tu usuario PAPER de IBKR
echo   3. En Configure ^> Settings ^> API: marca "Enable ActiveX and
echo      Socket Clients", desmarca "Read-Only API", puerto 4002
echo      (guia completa en IBKR.md)
echo ============================================================
echo.
pause
