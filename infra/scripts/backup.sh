#!/usr/bin/env bash
# 数据库 + 私有文件（PDF/附件）一起备份；只备份数据库视为失败。
# 建议 crontab：0 2 * * * /opt/ish/infra/scripts/backup.sh >> /var/log/ish-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source ../.env; set +a
TS=$(date +%Y%m%d-%H%M%S)
OUT=${BACKUP_DIR:-/var/backups/ish}/$TS
mkdir -p "$OUT"
docker compose --env-file ../.env exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$OUT/db.dump"
docker compose --env-file ../.env exec -T api tar -C /data -czf - private > "$OUT/files.tgz"
( cd "$OUT" && sha256sum db.dump files.tgz > SHA256SUMS )
# 可选：加密后同步到对象存储/异机
if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
  tar -C "$OUT" -cf - . | gpg --encrypt -r "$BACKUP_GPG_RECIPIENT" > "$OUT.tar.gpg" && rm -rf "$OUT"
fi
# 保留 30 天
find "${BACKUP_DIR:-/var/backups/ish}" -maxdepth 1 -mtime +30 -exec rm -rf {} +
echo "[$(date)] backup ok: $OUT"
