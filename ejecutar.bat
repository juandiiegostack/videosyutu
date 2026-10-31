@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON="
set "PYTHONW="

REM Preferir el entorno virtual del proyecto si existe.
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    set "PYTHONW=.venv\Scripts\pythonw.exe"
    goto :comprobar_dependencias
)

REM Buscar el lanzador oficial de Python, luego python en PATH.
where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
    where pyw >nul 2>nul
    if not errorlevel 1 set "PYTHONW=pyw -3"
    goto :comprobar_dependencias
)

where python >nul 2>nul
if errorlevel 1 goto :sin_python

set "PYTHON=python"
where pythonw >nul 2>nul
if not errorlevel 1 set "PYTHONW=pythonw"

:comprobar_dependencias
%PYTHON% -c "import customtkinter, yt_dlp" >nul 2>nul
if errorlevel 1 goto :sin_dependencias

REM pythonw evita dejar una consola abierta. Si no está disponible, se usa python.
if not defined PYTHONW set "PYTHONW=%PYTHON%"
start "" %PYTHONW% "main.py"
exit /b 0

:sin_python
echo.
echo Python no esta instalado o no esta en el PATH.
echo Instala Python 3.10 o superior desde https://www.python.org/downloads/
echo y marca la opcion "Add Python to PATH" durante la instalacion.
echo.
pause
exit /b 1

:sin_dependencias
echo.
echo Faltan dependencias de Python.
echo Abre una terminal en esta carpeta y ejecuta:
echo.
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Si no usas el entorno virtual .venv, ejecuta:
echo     python -m pip install -r requirements.txt
echo.
pause
exit /b 1
