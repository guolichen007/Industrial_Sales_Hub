#!/bin/bash
# 升级器/恢复脚本 Linux 模拟验收（真实 pwsh 执行 upgrade.ps1/restore.ps1/backup.ps1；真实 PostgreSQL 16、真实文件移动、
# 真实 Waitress 应用；Windows 服务由 simhook.sh 模拟，ACL/防火墙/服务注册不在此验证 —— 必须 Windows 实机验证）。
# 在 withpg.sh 内运行：bash withpg.sh bash sim/run_sim.sh <输出日志目录> [场景...]
set -u
O=${ISH_SIM_TOOLS:?设置 ISH_SIM_TOOLS 为含 env/venv、env/pwsh 的目录}
SIM=/dev/shm/ishsim
EV=${1:?evidence dir}; shift
SCEN=${*:-all}
mkdir -p "$EV"
export HOME=$O/tmp/pshome DOTNET_CLI_HOME=$O/tmp/pshome
PWSH=$O/env/pwsh/pwsh
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export NO_PROXY='*'
PGPASS_SAVE=$PGPASSWORD
pass=0; fail=0
RES=$EV/results.txt; : > $RES

note() { echo "$*" | tee -a $RES; }
check() { if [[ $1 == 0 ]]; then pass=$((pass+1)); note "PASS $2"; else fail=$((fail+1)); note "FAIL $2 ${3:-}"; fi; }

pkg() { bash $(dirname "$0")/make_pkg.sh "$1" "$2" "$3" >/dev/null; }

clean_env() { env -u DATABASE_URL -u DATA_DIR -u PGDATABASE -u PGUSER -u PGHOST -u PGPASSWORD -u ISH_MODE -u DJANGO_SECRET_KEY -u DJANGO_ALLOWED_HOSTS -u ISH_LISTEN -u ISH_OPS_TOKEN "$@"; }

manage() { # manage <inst> args...
  local inst=$1; shift
  ( cd $inst/app/backend && clean_env ISH_CONFIG_FILE=$inst/config/app.env $inst/runtime/python/python.exe -X utf8 -B manage.py "$@" )
}
fp() { manage $1 shell < $(dirname "$0")/fingerprint.py 2>/dev/null | sed -n 's/^FINGERPRINT=//p'; }
relcheck() { ( cd $1 && sha256sum --quiet -c SHA256SUMS.txt >/dev/null 2>&1 ) ; }
health() { curl -s --noproxy '*' --max-time 5 http://127.0.0.1:8100/healthz; }
hver() { health | python3 -c "import sys,json;print(json.load(sys.stdin)['version'])" 2>/dev/null; }

ps_run() { # ps_run <script> args...  （模拟钩子 + 清理环境）
  local s=$1; shift
  clean_env ISH_SIM_HOOK=$(cd "$(dirname "$0")" && pwd)/simhook.sh ISH_SIM_MARGIN_MB=50 $PWSH -NoProfile -File "$s" "$@"
}

install_old() { # install_old <pkgdir> <dbname>
  local pkgdir=$1 db=$2 inst=$SIM/inst
  bash $(dirname "$0")/simhook.sh stop IndustrialSalesHub-App $inst >/dev/null 2>&1
  killsim
  rm -rf $inst; cp -a $pkgdir $inst
  mkdir -p $inst/config $inst/data/pgdata $inst/backups $inst/logs $inst/services
  echo 16 > $inst/data/pgdata/PG_VERSION
  touch $inst/data/ALLOW_SIMULATION $inst/data/ALLOW_FAULT_INJECTION
  PGPASSWORD=$PGPASS_SAVE psql -h 127.0.0.1 -U ish -d postgres -qc "DROP DATABASE IF EXISTS $db" 2>/dev/null
  for d in $(PGPASSWORD=$PGPASS_SAVE psql -h 127.0.0.1 -U ish -d postgres -tAc "SELECT datname FROM pg_database WHERE datname LIKE '${db}_%'"); do PGPASSWORD=$PGPASS_SAVE psql -h 127.0.0.1 -U ish -d postgres -qc "DROP DATABASE IF EXISTS $d"; done
  PGPASSWORD=$PGPASS_SAVE createdb -h 127.0.0.1 -U ish $db
  cat > $inst/config/app.env <<EOF
ISH_MODE=LAN
DJANGO_SECRET_KEY=sim-$(python3 -c "import secrets;print(secrets.token_urlsafe(60))")
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DATABASE_URL=postgres://ish:ish@127.0.0.1:5432/$db
ISH_DB_PORT=5432
DATA_DIR=$inst/data
ISH_LOG_DIR=$inst/logs
FRONTEND_DIST=$inst/app/frontend/dist
ISH_LISTEN=127.0.0.1:8100
ISH_THREADS=4
ISH_HTTPS=0
ISH_BACKUP_DIR=$inst/backups
ISH_BACKUP_KEEP_DAYS=30
ISH_BACKUP_COPY_TO=
EOF
  manage $inst migrate --noinput -v0
  manage $inst seed_entities $inst/config-templates/entities.json >/dev/null
  manage $inst shell < $(dirname "$0")/seed_business.py >/dev/null
  manage $inst shell < $(dirname "$0")/seed_business.py >/dev/null
  bash $(dirname "$0")/simhook.sh start IndustrialSalesHub-App $inst
  for i in $(seq 1 40); do [[ -n "$(health)" ]] && break; sleep 0.5; done
}

killsim() { for p in $(ls /proc | grep -E '^[0-9]+$'); do grep -qa "serve.py" /proc/$p/cmdline 2>/dev/null && grep -qa "ishsim" /proc/$p/environ 2>/dev/null && kill $p 2>/dev/null; done; rm -f /dev/shm/ishsim-app.pid; sleep 0.5; }
killsim
mkdir -p $SIM
echo "== 构造安装包"
pkg 875ef0c $SIM/pkg-rc1 2.1.0-rc1
pkg WORKTREE $SIM/pkg-rc2 2.1.0-rc2
# rc3sim：在 RC2 基础上加一个真实迁移（创建 sim_upgrade_marker 表），用于“迁移成功后失败”的数据库回滚
pkg WORKTREE $SIM/pkg-rc3 2.1.0-rc3
cat > $SIM/pkg-rc3/app/backend/apps/audit/migrations/0099_sim_marker.py <<'EOF'
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL("CREATE TABLE sim_upgrade_marker(id int)", "DROP TABLE sim_upgrade_marker")]
EOF
sed -i 's/^APP_VERSION = .*/APP_VERSION = "2.1.0-rc3"/' $SIM/pkg-rc3/app/backend/config/settings.py
( cd $SIM/pkg-rc3 && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum | sed 's#  \./#  #' > SHA256SUMS.txt )
cp -a $SIM/pkg-rc3 $SIM/pkg-rc3-bad
sed -i 's/CREATE TABLE sim_upgrade_marker(id int)/CREATE TABLE sim_upgrade_marker(id int); SELECT 1\/0/' $SIM/pkg-rc3-bad/app/backend/apps/audit/migrations/0099_sim_marker.py
( cd $SIM/pkg-rc3-bad && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum | sed 's#  \./#  #' > SHA256SUMS.txt )
cp -a $SIM/pkg-rc2 $SIM/pkg-rc2-tampered; echo "x" >> $SIM/pkg-rc2-tampered/app/backend/manage.py
INST=$SIM/inst

want() { [[ $SCEN == all || " $SCEN " == *" $1 "* ]]; }

# ---------------------------------------------------------------- 1. RC1 → RC2 成功升级
if want ok; then
note "## 场景 ok：RC1(875ef0c) → RC2 升级成功"
install_old $SIM/pkg-rc1 ishsim
B=$(fp $INST); check $([[ $(hver) == 2.1.0-rc1 ]]; echo $?) "升级前应用版本 rc1"
ps_run $SIM/pkg-rc2/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/ok.log 2>&1; rc=$?
cp $INST/logs/sim-services.log $EV/ok.services.log 2>/dev/null; tail -30 $INST/logs/sim-app.log > $EV/ok.app.log 2>/dev/null
check $rc "upgrade.ps1 退出码 0" "rc=$rc"
check $([[ $(hver) == 2.1.0-rc2 ]]; echo $?) "升级后 /healthz 版本 2.1.0-rc2"
relcheck $INST; check $? "安装目录发行文件与 RC2 清单逐文件一致"
cmp -s $INST/SHA256SUMS.txt $SIM/pkg-rc2/SHA256SUMS.txt; check $? "SHA256SUMS.txt = 新包清单"
A=$(fp $INST); check $([[ "$A" == "$B" ]]; echo $?) "业务数据/PDF/附件哈希升级前后一致"
ls -d $INST/backups/ish-backup-*-pre-upgrade-* >/dev/null 2>&1; check $? "存在 pre-upgrade 备份（旧程序生成）"
grep -q '"state": *"DONE"' $INST/data/ops/upgrade-*.json; check $? "升级日志状态 DONE"
check $([[ ! -f $INST/data/MAINTENANCE ]]; echo $?) "维护标记已解除"
UID1=$(python3 -c "import json,glob;print(json.load(open(sorted(glob.glob('$INST/data/ops/upgrade-*.json'))[-1]))['id'])")
# 同版本不加 -Repair 拒绝；降级拒绝
ps_run $SIM/pkg-rc2/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/same.log 2>&1; check $([[ $? == 52 ]]; echo $?) "同版本无 -Repair 被拒绝(52)"
ps_run $SIM/pkg-rc1/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/down.log 2>&1; rc=$?; check $([[ $rc != 0 ]]; echo $?) "降级被拒绝（RC1 包的旧升级器或新规则）rc=$rc"
# 已完成升级的回滚（从安装目录运行，自动复制到临时目录执行）
ps_run $INST/scripts/upgrade.ps1 -Rollback $UID1 -Yes > $EV/ok-rollback.log 2>&1; rc=$?
check $rc "已完成升级可按日志回滚（退出码 0）" "rc=$rc"
check $([[ $(hver) == 2.1.0-rc1 ]]; echo $?) "回滚后版本 rc1"
relcheck $INST; check $? "回滚后发行文件与 RC1 清单逐文件一致"
cmp -s $INST/SHA256SUMS.txt $SIM/pkg-rc1/SHA256SUMS.txt; check $? "回滚后 SHA256SUMS.txt = RC1 清单"
check $([[ "$(fp $INST)" == "$B" ]]; echo $?) "回滚后数据与升级前一致"
fi

# ---------------------------------------------------------------- 2. 各阶段故障 → 自动回滚
for stage in upgrade.after_backup upgrade.stage_copy upgrade.switch_half upgrade.migrate upgrade.verify; do
  want fault || continue
  note "## 场景 fault：RC1 → RC2 在 $stage 注入失败"
  install_old $SIM/pkg-rc1 ishsim
  B=$(fp $INST)
  ISH_FAULT_INJECT=$stage ps_run $SIM/pkg-rc2/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/fault-$stage.log 2>&1; rc=$?
  check $([[ $rc == 56 ]]; echo $?) "$stage：升级失败并自动回滚（退出码 56）" "rc=$rc"
  check $([[ $(hver) == 2.1.0-rc1 ]]; echo $?) "$stage：旧版本服务恢复"
  relcheck $INST; check $? "$stage：发行文件 = RC1 清单"
  check $([[ "$(fp $INST)" == "$B" ]]; echo $?) "$stage：数据/文件/PDF 哈希不变"
  check $([[ ! -f $INST/data/MAINTENANCE ]]; echo $?) "$stage：维护标记已解除"
  left=$(PGPASSWORD=$PGPASS_SAVE psql -h 127.0.0.1 -U ish -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname LIKE 'ishsim_preupg_%'")
  check $([[ $left == 0 ]]; echo $?) "$stage：未遗留数据库快照（left=$left）"
done

# ---------------------------------------------------------------- 3. 迁移成功后失败 → 数据库切回快照
if want migrate; then
note "## 场景 migrate：RC2 → rc3sim（含真实迁移）"
install_old $SIM/pkg-rc2 ishsim
B=$(fp $INST)
ISH_FAULT_INJECT=upgrade.verify ps_run $SIM/pkg-rc3/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/migrate-then-fail.log 2>&1; rc=$?
check $([[ $rc == 56 ]]; echo $?) "迁移成功后 VERIFY 失败 → 回滚（56）" "rc=$rc"
A=$(fp $INST); check $([[ "$A" == "$B" ]]; echo $?) "数据库切回升级前快照：无 sim_upgrade_marker、迁移列表与升级前相同"
check $([[ $(hver) == 2.1.0-rc2 ]]; echo $?) "回滚后版本 rc2"
fdb=$(PGPASSWORD=$PGPASS_SAVE psql -h 127.0.0.1 -U ish -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname LIKE 'ishsim_failed_%'")
check $([[ $fdb == 1 ]]; echo $?) "迁移后的库保留为 _failed_ 供分析"
ps_run $SIM/pkg-rc3-bad/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/migrate-bad.log 2>&1; rc=$?
check $([[ $rc == 56 ]]; echo $?) "迁移本身失败 → 回滚（56）" "rc=$rc"
check $([[ "$(fp $INST)" == "$B" ]]; echo $?) "迁移失败回滚后数据不变"
ps_run $SIM/pkg-rc3/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/migrate-ok.log 2>&1; rc=$?
check $rc "RC2 → rc3sim 成功" "rc=$rc"
fp $INST | grep -q '"sim_marker_table": true'; check $? "新迁移已应用"
UID3=$(python3 -c "import json,glob;print(json.load(open(sorted(glob.glob('$INST/data/ops/upgrade-*.json'))[-1]))['id'])")
ps_run $INST/scripts/upgrade.ps1 -Cleanup $UID3 > $EV/cleanup.log 2>&1; check $? "-Cleanup 删除旧版本与快照"
check $([[ ! -d $INST/_upgrade/$UID3 ]]; echo $?) "旧版本目录已删除"
fi

# ---------------------------------------------------------------- 4. 断电（进程被杀）→ 从新包执行 -Rollback
if want crash; then
note "## 场景 crash：SWITCH 中途进程被杀（模拟断电）"
install_old $SIM/pkg-rc1 ishsim
B=$(fp $INST)
ISH_FAULT_CRASH=upgrade.switch_half ps_run $SIM/pkg-rc2/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/crash.log 2>&1; rc=$?
check $([[ $rc == 99 ]]; echo $?) "进程在 SWITCH 中途终止（99）" "rc=$rc"
check $([[ -f $INST/data/MAINTENANCE ]]; echo $?) "崩溃后维护标记保留（系统只读）"
check $([[ ! -d $INST/scripts ]]; echo $?) "崩溃后安装目录处于半切换状态（scripts 已移出）"
CID=$(python3 -c "import json,glob;print(json.load(open(sorted(glob.glob('$INST/data/ops/upgrade-*.json'))[-1]))['id'])")
ps_run $SIM/pkg-rc2/scripts/upgrade.ps1 -InstallDir $INST -Rollback $CID -Yes > $EV/crash-rollback.log 2>&1; rc=$?
check $rc "从新包目录执行 -Rollback 成功" "rc=$rc"
relcheck $INST; check $? "发行文件恢复为 RC1 清单"
check $([[ "$(fp $INST)" == "$B" ]]; echo $?) "数据不变"
check $([[ $(hver) == 2.1.0-rc1 ]]; echo $?) "旧版本服务恢复"
check $([[ ! -f $INST/data/MAINTENANCE ]]; echo $?) "维护标记已解除"
fi

# ---------------------------------------------------------------- 5. 篡改的新包、遇忙
if want guard; then
note "## 场景 guard：篡改安装包、另一运维任务进行中"
install_old $SIM/pkg-rc1 ishsim
B=$(fp $INST)
ps_run $SIM/pkg-rc2-tampered/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/tampered.log 2>&1; rc=$?
check $([[ $rc == 51 ]]; echo $?) "篡改的新包在任何改动前被拒绝（51）" "rc=$rc"
check $([[ "$(fp $INST)" == "$B" && $(hver) == 2.1.0-rc1 ]]; echo $?) "拒绝后系统未变化"
( exec 9>>$INST/data/ops.lock; flock -x 9; echo '{"task":"backup","token":"t","pid":1,"started_at":"sim"}' > $INST/data/ops.owner.json; sleep 25 ) &
LP=$!; sleep 1
ps_run $SIM/pkg-rc2/scripts/upgrade.ps1 -InstallDir $INST -Yes > $EV/busy.log 2>&1; rc=$?
check $([[ $rc == 75 ]]; echo $?) "其他运维任务持锁时升级遇忙退出（75）" "rc=$rc"
ps_run $INST/scripts/backup.ps1 > $EV/busy-backup-rc1.log 2>&1; rc=$?
note "RC1 反例（记录，不计入）：另一运维任务持锁时 RC1 backup.ps1 仍执行，rc=$rc（0=未互斥）"
kill $LP 2>/dev/null; wait $LP 2>/dev/null
install_old $SIM/pkg-rc2 ishsim
( exec 9>>$INST/data/ops.lock; flock -x 9; echo '{"task":"upgrade","token":"t","pid":1,"started_at":"sim"}' > $INST/data/ops.owner.json; sleep 20 ) &
LP=$!; sleep 1
ps_run $INST/scripts/backup.ps1 > $EV/busy-backup-rc2.log 2>&1; rc=$?
check $([[ $rc == 75 ]]; echo $?) "RC2：另一运维任务持锁时 backup.ps1 遇忙退出（75）" "rc=$rc"
check $([[ ! -f $INST/data/backup_status.json ]] || ! grep -q '"ok": false' $INST/data/backup_status.json; echo $?) "RC2：遇忙不写失败状态"
ps_run $INST/scripts/restore.ps1 -BackupPath $INST/backups -Production -Yes > $EV/busy-restore-rc2.log 2>&1; rc=$?
check $([[ $rc != 0 ]]; echo $?) "RC2：持锁时正式恢复不执行（rc=$rc）"
kill $LP 2>/dev/null; wait $LP 2>/dev/null
fi

# ---------------------------------------------------------------- 6. RC2 安装上的备份/恢复脚本（真实 pwsh）
if want restore; then
note "## 场景 restore：RC2 安装上的 backup.ps1 / restore.ps1（演练、正式、失败回退、撤销）"
install_old $SIM/pkg-rc2 ishsim
ps_run $INST/scripts/backup.ps1 > $EV/r-backup.log 2>&1; check $? "backup.ps1 成功"
BK=$(ls -d $INST/backups/ish-backup-* | tail -1)
B=$(fp $INST)
manage $INST shell < $(dirname "$0")/seed_business.py >/dev/null
check $([[ "$(fp $INST)" != "$B" ]]; echo $?) "备份后写入了新业务数据"
ps_run $INST/scripts/restore.ps1 -BackupPath $BK > $EV/r-drill.log 2>&1; check $? "恢复演练（不影响正式数据）"
M=$(fp $INST)
ISH_FAULT_INJECT=restore.switch_files ps_run $INST/scripts/restore.ps1 -BackupPath $BK -Production -Yes > $EV/r-fault.log 2>&1; rc=$?
check $([[ $rc == 40 ]]; echo $?) "正式恢复切换中失败 → 自动回退（40）" "rc=$rc"
check $([[ "$(fp $INST)" == "$M" ]]; echo $?) "回退后数据 = 恢复前"
check $([[ -n "$(health)" ]]; echo $?) "回退后应用已重新启动"
ISH_FAULT_INJECT=restore.ps1.start ps_run $INST/scripts/restore.ps1 -BackupPath $BK -Production -Yes > $EV/r-fault-start.log 2>&1; rc=$?
check $([[ $rc == 41 ]]; echo $?) "恢复成功但启动失败 → 按日志撤销（41）" "rc=$rc"
check $([[ "$(fp $INST)" == "$M" ]]; echo $?) "撤销后数据 = 恢复前"
ps_run $INST/scripts/restore.ps1 -BackupPath $BK -Production -Yes > $EV/r-prod.log 2>&1; check $? "正式恢复成功"
check $([[ "$(fp $INST)" == "$B" ]]; echo $?) "正式恢复后数据 = 备份时刻"
check $([[ -n "$(health)" ]]; echo $?) "恢复后应用运行"
RID=$(python3 -c "import json,glob;print(json.load(open(sorted(glob.glob('$INST/data/ops/restore-*.json'))[-1]))['id'])")
ps_run $INST/scripts/restore.ps1 -Rollback $RID -Yes > $EV/r-undo.log 2>&1; check $? "撤销已完成的恢复"
check $([[ "$(fp $INST)" == "$M" ]]; echo $?) "撤销后数据 = 恢复前"
fi

bash $(dirname "$0")/simhook.sh stop IndustrialSalesHub-App $INST >/dev/null 2>&1
note "== 模拟验收：$pass PASS / $fail FAIL"
[[ $fail == 0 ]]
