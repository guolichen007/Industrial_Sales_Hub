@echo off
chcp 65001 >nul
REM 启动并打开系统（重复执行安全）。
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" echo 执行未成功（退出码 %RC%），请查看 logs 目录中的日志。
pause
exit /b %RC%
