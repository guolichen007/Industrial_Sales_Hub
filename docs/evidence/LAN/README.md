# V2.1-LAN 门禁证据矩阵

> 结论分级：**PASS** = 有可复现原始证据的实测通过；**PASS（沙箱）** = 在 Linux 沙箱中实测通过、未在甲方 Windows 现场复测；
> **静态通过** = 只做了语法/静态分析，未实际执行；**BLOCKED** = 缺外部条件，未执行；**未执行**。
> 任何未经真实运行的事项不标 PASS。**本轮不宣布生产交付完成。**

## 0. 执行环境与基线

| 项 | 值 |
|---|---|
| 分支 / 基线 | `feature/v2.1-lan-release`，自 `main` `4bab6f200069de9ff15c49564c91d291809b99ee` |
| 开发与验证环境 | Linux 沙箱 x86_64（2 核 / 3 GB），Python 3.10.12（测试）、PostgreSQL 16.2（pgserver 发行版）、Chromium headless（Playwright 1.65）、PowerShell 7.4.6 + PSScriptAnalyzer 1.22.0 |
| 甲方 Windows 主机 / 干净 VM | **不可用** |
| GitHub 远端 CI | 本轮未推送，**新 CI 未运行**；L0 记录了基线 run 44478523995（网页抓取，API 限流未逐 job 复核） |

## 1. 自动化测试（同一工作区代码，提交见 `git log`）

| 套件 | 命令 | 结果 |
|---|---|---|
| 后端 PostgreSQL 16 | `DATABASE_URL=postgres://… python -m pytest -q` | **210 passed / 0 failed / 0 skipped**（`L2/pytest-postgresql16.xml`） |
| 后端 SQLite（DEMO） | `python -m pytest -q` | 184 passed / 26 skipped（跳过的是需要 PostgreSQL 行锁或 pg_dump 的并发与备份测试） |
| 前端 | `npm run typecheck && npm run build` | strict 0 错误；构建成功；无外部 CDN 引用 |
| LAN 浏览器验收 | `e2e/e2e_v21_lan.py`（LAN 模式 Waitress + PostgreSQL + 主机局域网 IP + 真实账号） | **56/56 PASS**（`L3/e2e_v21_lan_results.json` + 11 张截图） |
| V2.0 基线浏览器脚本重跑 | `e2e/run_e2e.sh e2e/e2e_v20_baseline.py` | 23/23 PASS（L0，在 4bab6f2 代码上） |
| Windows 脚本 | PowerShell 解析器 + PSScriptAnalyzer（通用规则 + Windows PowerShell 5.1 / Server 2019 兼容规则） | 0 错误 / 0 告警（**静态**） |
| 依赖审计 | `pip-audit`（cp312 目标版本）、`npm audit --omit=dev` | 修复后 0 / 0 |
| 密钥扫描 | `gitleaks git`（全历史） | 0（测试 fixture 合成口令已白名单） |

测试分布：POC-A 报价 26、POC-B 项目关系 11、POC-C 权限 47、导入/附件 4、V2.1 权限与业务 78、V2.1 模板 19、V2.1 备份恢复 12、V2.1 并发 13。

## 2. 门禁矩阵（任务书 §6.1）

| ID | 场景 | 结论 | 证据 / 说明 |
|---|---|---|---|
| LAN-01 | Windows 干净 x64 VM 解压正式 ZIP 首次安装 | **BLOCKED（无 Windows 环境）** | 已完成：ZIP 构建、6458 个文件 SHA256 复核、包内无测试/数据库/密钥/演示口令、Windows wheel 全为 cp312-win_amd64、包内依赖在 Linux 上以 LAN 模式启动并登录成功（`L1/README.md`）。安装脚本仅静态通过 |
| LAN-02 | 重复安装/启动、重启、关闭控制台与浏览器 | **BLOCKED** | 幂等逻辑已实现（已有 config 时沿用端口、不重生成密钥、服务存在不重复创建、管理员存在跳过）；未实机验证 |
| LAN-03 | 两台独立电脑局域网访问，互联网可断开 | **PASS（沙箱，模拟）/ 现场 BLOCKED** | 用主机局域网 IP 与两个独立浏览器上下文验证：财务更新节点后销售在 1.6 s 内看到；前端无外部依赖。**不等于两台独立电脑**，断网未实测 |
| LAN-04 | 五角色同时操作；原 88 项与新增测试全过；5 线程并发无 500/重复业绩/半状态 | **PASS（沙箱）** | 210/210；barrier 并发：生成与编辑竞态 6 轮、并发新版本、同幂等键 ×5、同订单号 ×5、负责人确认 ×5、五角色混合、并发降级管理员、PDF 失败不挂起。L0 竞态在 4bab6f2 上 1/12 复现，修复后 0/30 |
| LAN-05 | 越权/匿名/金额泄露；生产无 DEBUG/默认密钥/演示密码；HTTPS 或 HTTP 例外 | **PASS（沙箱）；HTTPS 现场 BLOCKED** | 权限矩阵 125 项测试 + 浏览器反例；LAN 模式拒绝 DEBUG/开发密钥/SQLite/`*`/seed_demo；`check --deploy --fail-level WARNING` 通过；伪造 `X-Forwarded-Proto` 不生效；数据库仅监听 127.0.0.1。当前为“隔离内网 HTTP 试用”，正式 HTTPS 需甲方提供证书/代理 |
| LAN-06 | 三公司真实模板、长报价、历史版本 | **BLOCKED（真实样张与财务口径未提供）** | 工程部分 PASS：三模板短/长清单（≥3 页）、页码、大写、特殊字符、NET/GROSS、乘数、参考价；冻结版本下载/预览返回原字节；升级后旧 PDF 哈希不变 |
| LAN-07 | 完整业务链 | **PASS（沙箱）** | 浏览器：无客户项目 → 3 个参与方 → 两家公司/客户报价 → V2 → 财务确认（无金额）→ 执行节点延期 → 业绩 → 销售生成公开案例 PDF |
| LAN-08 | 正式空库、历史导入、备份恢复、坏备份 | **PASS（沙箱）** | LAN 空库无演示账号/客户；400 条合成业绩导入幂等；并发写入下一致性备份；恢复到临时库完整性一致；6 种坏备份拒绝；拒绝恢复到正式目标 |
| LAN-09 | 升级、迁移失败与恢复 | **PASS（沙箱，命令层）/ Windows 脚本 BLOCKED** | V2.0 → V2.1：5 个迁移、旧 PDF 原字节不变、历史重复订单号留痕；注入失败迁移：维护模式保持、事务无残留、按 pre-upgrade 备份恢复后完整性通过（`L4/`） |
| LAN-10 | 前端与性能 | **PASS（沙箱）** | 5 个用户上下文混合浏览；216 个 API 请求 P50 16 ms、P95 172 ms、最大 197 ms；PDF P95 228 ms；0 个 5xx；无 JS 错误；1366/1920 桌面与 390 px 手机截图。设备：沙箱 2 核 3 GB，**非目标主机** |
| LAN-11 | 安装异常（端口占用、中文路径、无管理员、断网、数据库故障、中断、磁盘不足） | **部分 PASS / 其余 BLOCKED** | 已实测：备份磁盘不足拒绝、备份失败保留上一个并解除维护、迁移失败保持只读。脚本已实现但未实机：端口占用、非 ASCII 路径拒绝（PostgreSQL 限制，已在手册说明）、非管理员、同步盘、包哈希不符、服务启动失败提示 |
| LAN-12 | 同 SHA 发布证据 | **未完成** | CI 已扩展（backend PG / frontend / e2e / windows-scripts / security / manifest），**尚未在 GitHub 运行**。本地 ZIP 由固定提交 `git archive` 构建，`VERSION.txt` 记录 SHA |

## 3. L0 发现的关闭情况

L0 反例探针在修复后复测（`L0/probes/probe_and_race_after_L2.txt`）：财务写金额 403、读金额否、财务台账 404、销售 quote_count 否、销售预计规模否、管理员读报价 403、最后管理员自降级 409、非数字过滤 400、登录数组体 400、竞态 0/30。

两轮独立复审（第一轮 38 项、第二轮 backend 19 项 + Windows 脚本 19 项）的 P0/P1 已修复并加回归测试；未采纳/保留项见 `../../剩余问题.md`。
