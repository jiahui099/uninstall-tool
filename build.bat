@echo off
chcp 65001 > nul
echo ========================================
echo   一键卸载工具 - 打包脚本
echo ========================================
echo.
echo 正在检查 Python 环境...
python --version 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] Python 已找到

REM 检查是否已安装 pyinstaller
pip show pyinstaller > nul 2>&1
if errorlevel 1 (
    echo.
    echo 正在安装 PyInstaller (首次运行会下载，约30秒)...
    pip install pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
)

echo.
echo 正在打包程序...
echo 打包过程可能需要 1-3 分钟，请耐心等待...
echo.

REM 打包 (隐藏控制台窗口，单文件)
pyinstaller --onefile ^
    --noconsole ^
    --name "一键卸载工具" ^
    --add-data "uninstall_tool.py;." ^
    --hidden-import tkinter ^
    --hidden-import tkinter.ttk ^
    --hidden-import winreg ^
    uninstall_tool.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查错误信息
    pause
    exit /b 1
)

echo.
echo ========================================
echo   打包成功！
echo ========================================
echo.
echo exe 文件位于:
cd /d "%~dp0"
if exist "dist\一键卸载工具.exe" (
    echo   dist\一键卸载工具.exe
    echo.
    echo 可以直接把这个 exe 文件复制到 U 盘发给妈妈了！
    echo.
)
if exist "dist\一键卸载工具.exe" (
    start explorer "dist"
)
pause