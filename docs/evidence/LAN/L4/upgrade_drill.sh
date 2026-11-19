set -e
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
O=/sessions/funny-sharp-brahmagupta/mnt/outputs
B=$O/pglib/pgserver/pginstall/bin
R=/sessions/funny-sharp-brahmagupta/mnt/工业销售平台/industrial-sales-hub
export PATH=$B:$PATH PYTHONPATH=$O/pylib:$O/pglib
W=$O/tmp/upg; rm -rf $W; mkdir -p $W
$B/psql -h 127.0.0.1 -p 54329 -U ish -d postgres -v ON_ERROR_STOP=1 -qc "drop database if exists ishupg with (force)" -c "create database ishupg"
# 1) V2.0 代码（4bab6f2）
git -C $R archive 4bab6f2 backend | tar -x -C $W && mv $W/backend $W/v20
unset DATABASE_URL DATA_DIR
export DATABASE_URL=postgres://ish@127.0.0.1:54329/ishupg DATA_DIR=$W/data
cd $W/v20
python3 manage.py migrate -v0
python3 manage.py seed_demo | tail -1
python3 manage.py shell <<'PY' 2>&1 | grep -v imported
from rest_framework.test import APIClient
from apps.accounts.models import User
c=APIClient(); c.force_authenticate(User.objects.get(username="assistant"))
q=c.post("/api/quotations/",{"project":1,"recipient":1,"legal_entity":1},format="json").json(); rid=q["latest"]["id"]
c.post(f"/api/quote-revisions/{rid}/lines/",{"product":1,"qty":"2"},format="json")
r=c.post(f"/api/quote-revisions/{rid}/render/").json()
open("/tmp/v20_sha.txt","w").write(r["pdf_sha256"])
lc=APIClient(); lc.force_authenticate(User.objects.get(username="leader"))
# 制造两个规范化后重复的订单号（V2.0 允许，V2.1 迁移需留痕处理）
lc.post("/api/order-confirmations/",{"project":1,"legal_entity":1,"basis":"CONTRACT","order_ref":"ht-001","confirm_now":True},format="json")
lc.post("/api/order-confirmations/",{"project":2,"legal_entity":1,"basis":"CONTRACT","order_ref":"HT-001 ","confirm_now":True},format="json")
print("V2.0 数据就绪：PDF sha", r["pdf_sha256"][:16])
PY
python3 manage.py verify_integrity | tail -1
# 2) 升级到当前代码
cd $R/backend
echo "== 升级计划"; python3 manage.py ish_upgrade --backup-dest $W/backups --plan-only | tail -2
echo "== 执行升级"; python3 manage.py ish_upgrade --backup-dest $W/backups 2>&1 | tail -4
python3 manage.py shell <<'PY' 2>&1 | grep -v imported
import hashlib
from apps.quotations.models import QuoteRevision
from apps.common.pdfkit import read_private
from apps.contracts.models import OrderConfirmation
r=QuoteRevision.objects.get(status="GENERATED")
sha=open("/tmp/v20_sha.txt").read()
print("旧报价 PDF 原字节哈希不变:", hashlib.sha256(read_private(r.pdf_storage_key)).hexdigest()==sha==r.pdf_sha256)
print("订单规范化:", list(OrderConfirmation.objects.order_by("id").values_list("order_ref","order_ref_norm")))
print("冲突留痕:", any("V2.1 迁移" in o.reason for o in OrderConfirmation.objects.all()))
PY
ls $W/backups
# 3) 模拟迁移失败：注入一个必失败的迁移 → 维护模式保持 → 按预备份回滚
mkdir -p $W/v21bad && cp -r $R/backend/. $W/v21bad/ && cat > $W/v21bad/apps/projects/migrations/0003_fail_drill.py <<'PY'
from django.db import migrations
def boom(apps, schema_editor):
    raise RuntimeError("演练：故意失败的迁移")
class Migration(migrations.Migration):
    dependencies = [("projects", "0002_executionmilestone_delay_reason")]
    operations = [migrations.RunSQL("ALTER TABLE projects_project ADD COLUMN drill_tmp int"), migrations.RunPython(boom)]
PY
cd $W/v21bad
set +e
python3 manage.py ish_upgrade --backup-dest $W/backups 2>&1 | tail -2
echo "升级失败退出码=$?"
set -e
echo "维护标志存在: $(test -f $W/data/MAINTENANCE && echo 是 || echo 否)"; cat $W/data/upgrade_status.json
$B/psql -h 127.0.0.1 -p 54329 -U ish -d ishupg -tAc "select count(*) from information_schema.columns where table_name='projects_project' and column_name='drill_tmp'" | sed 's/^/drill 列残留(事务回滚应为0): /'
# 回滚：用 pre-upgrade 备份恢复（单事务 pg_restore --clean）+ 私有文件；回到 V2.1 正常代码
PRE=$(ls -d $W/backups/ish-backup-*-pre-upgrade | tail -1)
cd $R/backend
python3 manage.py ish_verify_backup $PRE | tail -1
pg_restore -h 127.0.0.1 -p 54329 -U ish -d ishupg --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error $PRE/db.dump
rm -rf $W/data/private && cp -r $PRE/private $W/data/private && rm -f $W/data/MAINTENANCE
python3 manage.py verify_integrity --expect-manifest $PRE/manifest.json | tail -1
python3 manage.py showmigrations projects | tail -2
