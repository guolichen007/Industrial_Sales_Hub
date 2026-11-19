@echo off
chcp 65001 >nul
REM 本机试用（单机、SQLite），不用于正式部署。需要 Python 3.10+；首次运行还需要 Node.js 18+ 用于构建前端。
cd /d %~dp0

if not exist frontend\dist\index.html (
  echo 首次运行：构建前端……
  where npm >nul 2>nul || (echo 未找到 Node.js，请先安装 https://nodejs.org/ & goto :err)
  pushd frontend
  call npm install --no-audit --no-fund || (popd & goto :err)
  call npm run build || (popd & goto :err)
  popd
)

cd backend
if not exist .venv (
  echo 首次运行：创建虚拟环境并安装依赖……
  python -m venv .venv || goto :err
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt || goto :err
) else (
  call .venv\Scripts\activate.bat
)
python manage.py migrate --noinput || goto :err
python manage.py seed_demo
echo.
echo 浏览器打开 http://127.0.0.1:8000  账号 leader / assistant / sales / finance / admin，密码 Demo@2026
start "" http://127.0.0.1:8000
python manage.py runserver 127.0.0.1:8000
goto :eof
:err
echo 启动失败，请检查上方错误信息。
pause
