# RC2 证据与门禁矩阵（2.1.0-rc2）

> 每项证据注明**真实执行环境**。四类结论分开记录，不混用：**Linux PASS**（开发沙箱实测）、**Windows 静态 PASS**（解析/PSScriptAnalyzer）、
> **Windows 实测 PASS**（需按 `docs/RC2-Windows实机验收手册.md` 在测试机执行）、**现场 PASS**（甲方环境）。
> 开发沙箱：Linux x86_64、2 核、Python 3.12.16、PostgreSQL 16（pgserver 发行版）、Chromium headless（Playwright 1.58.0）、PowerShell 7.4.6 + PSScriptAnalyzer 1.22.0。

## 1. R0 远端 CI 基线

| 项 | 内容 |
|---|---|
| 复核对象 | Run 44651289479 Attempt 1，SHA 875ef0c9a9c9df0d0718d543ae80a604e2836172 |
| 实际结果（GitHub API） | windows `success`、frontend `success`、backend (PostgreSQL 16) `success`、dependency audit + secret scan `success`、**e2e job 136006702458 `failure`**（步骤 6 安装依赖与 Chromium 失败，后续步骤 skipped）、**artifact manifest `skipped`** |
| 原因 | `runs-on: ubuntu-latest` 已滚动到 26.04，Playwright 1.58 不支持 chromium on ubuntu26.04-x64 |
| 修复 | 所有 Linux job 固定 `ubuntu-24.04`，Windows 固定 `windows-2022`；Playwright 版本唯一来源 `e2e/requirements.txt`；pip-audit 不再 `|| echo warning`；manifest 依赖 security；E2E 失败时保留截图与服务日志 |
| 本地复跑 | 875ef0c 基线：后端 210/210（PostgreSQL 16）；浏览器 56/56（使用新构建前端；本地旧 dist 过期会导致管理员首页崩溃——CI 每次构建前端不受影响） |
| 远端 | **待负责人推送后复核**：同一 final SHA、同一 Attempt 六个 job 全 success（见交付目录《CI证据说明》） |

## 2. 反例 → 修复（Linux PASS，PostgreSQL 16 真实执行）

| 编号 | 变更前反例（证据） | 变更后 |
|---|---|---|
| R01 | `counterexamples/R01-before.txt`：4 失败（seed 覆盖已有主体与签收、无授权导入命令） | `test_rc2_r01_entities.py` 6/6（含“重复安装序列”逐表逐行与私有文件哈希不变） |
| R02/R05 | `R02-R05-before.txt`：8 失败（空备份无 private、无运维锁、他人维护标记被删、无忙退出） | `test_rc2_r05_ops_lock.py` 8/8（跨进程锁、崩溃释放、并发备份/升级/恢复不乱删标记） |
| R04 | `R04-before-rc1-sequence.txt`：按 RC1 顺序先 `pg_restore --clean` 正式库、再复制文件中断 → 数据库已覆盖、private 不存在；`--clean` 不删除 dump 外的新表 | `test_rc2_r04_restore.py` 17/17（有业务/空库/大附件/缺附件/6 阶段故障/哈希不一致/权限拒绝/磁盘不足/新旧 schema/遇忙/撤销/清理） |
| R03/R06 | RC1 `upgrade.ps1` 先替换 app/runtime 再由新程序备份；回滚只覆盖 app/runtime | Linux 模拟验收（PowerShell 7 真实执行 `upgrade.ps1`）83/83，见第 3 节 |
| R2 | RC1 `template_verified` 任意配置角色可勾选、服务端不检查；未签收 PDF 与正式无区别 | `test_rc2_templates.py` 18/18；E2E 未签收 409、试用不可发送、商务不可签收 |
| R3 | 附件无分型、报价/合同文件只能放项目附件、无销售授权通道；首期缺口 8 项 | `test_rc2_r3_scope_features.py` 17/17；E2E 两个独立销售账号验证授权/直链/报价附件/导出列 |
| 安全 | 登录请求未校验 CSRF（DRF 对未认证请求豁免） | `test_rc2_tls_settings.py` 6/6；登录强制 CSRF |
| 独立复审 | 另一个审查代理对恢复/升级/附件/试用报价做只读审查，提出 3 个 P0、7 个 P1（`counterexamples/review-fixes-before.txt`：8 失败） | `test_rc2_review_fixes.py` 9/9，见第 2.1 节 |

### 2.1 独立复审发现与处理

| 级别 | 发现 | 处理 |
|---|---|---|
| P0 | 恢复前安全备份按 30 天清理，可能删除正要恢复的旧备份 | 安全备份 `--no-prune`；清理永不删除 before-restore/pre-upgrade 备份；升级器调用旧程序时用超长保留天数 |
| P0 | 撤销“已完成”的恢复会删除恢复之后录入的数据 | 改为改名保留为 `<库>_undone_<ID>` 与 `private.undone-<ID>` |
| P0 | 可编辑项目的销售能看到商务上传的“内部”项目附件 | 销售只能看到售前资料与已授权的交付附件（列表、下载、删除一致） |
| P1 | 日志在动作之后记录；撤销不可重试 | 意图先于动作记录；撤销按实际存在性执行、可重复；回退后校验正式库存在 |
| P1 | restore.ps1 回退失败也启动应用 | 仅日志为 ROLLED_BACK 时启动，否则保持停止（退出码 42） |
| P1 | 清理后再回退会移走在用文件 | 已清理的恢复拒绝回退 |
| P1 | 升级回滚两次改名中途失败后无法重试 | 每次改名前检查存在性 |
| P1 | 父窗口被杀后子进程仍运行、锁已释放 | 子进程登记 PID；新任务发现存活子进程即判忙（Python 与 PowerShell 两侧） |
| P1 | 订单确认可把“最新为试用”的报价改为已接受 | 跳过 |
| P2 | 销售可撤回授权；财务可 PATCH 探测订单附件；非字符串类别 500；财务可关联报价；签收撤销与生成竞态；已签收时 trial=true 静默生成正式版 | 均已修复并有测试 |

## 3. 升级器与恢复脚本模拟验收（Linux PASS；**不是** Windows 实测）

`deploy/windows/tests/sim/run_sim.sh`：真实 `pwsh` 执行 `upgrade.ps1/restore.ps1/backup.ps1`，真实 PostgreSQL 16、真实文件移动、真实 Waitress；
Windows 服务由 `simhook.sh` 模拟，ACL/防火墙/计划任务跳过（仅当 `data\ALLOW_SIMULATION` 存在且设置 `ISH_SIM_HOOK`）。安装包由 `make_pkg.sh` 按发行结构构造（RC1 用 `git archive 875ef0c`）。

| 场景 | 结果 | 文件 |
|---|---|---|
| ok：RC1(875ef0c)→RC2 升级、5 个新迁移为纯增量、原有业务/PDF/附件哈希不变、pre-upgrade 备份由旧程序生成、同版本拒绝、降级拒绝、完成后 -Rollback 回到 RC1（发行文件与 RC1 清单逐文件一致） | 16/16 | `sim/sim-ok-results.txt` |
| fault：after_backup / stage_copy / switch_half / migrate / verify 注入失败 → 自动回滚（退出码 56）、旧版本服务、RC1 清单、数据不变、维护解除、无残留快照 | 30/30 | `sim/sim-fault-results.txt` |
| migrate：迁移成功后 VERIFY 失败 → 切回快照（迁移库保留为 _failed_）；迁移本身失败；成功后 -Cleanup；crash：SWITCH 中途进程被杀 → 维护保留 → 从新包 -Rollback | 18/18 | `sim/sim-migrate-results.txt` |
| guard：篡改新包在任何改动前拒绝（51）、他任务持锁时升级/备份遇忙（75）、遇忙不写失败状态、持锁时正式恢复不执行；restore：演练、切换中失败回退（40）、启动失败撤销（41）、正式恢复、撤销已完成恢复 | 19/19 | `sim/sim-guard-results.txt` |
| RC1 反例（记录不计入）：他任务持锁时 RC1 `backup.ps1` 照常执行（无互斥） | 记录 | 同上 |

## 4. 自动化测试汇总

| 套件 | RC1 | RC2 | 环境 |
|---|---|---|---|
| 后端 pytest（真实 PostgreSQL 16，含 pg_dump/pg_restore） | 210 | **292**（原 210 项全部保留；3 项因行为调整更新断言并在测试中写明原因：模板 C 列名、项目附件列表 200 空列表、登录 CSRF） | Linux PASS |
| 浏览器 E2E（Waitress + LAN 模式 + 真实账号，非演示数据） | 56 | **72**（`e2e/e2e_v21_lan_results.json`；0 个 5xx，无 JS 错误） | Linux PASS（单机多上下文，**不等于双机验收**） |
| PowerShell 5.1 语法 + PSScriptAnalyzer（CI 同规则） | 通过 | 通过（scripts、tools） | Windows 静态：本地 pwsh 7 PASS；远端 windows-2022 待复核 |
| 前端 typecheck + build + 无 CDN | 通过 | 通过 | Linux PASS |
| pip-audit（硬门禁） | 警告不阻断 | No known vulnerabilities | Linux PASS |

## 5. 未执行 / 外部阻断（不得记为 PASS）

Windows 11 实机：服务注册/开机自启/自动重启、ACL 与普通用户拒绝、计划任务 SYSTEM、空格路径完整安装、同名服务、磁盘不足、安装中断恢复、真实断电回滚；
Windows Server 2019+；两台设备断网五账号；HTTPS 内部 CA 与代理；模板负责人签收与财务黄金用例；远端同 SHA 同 Attempt CI。
