#!/usr/bin/env bash
# 单命令运行浏览器端到端验收（Linux/CI）。
# 用法：DATABASE_URL=postgres://... e2e/run_e2e.sh <脚本> <输出目录>
#   需要：已构建 frontend/dist；python 依赖（backend/requirements.txt + playwright）；playwright 浏览器。
# 每次运行使用全新数据目录；DATABASE_URL 指向的库应为空库（脚本会 migrate + 写入合成演示数据）。
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=${1:?e2e 脚本}
OUT=${2:?输出目录}
PORT=${E2E_PORT:-8765}
export ISH_MODE=${ISH_MODE:-DEMO}
export DATA_DIR=${E2E_DATA_DIR:-$(mktemp -d)}
export FRONTEND_DIST=${FRONTEND_DIST:-$ROOT/frontend/dist}
mkdir -p "$OUT"
cd "$ROOT/backend"
python manage.py migrate --noinput -v0
python manage.py seed_demo
python manage.py runserver 127.0.0.1:$PORT --noreload > "$OUT/server.log" 2>&1 &
SP=$!
trap 'kill $SP 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -sf http://127.0.0.1:$PORT/healthz >/dev/null && break; sleep 1; done
E2E_BASE=http://127.0.0.1:$PORT python "$ROOT/$SCRIPT" "$OUT"
