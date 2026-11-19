@echo off
chcp 65001 >nul
REM 默认恢复演练；加 -Production 才覆盖正式数据。
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restore.ps1" %*
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" echo 执行未成功（退出码 %RC%），请查看 logs 目录中的日志。
pause
exit /b %RC%
