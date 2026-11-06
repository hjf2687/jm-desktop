@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

set "PY="
if exist "D:\tools\miniconda3\python.exe" set "PY=D:\tools\miniconda3\python.exe"
if not defined PY (py -c "import sys" >nul 2>nul && set "PY=py")
if not defined PY (python -c "import sys" >nul 2>nul && set "PY=python")
if not defined PY (python3 -c "import sys" >nul 2>nul && set "PY=python3")

if not defined PY goto :nopython

"%PY%" "scripts\pack_release.py" %*
echo.
pause
exit /b 0

:nopython
echo.
echo   [x] 没有找到可用的 Python
echo       请先安装 Python 3.9+，安装时勾选 "Add Python to PATH"
echo.
pause
exit /b 1
