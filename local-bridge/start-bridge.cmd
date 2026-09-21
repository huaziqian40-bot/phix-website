@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
title phix 本机直连服务 (phix-local-bridge)

set "HERE=%~dp0"
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY if exist "D:\phix\server\.venv\Scripts\python.exe" set "PY=D:\phix\server\.venv\Scripts\python.exe"
if not defined PY (
  echo [x] 找不到 Python。请先安装 Python 3.10+ 并勾选 "Add python.exe to PATH"。
  pause
  exit /b 2
)

echo ============================================================
echo   phix 本机直连服务（phix-local-bridge）
echo   * 只监听 127.0.0.1:38123，不对外网开放
echo   * 抓取用你自己的网络，凭据只在内存里用，不落盘、不写日志
echo   * 关掉这个窗口 = 停止服务（网页端会自动回到「服务器抓取」）
echo ============================================================
echo.

"%PY%" -c "import requests, bs4" 2>nul
if errorlevel 1 (
  echo [!] 缺少抓取依赖（requests / beautifulsoup4），正在安装…
  "%PY%" -m pip install -r "%HERE%requirements-webapp.txt"
  echo.
)

"%PY%" "%HERE%bridge.py" %*
echo.
echo 服务已退出。
pause
