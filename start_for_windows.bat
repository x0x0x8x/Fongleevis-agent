@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo =====================================
echo   Fongleevis Agent Launcher
echo =====================================
echo.

:: ========================================
:: 定义 Python 可能的位置
:: ========================================
set PYTHON_EXE=

:: 1. 检查 PATH 里的 python
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_EXE=python
    goto :python_found
)

:: 2. 检查 PATH 里的 python3
python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON_EXE=python3
    goto :python_found
)

:: 3. 检查 Windows 常见安装位置
for %%p in (
    "C:\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python310\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python310\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Microsoft\WindowsApps\python.exe"
) do (
    if exist %%p (
        set PYTHON_EXE=%%p
        goto :python_found
    )
)

:: 4. 检查 py 命令（Windows Python Launcher）
py --version >nul 2>&1
if not errorlevel 1 (
    :: 用 py 查找最新版本
    for /f "tokens=2" %%i in ('py -3.11 --version 2^>^&1') do (
        set PYTHON_EXE=py -3.11
        goto :python_found
    )
    for /f "tokens=2" %%i in ('py -3.10 --version 2^>^&1') do (
        set PYTHON_EXE=py -3.10
        goto :python_found
    )
)

:: ========================================
:: Python 没找到
:: ========================================
echo.
echo [ERROR] Python not found!
echo.
echo ========================================
echo   请安装 Python 3.10 或更高版本
echo ========================================
echo.
echo   1. 下载 Python: https://www.python.org/downloads/
echo   2. 安装时 必须勾选 "Add Python to PATH"
echo   3. 安装完成后，重新运行本脚本
echo.
echo ========================================
echo.
pause
exit /b 1

:python_found
:: ========================================
:: Python 找到了，检查版本
:: ========================================
echo [OK] Found Python: %PYTHON_EXE%

%PYTHON_EXE% --version

:: 检查版本 >= 3.10
%PYTHON_EXE% -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python version too old! Need 3.10+
    echo.
    pause
    exit /b 1
)

echo [OK] Python version >= 3.10
echo.

:: ========================================
:: 检查 venv 是否可用（Windows 通常默认可用，但以防万一）
:: ========================================
%PYTHON_EXE% -c "import venv" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python venv module not available!
    echo.
    echo ========================================
    echo   请重新安装 Python，确保包含 venv 模块
    echo ========================================
    echo.
    echo   1. 下载 Python: https://www.python.org/downloads/
    echo   2. 安装时确保勾选所有组件
    echo   3. 安装完成后，重新运行本脚本
    echo.
    echo ========================================
    echo.
    pause
    exit /b 1
)

echo [OK] venv module available
echo.

:: ========================================
:: 执行 launcher.py（用找到的 Python）
:: ========================================
%PYTHON_EXE% launcher.py

pause