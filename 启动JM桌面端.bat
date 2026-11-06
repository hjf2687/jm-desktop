@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

rem 先找一个"确实装了 PySide6"的 Python 再启动；
rem 实在找不到就给明确提示，而不是双击之后毫无反应。
set "RUN="
set "CAND="
set "PROBE=import importlib.util as u,sys;sys.exit(0 if u.find_spec('PySide6') else 1)"

rem 1) 作者机器上的 miniconda
call :probe "D:\tools\miniconda3\pythonw.exe"
if defined RUN goto :launch

rem 2) PATH 上的 pythonw（无控制台窗口，最理想）
for %%I in (pythonw.exe) do set "CAND=%%~$PATH:I"
call :probe "%CAND%"
if defined RUN goto :launch

rem 3) PATH 上的 python
set "CAND="
for %%I in (python.exe) do set "CAND=%%~$PATH:I"
call :probe "%CAND%"
if defined RUN goto :launch

rem 4) py 启动器
set "CAND="
for %%I in (py.exe) do set "CAND=%%~$PATH:I"
call :probe "%CAND%"
if defined RUN goto :launch

goto :nodep

rem ------------------------------------------------------------ 探测
:probe
if "%~1"=="" exit /b 0
echo %~1 | find /i "WindowsApps" >nul
if not errorlevel 1 exit /b 0
"%~1" -c "%PROBE%" >nul 2>nul
if not errorlevel 1 set "RUN=%~1"
exit /b 0

rem ------------------------------------------------------------ 启动
:launch
start "" "%RUN%" main.py
exit /b 0

rem ------------------------------------------------------------ 没有可用环境
:nodep
echo.
echo   [x] 启动失败：没有找到安装好依赖的 Python
echo.
echo       请在本文件夹里先执行一次：
echo           pip install -r requirements.txt
echo.
echo       如果提示找不到 python 或 pip，说明安装 Python 时没有勾选
echo       "Add Python to PATH"。重装 Python 并勾上该选项，再运行本文件。
echo.
pause
exit /b 1
