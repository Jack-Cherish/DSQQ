@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "tools\breeze\install_breeze.ps1" (
  echo 没找到 tools\breeze\install_breeze.ps1，先把配置包压缩包整个解压出来再双击。
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\breeze\install_breeze.ps1" %*
pause
