@echo off
title Bot IBKR (acciones)
cd /d "%~dp0.."
:restart
echo [%date% %time%] Iniciando loop IBKR (requiere IB Gateway abierto y logueado)...
venv\Scripts\python.exe main.py ibkr --sleep-seconds 300
echo [%date% %time%] ibkr termino/cayo. Reiniciando en 30s...
timeout /t 30 /nobreak >nul
goto restart
