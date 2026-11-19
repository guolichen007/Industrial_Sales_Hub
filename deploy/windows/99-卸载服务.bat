@echo off
chcp 65001 >nul
REM 卸载服务（默认保留数据）。
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1" %*
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" echo 执行未成功（退出码 %RC%），请查看 logs 目录中的日志。
pause
exit /b %RC%
