@echo off
title Agente Trading Autonomo V4.0
cd /d "%~dp0"
echo ===================================================
echo     Iniciando Agente Trading Autonomo V4.0
echo ===================================================
echo Activando entorno virtual y arrancando servicio...
.\venv\Scripts\python.exe main.py
pause
