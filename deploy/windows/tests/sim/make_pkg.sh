#!/bin/bash
# 构造“安装包目录”（模拟 Windows 发行结构）：make_pkg.sh <git-ref|WORKTREE> <输出目录> <版本号>
# runtime/python/python.exe 为指向 venv python 的 shell shim；runtime/pgsql/bin 为 pgserver PostgreSQL 16 二进制的 shim。
set -e
REF=$1; OUT=$2; VER=$3
O=${ISH_SIM_TOOLS:?设置 ISH_SIM_TOOLS 为含 env/venv、env/pwsh 的目录}
R=$(cd "$(dirname "$0")/../../../.." && pwd)
rm -rf "$OUT"; mkdir -p "$OUT/app" "$OUT/runtime/python" "$OUT/runtime/pgsql/bin" "$OUT/runtime/winsw"
if [[ $REF == WORKTREE ]]; then
  ( cd $R && git ls-files -co --exclude-standard backend deploy | grep -v '^backend/tests/' | tar -cf - -T - ) | tar -xf - -C "$OUT/app.tmp" 2>/dev/null || { mkdir -p "$OUT/app.tmp"; ( cd $R && git ls-files -co --exclude-standard backend deploy | grep -v '^backend/tests/' | tar -cf - -T - ) | tar -xf - -C "$OUT/app.tmp"; }
else
  mkdir -p "$OUT/app.tmp"; ( cd $R && git archive --format=tar $REF backend deploy ) | tar -xf - -C "$OUT/app.tmp"
  rm -rf "$OUT/app.tmp/backend/tests"
fi
mv "$OUT/app.tmp/backend" "$OUT/app/backend"
mkdir -p "$OUT/app/frontend/dist"; cp -r $O/build/fe/dist/. "$OUT/app/frontend/dist/"
cp "$OUT/app.tmp/deploy/windows/"*.bat "$OUT/" ; cp -r "$OUT/app.tmp/deploy/windows/scripts" "$OUT/scripts"; cp -r "$OUT/app.tmp/deploy/windows/config-templates" "$OUT/config-templates"
rm -rf "$OUT/app.tmp"
find "$OUT/app" -name __pycache__ -prune -exec rm -rf {} +
cat > "$OUT/runtime/python/python.exe" <<EOF
#!/bin/bash
exec $O/env/venv/bin/python "\$@"
EOF
for t in postgres psql pg_dump pg_restore pg_isready initdb pg_ctl; do
  printf '#!/bin/bash\nexec %s/env/venv/lib/python3.12/site-packages/pgserver/pginstall/bin/%s "$@"\n' "$O" "$t" > "$OUT/runtime/pgsql/bin/$t.exe"
done
for t in postgres psql pg_dump pg_restore pg_isready initdb pg_ctl; do cp "$OUT/runtime/pgsql/bin/$t.exe" "$OUT/runtime/pgsql/bin/$t"; done  # Linux 模拟：Python 端不带 .exe 查找
chmod +x "$OUT/runtime/python/python.exe" "$OUT/runtime/pgsql/bin/"*
echo "winsw-sim" > "$OUT/runtime/winsw/WinSW-x64.exe"; echo "vc-sim" > "$OUT/runtime/vc_redist.x64.exe"
echo "$VER (sim $REF)" > "$OUT/VERSION.txt"
mkdir -p "$OUT/licenses"; echo "sim" > "$OUT/licenses/README.txt"
( cd "$OUT" && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum | sed 's#  \./#  #' > SHA256SUMS.txt )
echo "pkg $OUT files=$(wc -l < $OUT/SHA256SUMS.txt)"
