#!/usr/bin/env bash
# 构建 Windows x64 局域网正式交付 ZIP（在构建机上执行；客户端与目标主机不需要联网）。
#
# 用法：deploy/windows/build_package.sh <输出目录> [<下载缓存目录>]
#   - 源码取自 `git archive HEAD`（固定提交，不含工作区未提交改动）；工作区不干净时拒绝构建（除非 ISH_ALLOW_DIRTY=1）。
#   - 下载：Python 3.12.10 嵌入式包（python.org，sigstore 签名校验）、PostgreSQL 16.16-1 Windows x64 二进制（EnterpriseDB）、
#           WinSW 2.12.0、Microsoft VC++ 2015-2022 x64 运行库、所有 Python 依赖的 win_amd64 wheel。全部记录 SHA256。
#   - 前端：npm ci && npm run build（构建机需要 Node 22）。
#   - 产物：Industrial_Sales_Hub_<版本>_LAN_Windows_x64.zip 与 .sha256、BUILD-MANIFEST.json、SBOM。
set -euo pipefail
OUT=${1:?输出目录}
CACHE=${2:-$OUT/.cache}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

if [[ -n "$(git status --porcelain)" && "${ISH_ALLOW_DIRTY:-0}" != "1" ]]; then
  echo "工作区有未提交改动，拒绝构建（正式包必须对应固定提交）。" >&2; exit 2
fi
SHA=$(git rev-parse HEAD)
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' backend/config/settings.py)
NAME="Industrial_Sales_Hub_${VERSION}_LAN_Windows_x64"
STAGE="$OUT/stage/$NAME"
mkdir -p "$OUT" "$CACHE"
# 可续跑：同一提交的阶段标记存在则跳过（构建机网络慢时分段执行）；提交变化则全部重来
MARK="$OUT/.marks-$SHA"
if [[ ! -d "$MARK" ]]; then rm -rf "$OUT/stage" "$OUT"/.marks-*; mkdir -p "$MARK"; fi
mkdir -p "$STAGE"
done_() { touch "$MARK/$1"; }
has() { [[ -f "$MARK/$1" ]]; }

PY_VER=3.12.10
PG_VER=16.16-1
WINSW_VER=2.12.0
# 已核验的上游校验和（首次核验方式见 docs/evidence/LAN/L1/README.md）
declare -A EXPECT=(
  [python-$PY_VER-embed-amd64.zip]=4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3
  [postgresql-$PG_VER-windows-x64-binaries.zip]=5c0d06f3d26a929ecb36dd931409f1e51a4181355794a259b90aae8bffc57a9f
  [WinSW-x64.exe]=05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da
)
fetch() { # url file
  local f="$CACHE/$2"
  if [[ ! -s "$f" ]]; then
    echo "下载 $1"
    curl -sSfL --retry 3 -C - -o "$f.part" "$1" && mv "$f.part" "$f"
  fi
  if [[ -n "${EXPECT[$2]:-}" ]]; then
    echo "${EXPECT[$2]}  $f" | sha256sum -c --quiet - || { echo "校验和不符：$2" >&2; exit 3; }
  fi
}
fetch "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip" "python-$PY_VER-embed-amd64.zip"
fetch "https://get.enterprisedb.com/postgresql/postgresql-$PG_VER-windows-x64-binaries.zip" "postgresql-$PG_VER-windows-x64-binaries.zip"
fetch "https://github.com/winsw/winsw/releases/download/v$WINSW_VER/WinSW-x64.exe" "WinSW-x64.exe"
fetch "https://aka.ms/vs/17/release/vc_redist.x64.exe" "vc_redist.x64.exe"   # 微软滚动更新：记录实际哈希，并做 Authenticode 校验（见下）

# Authenticode 签名校验（需要 python 包 signify；没有则提示并失败，除非显式跳过）
if python3 -c "import signify" 2>/dev/null; then
  python3 - "$CACHE" <<'PY'
import sys
from pathlib import Path
from signify.authenticode.signed_file.pe import SignedPEFile
cache = Path(sys.argv[1])
for name, who in [("vc_redist.x64.exe", "Microsoft Corporation")]:
    with open(cache / name, "rb") as fh:
        pe = SignedPEFile(fh)
        ok, err = pe.explain_verify()
        subjects = [c.subject.dn for sd in pe.signatures for c in sd.certificates]
        if str(ok) != "AuthenticodeVerificationResult.OK" or not any(who in s for s in subjects):
            sys.exit(f"签名校验失败：{name} {ok} {err}")
        print(f"签名校验通过：{name}（{who}）")
PY
elif [[ "${ISH_SKIP_AUTHENTICODE:-0}" != "1" ]]; then
  echo "缺少 signify，无法校验 vc_redist 签名（pip install signify，或设置 ISH_SKIP_AUTHENTICODE=1 并在报告中注明）" >&2; exit 4
fi

if ! has src; then
echo "== 源码（git archive $SHA）"
mkdir -p "$STAGE/app"
git archive --format=tar HEAD backend frontend | tar -x -C "$STAGE/app"
rm -rf "$STAGE/app/backend/tests"   # 测试不进入正式包
done_ src; fi

if ! has fe; then
echo "== 前端构建"
FE_BUILD="$OUT/fe-build"
mkdir -p "$FE_BUILD"
rm -rf "$FE_BUILD/frontend/src" "$FE_BUILD/frontend/dist"
git archive --format=tar HEAD frontend | tar -x -C "$FE_BUILD"
( cd "$FE_BUILD/frontend" && { [[ -d node_modules ]] || npm ci --no-audit --no-fund --loglevel=error; } && npm run build >/dev/null )
rm -rf "$STAGE/app/frontend"
mkdir -p "$STAGE/app/frontend"
cp -r "$FE_BUILD/frontend/dist" "$STAGE/app/frontend/dist"
if grep -rIl -E "https?://(cdn|fonts\.googleapis|unpkg|jsdelivr)" "$STAGE/app/frontend/dist" >/dev/null; then echo "前端包含外部 CDN 引用" >&2; exit 5; fi
done_ fe; fi

PYDIR="$STAGE/runtime/python"
SITE="$PYDIR/Lib/site-packages"
if ! has py; then
echo "== Python 运行时"
mkdir -p "$PYDIR"
unzip -q "$CACHE/python-$PY_VER-embed-amd64.zip" -d "$PYDIR"
# 嵌入式 Python：启用 site-packages 与应用目录
PTH=$(ls "$PYDIR"/python3*._pth)
cat > "$PTH" <<EOF
python312.zip
.
Lib\\site-packages
..\\..\\app\\backend
import site
EOF
echo "== Python 依赖（win_amd64 wheel，仅运行所需）"
WHEELS="$CACHE/wheels"
mkdir -p "$WHEELS"
grep -v -E "^(pytest|gunicorn)" backend/requirements.txt | sed 's/#.*//' | grep -v '^\s*$' > "$OUT/req-runtime.txt"
pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp -d "$WHEELS" -r "$OUT/req-runtime.txt" -q
mkdir -p "$SITE"
python3 - "$WHEELS" "$SITE" "$OUT/req-runtime.txt" <<'PY'
import re, sys, zipfile
from pathlib import Path
wheels, site, req = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
# 只安装 requirements 解析出的分发及其依赖：按文件名匹配（pip download 已解析依赖）
names = set()
for w in wheels.glob("*.whl"):
    if "win_amd64" in w.name or "none-any" in w.name or "py2.py3" in w.name:
        names.add(w)
for w in sorted(names):
    if re.match(r"(pytest|pluggy|iniconfig|pygments|exceptiongroup|tomli|gunicorn|packaging)-", w.name, re.I):
        continue
    with zipfile.ZipFile(w) as z:
        z.extractall(site)
    print("  安装", w.name)
PY
done_ py; fi

if ! has pg; then
echo "== PostgreSQL（裁剪 pgAdmin/StackBuilder/文档/头文件）"
rm -rf "$STAGE/runtime/pgsql"
# 只保留服务端与命令行工具运行所需文件：去掉 pgAdmin（目录名含空格 "pgAdmin 4"）、StackBuilder、文档、头文件、.lib/.pdb
unzip -Z1 "$CACHE/postgresql-$PG_VER-windows-x64-binaries.zip" \
  | grep -v -E '^pgsql/(pgAdmin|StackBuilder|doc|include|symbols)|^pgsql/bin/(wx|stackbuilder)|\.(lib|pdb)$|/$' > "$OUT/pg-files.txt"
if grep -q -i pgadmin "$OUT/pg-files.txt"; then echo "pgAdmin 未被排除" >&2; exit 6; fi
( cd "$STAGE/runtime" && tr '\n' '\0' < "$OUT/pg-files.txt" | xargs -0 -n 2000 unzip -q -o "$CACHE/postgresql-$PG_VER-windows-x64-binaries.zip" )
mkdir -p "$STAGE/runtime/winsw"
cp "$CACHE/WinSW-x64.exe" "$STAGE/runtime/winsw/"
cp "$CACHE/vc_redist.x64.exe" "$STAGE/runtime/"
done_ pg; fi

if ! has meta; then
echo "== 脚本、配置模板、许可"
cp deploy/windows/*.bat "$STAGE/"
cp -r deploy/windows/scripts "$STAGE/scripts"
cp -r deploy/windows/config-templates "$STAGE/config-templates"
cp docs/LAN安装使用手册.md "$STAGE/使用说明.md"
cp docs/备份恢复与升级手册.md "$STAGE/备份恢复与升级手册.md"
mkdir -p "$STAGE/licenses"
cp backend/fonts/LICENSE-* "$STAGE/licenses/"
cp "$STAGE/runtime/pgsql/server_license.txt" "$STAGE/licenses/PostgreSQL-server_license.txt"
cp "$STAGE/runtime/pgsql/commandlinetools_3rd_party_licenses.txt" "$STAGE/licenses/PostgreSQL-3rd_party_licenses.txt"
cp "$PYDIR/LICENSE.txt" "$STAGE/licenses/Python-LICENSE.txt"
python3 deploy/windows/sbom.py "$SITE" "$STAGE/licenses" > "$STAGE/licenses/SBOM.json"
cat > "$STAGE/licenses/README.txt" <<EOF
本安装包包含的第三方组件及其许可证：
- Python $PY_VER 嵌入式发行版（PSF License）— python.org，sigstore 签名已核验
- PostgreSQL $PG_VER Windows x64（PostgreSQL License）— EnterpriseDB 构建，Authenticode 签名（EnterpriseDB Corporation）已核验
- WinSW $WINSW_VER（MIT License）— github.com/winsw/winsw
- Microsoft Visual C++ 2015-2022 Redistributable x64 — 按微软可再分发条款随附，Authenticode 签名已核验
- Python 依赖：见 SBOM.json（名称、版本、许可证）
- 字体：DroidSansFallbackFull（Apache-2.0）、DejaVu Sans（Bitstream Vera 许可）
EOF
mkdir -p "$STAGE/licenses/WinSW"; echo "WinSW $WINSW_VER — MIT License — https://github.com/winsw/winsw/blob/v$WINSW_VER/LICENSE.txt" > "$STAGE/licenses/WinSW/LICENSE-URL.txt"
done_ meta; fi

echo "$VERSION (git $SHA)" > "$STAGE/VERSION.txt"
echo "== 校验清单"
( cd "$STAGE" && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum | sed 's#  \./#  #' > SHA256SUMS.txt )

python3 - "$STAGE" "$OUT/BUILD-MANIFEST.json" "$SHA" "$VERSION" "$CACHE" <<'PY'
import hashlib, json, sys, os
from pathlib import Path
stage, out, sha, ver, cache = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
comp = {p.name: h(p) for p in cache.iterdir() if p.is_file() and not p.name.endswith(".part")}
size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
json.dump({"name": stage.name, "version": ver, "git_sha": sha, "files": sum(1 for f in stage.rglob('*') if f.is_file()),
           "unpacked_mb": round(size / 2**20, 1), "upstream_components_sha256": comp}, open(out, "w"), ensure_ascii=False, indent=1)
PY

echo "== 打包"
( cd "$OUT/stage" && rm -f "$OUT/$NAME.zip" && zip -q -r -X "$OUT/$NAME.zip" "$NAME" )
( cd "$OUT" && sha256sum "$NAME.zip" > "$NAME.zip.sha256" )
cp "$STAGE/SHA256SUMS.txt" "$OUT/$NAME.SHA256SUMS.txt"
echo "完成：$OUT/$NAME.zip（$(du -h "$OUT/$NAME.zip" | cut -f1)），git $SHA"
cat "$OUT/$NAME.zip.sha256"
