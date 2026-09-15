@echo off
REM ============================================================
REM  Arranca los 3 procesos del bot y se auto-registra para
REM  iniciar solo con Windows. NO requiere administrador.
REM  Doble clic normal (sin "ejecutar como administrador").
REM ============================================================
cd /d "%~dp0"

echo Lanzando procesos del bot...

REM --- Trading Binance (con reinicio automatico) ---
start "Bot Binance" /min cmd /c scripts\run_binance.bat

REM --- Panel unificado ---
start "Bot Panel" /min cmd /c scripts\run_panel.bat

REM --- IBKR (solo si IB Gateway esta abierto; si no, se reintenta solo) ---
start "Bot IBKR" /min cmd /c scripts\run_ibkr.bat

REM --- Auto-registro en el arranque de Windows (carpeta Inicio, sin admin) ---
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNK=%STARTUP%\ArrancarBot.lnk"
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%');" ^
  "$s.TargetPath='%~f0';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.WindowStyle=7;" ^
  "$s.Description='Arranca el bot de trading al iniciar sesion';" ^
  "$s.Save()"

echo.
echo ============================================================
echo  LISTO. Corriendo ahora:
echo   - Trading Binance (con Earn integrado)
echo   - Panel unificado: http://127.0.0.1:8770
echo   - IBKR (si IB Gateway esta abierto)
echo.
echo  Se arrancara solo cada vez que inicies sesion en Windows.
echo  Para desactivar el autoarranque, borra:
echo   %LNK%
echo ============================================================
timeout /t 8 /nobreak >nul
