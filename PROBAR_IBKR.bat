@echo off
cd /d "%~dp0"
echo === Prueba de conexion IBKR (paper) === > ibkr_test_output.txt
echo Fecha: %date% %time% >> ibkr_test_output.txt
echo. >> ibkr_test_output.txt
venv\Scripts\python.exe main.py ibkr --cycles 1 >> ibkr_test_output.txt 2>&1
echo. >> ibkr_test_output.txt
echo === FIN (codigo de salida: %errorlevel%) === >> ibkr_test_output.txt
