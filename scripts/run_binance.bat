@echo off
title Bot Binance (live-loop)
cd /d "%~dp0.."
:restart
echo [%date% %time%] Iniciando live-loop de Binance...
venv\Scripts\python.exe main.py live-loop --confirm-live I_UNDERSTAND_LIVE_RISK --sleep-seconds 300
echo [%date% %time%] live-loop termino/cayo. Reiniciando en 15s...
timeout /t 15 /nobreak >nul
goto restart
