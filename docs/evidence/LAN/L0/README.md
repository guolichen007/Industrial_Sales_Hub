# L0 基线核验与事实审计

- 核验日期：2026-11-19
- 基线：`main` = `4bab6f200069de9ff15c49564c91d291809b99ee`（`git rev-parse origin/main` 与本地一致）
- 分支：`feature/v2.1-lan-release` 自该 SHA 创建
- 执行环境：Linux 沙箱（Python 3.10、PostgreSQL 16.2 本地实例、Chromium headless）。**不是**甲方 Windows 主机。

## 1. 远端 CI

| 项 | 结果 | 依据 |
|---|---|---|
| Run 44478523995 attempt 1 | backend success、frontend success，对应 SHA `4bab6f2…` | GitHub 网页 `actions/runs/44478523995` 抓取文本中出现 SHA 与 “completed successfully”。GitHub REST API 本次被限流（HTTP 403 rate limit），**未能用 API 逐 job 复核**，结论按网页文本记录 |
| Dependabot | 不计入业务 CI | — |

## 2. 在新分支上重跑既有测试（同一代码 = 4bab6f2）

| 套件 | 命令 | 结果 |
|---|---|---|
| 后端 PostgreSQL 16 | `DATABASE_URL=postgres://… python -m pytest -q` | **88 passed** |
| 后端 SQLite | `python -m pytest -q` | **86 passed, 2 skipped**（跳过的为需要行锁的并发测试） |
| 浏览器 E2E | `e2e/run_e2e.sh e2e/e2e_v20_baseline.py <out>` | **23/23 PASS**（`e2e_v20_rerun_results.json`） |

审查发现：V2.0 浏览器脚本此前只在开发者临时目录，**未纳入版本控制**。本轮已加入 `e2e/e2e_v20_baseline.py` 与单命令入口 `e2e/run_e2e.sh`。

## 3. 反例探针（在 4bab6f2 代码上执行，预期复现问题）

脚本：`probes/probe.py`、`probes/race.py`；原始输出：`probes/*_at_4bab6f2.json`。

| 编号 | 发现 | 实测 |
|---|---|---|
| F1 | 财务可通过订单接口写入合同金额（违反 D5） | 201，返回金额 123456.78 |
| F2 | 财务可读合同金额 | 是 |
| F3 | 独立财务台账仍可写（违反 D5） | 200 |
| S1 | 销售项目列表含 `quote_count`（违反 D2） | 是 |
| S2 | 销售可见/可写自由文本“预计规模” | 是 |
| A1 | 管理员默认可读全部报价（技术角色越权） | 200 |
| A2 | 最后一个管理员可把自己降级，系统失去管理员 | 200 |
| E2 | `GET /api/quotations/?project=abc` 未处理异常 | 500 ValueError |
| E3 | 登录接口提交 JSON 数组 | 500 AttributeError（匿名可触发） |
| R1 | render 与 add_line/PATCH 并发（barrier，同时起跑，12 轮） | **1/12 轮出现“已冻结版本的行数与快照不一致”** |

## 4. 源码审计结论（独立复审 38 项，摘要）

P0：财务读/写金额与台账（D5）；销售 quote_count（D2）；`SALES_SEE_ALL_PROJECTS` 会同时扩大业绩范围（D7）；报价修订 PATCH/行编辑与生成竞态；`DEBUG=0` 强制 Secure Cookie 导致 HTTP 局域网无法登录；管理员默认拥有全部业务敏感权限。
P1：预计规模自由文本；`SALES_QUOTE_SUMMARY` 开关与 D2 冲突；附件权限跟随项目可见性且不校验归属对象存在；订单幂等键与订单号并发插入 500；`confirm_now` 非原子；最后管理员保护缺失；HSTS 与代理头无条件信任；默认 SQLite 下行锁无效。
P2：销售可改成员/直接标“成交”；整对象保存造成丢失更新；生成失败 `last_error` 被回滚；业绩撤销与订单状态不一致；多处非数字参数、非对象 JSON、坏 xlsx、缺失文件导致 500；审计遮蔽不全；登录限速基于进程内缓存。

全部条目在 L2 逐项修复并以测试关闭，见 `../L2/README.md`。

## 5. 其他核查

- `启动-Windows本机试用.bat`：只监听 127.0.0.1、SQLite、在线 npm 构建、每次 seed_demo —— 属于 DEMO，保留为 DEMO，正式入口独立实现（L1）。
- 设置：`DEBUG` 默认 True；非 DEBUG 强制 Secure Cookie + HSTS + 信任代理头 —— 不能直接用于 HTTP 局域网（L1 修复为 DEMO/LAN/CLOUD 模式）。
- 备份脚本 `infra/scripts/backup.sh`：顺序 pg_dump 后打包文件，无写入静默，且基于 Docker/Linux（L4 重做）。
- 前端：侧栏文案“云端数据 · 实时共享”（L3 改为“局域网 · 共享数据”）；Execution 合同表单按 `performance.see_amount` 对财务显示金额（L2/L3 修复）。
- 公共仓库历史扫描（`git log --all -p` 搜索密钥/私钥/令牌模式）：仅发现 `.env.example` 中的占位文本与开发默认密钥 `dev-only-insecure-key-change-me`（仅 DEBUG 可用）。演示密码 `Demo@2026` 出现在 README 与 seed_demo（仅演示数据）。**未发现真实客户资料、真实密钥或生产备份**。处理：LAN 模式拒绝开发密钥与演示账号（L1/L2）；无需改写历史。
- 三份真实报价样张：**本地与仓库中均不存在**（工作区仅有设计包；文件名仅出现在任务书）。L2 模板验收因此 BLOCKED。
