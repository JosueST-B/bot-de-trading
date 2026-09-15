@echo off
title Bot Panel unificado
cd /d "%~dp0.."
:restart
echo [%date% %time%] Iniciando panel unificado en http://127.0.0.1:8770 ...
venv\Scripts\python.exe main.py panel --host 127.0.0.1 --port 8770
echo [%date% %time%] panel termino/cayo. Reiniciando en 15s...
timeout /t 15 /nobreak >nul
goto restart
