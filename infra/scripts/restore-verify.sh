#!/usr/bin/env bash
# 恢复演练：把备份恢复到临时数据库 + 临时目录，并校验报价 PDF 哈希与快照一致。
# 用法：infra/scripts/restore-verify.sh /var/backups/ish/20261119-020000
set -euo pipefail
B=${1:?备份目录}
cd "$(dirname "$0")/.."
set -a; source ../.env; set +a
( cd "$B" && sha256sum -c SHA256SUMS )
TMPDB=ish_restore_check
docker compose --env-file ../.env exec -T db psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS $TMPDB" -c "CREATE DATABASE $TMPDB"
docker compose --env-file ../.env exec -T db pg_restore -U "$POSTGRES_USER" -d $TMPDB --no-owner < "$B/db.dump"
docker compose --env-file ../.env exec -T api sh -c "rm -rf /tmp/restore && mkdir -p /tmp/restore"
docker compose --env-file ../.env exec -T api tar -C /tmp/restore -xzf - < "$B/files.tgz"
docker compose --env-file ../.env exec -T \
  -e DATABASE_URL="postgres://$POSTGRES_USER:$POSTGRES_PASSWORD@db:5432/$TMPDB" -e DATA_DIR=/tmp/restore \
  api python manage.py verify_integrity
docker compose --env-file ../.env exec -T db psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE $TMPDB"
echo "restore-check PASS: $B"
