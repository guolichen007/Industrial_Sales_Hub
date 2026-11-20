# 升级器/恢复脚本 Linux 模拟验收（RC2）

真实执行 `scripts/upgrade.ps1`、`restore.ps1`、`backup.ps1`（PowerShell 7），真实 PostgreSQL 16、真实文件移动与
真实 Waitress 应用；**Windows 服务、ACL、防火墙、计划任务由 `simhook.sh` 模拟或跳过**，因此本验收**不能替代 Windows 实机验收**
（见 `docs/RC2-Windows实机验收手册.md`）。模拟只在 `data\ALLOW_SIMULATION` 存在且设置 `ISH_SIM_HOOK` 时生效，正式主机不受影响。

场景：`ok`（RC1→RC2 升级、同版本拒绝、降级拒绝、完成后回滚）、`fault`（5 个阶段注入失败自动回滚）、
`migrate`（真实迁移后失败→数据库切回快照；迁移本身失败；成功后 -Cleanup）、`crash`（SWITCH 中途进程被杀→-Rollback）、
`guard`（篡改包、遇忙退出、RC1 无互斥反例）、`restore`（演练、正式恢复、切换中失败、启动失败撤销、撤销已完成恢复）。

```bash
ISH_SIM_TOOLS=<含 env/venv(python3.12+依赖) env/pwsh env/venv/.../pgserver> \
  bash <启动 PostgreSQL 并设置 PGPASSWORD 的包装> bash deploy/windows/tests/sim/run_sim.sh <证据目录> [场景...]
```
