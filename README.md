# Industrial Sales Hub · 工业销售管理系统 V2.1

[![ci](https://github.com/guolichen007/Industrial_Sales_Hub/actions/workflows/ci.yml/badge.svg)](https://github.com/guolichen007/Industrial_Sales_Hub/actions/workflows/ci.yml)
![Django 5.2 LTS](https://img.shields.io/badge/Django-5.2_LTS-0C4B33) ![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791) ![React 18](https://img.shields.io/badge/React-18-61DAFB) ![LAN](https://img.shields.io/badge/部署-Windows_局域网-2b6cb0) ![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6)

面向 1～5 人小型工业设备销售团队的轻量级业务系统：
**客户与项目 → 商务报价（三公司固定模板）→ 合同/订单确认 → 项目执行进度 → 内部业绩 → 对外案例 PDF**。

> **当前版本 2.1.0-rc3（两板块与现场验收候选，非正式版）**：首期部署方式为**一台 Windows 主机集中安装、局域网内其他电脑用浏览器访问**，
> 数据库为本机 PostgreSQL，不上云、不需要公网域名。RC3 把业务收敛为**商务工作区 / 销售工作区**两个入口：中标确认后销售只读、询价登记、
> 生产交付与资金两条状态线（不含金额）、内部业绩 PDF、字段级审计；安装器保留手工 HTTPS 规则、支持 VC++ 重启续装与多网卡选择。
> 门禁状态见 [`docs/evidence/RC3/README.md`](docs/evidence/RC3/README.md)；**模板负责人签收、财务黄金用例、Windows 实机、双机断网 UAT、HTTPS、远端同 SHA CI 仍为外部阻断**，
> 见 [`docs/剩余问题.md`](docs/剩余问题.md)。在这些完成前不发布正式版 `v2.1.0`、不合并 main。

## 文档入口

| 文档 | 用途 |
|---|---|
| [`docs/RC3-用户操作手册.md`](docs/RC3-用户操作手册.md) | 两板块日常业务操作（销售、商务、财务、负责人） |
| [`docs/RC3-业务角色与字段矩阵.md`](docs/RC3-业务角色与字段矩阵.md) | 业务口径、角色请求矩阵（CI 验证）、字段级规则 |
| [`docs/RC3-Windows实机验收手册.md`](docs/RC3-Windows实机验收手册.md) | RC3 增补 T17–T21 与两机 UAT U1–U10 |
| [`docs/LAN安装使用手册.md`](docs/LAN安装使用手册.md) | Windows 主机从零安装、首次使用、日常运维、HTTPS、常见问题 |
| [`docs/备份恢复与升级手册.md`](docs/备份恢复与升级手册.md) | 运维互斥、一致性备份、恢复演练、可回退正式恢复、独立升级器与回滚 |
| [`docs/RC2-Windows实机验收手册.md`](docs/RC2-Windows实机验收手册.md) | Windows 11 测试机逐步命令与证据清单（R4） |
| [`docs/RC2-HTTPS内网证书配置手册.md`](docs/RC2-HTTPS内网证书配置手册.md) | 内部 CA + 反向代理、仅回环监听 |
| [`docs/RC2-业务功能追踪矩阵.md`](docs/RC2-业务功能追踪矩阵.md) | 甲方原始需求逐条完成/后置/不做 |
| [`docs/api.md`](docs/api.md) | 接口与服务端授权一览（测试保证与路由一致） |
| [`docs/权限矩阵.md`](docs/权限矩阵.md) | 五个角色的服务端实际授权 |
| [`docs/decisions/V2.1-LAN-决策记录.md`](docs/decisions/V2.1-LAN-决策记录.md) | D1–D7 业务决策（覆盖 G0 中冲突条目） |
| [`docs/adr/`](docs/adr/) | 架构决策（ADR-004：局域网部署与运行模式） |
| [`docs/templates/三公司报价模板-字段映射与签收.md`](docs/templates/三公司报价模板-字段映射与签收.md) | 三版式、私有对照结论与负责人签收流程（不含真实资料） |
| [`docs/evidence/LAN/`](docs/evidence/LAN/) | L0–L4 门禁证据（测试结果、截图、演练输出） |

## 正式安装（Windows 局域网）

1. 构建机执行 `deploy/windows/build_package.sh <输出目录>`，得到 `Industrial_Sales_Hub_<版本>_LAN_Windows_x64.zip` 与 `.sha256`（由固定提交构建，内含 Python 运行时、PostgreSQL 16、VC++ 运行库、已构建前端）。
2. 在甲方 Windows 主机解压到纯英文路径（如 `D:\IndustrialSalesHub`），**右键 `01-安装部署.bat` → 以管理员身份运行**，按提示创建管理员。
   升级时从**新版本目录**运行 `07-升级程序.bat -InstallDir <安装目录>`。
3. 其他电脑在浏览器打开安装结束时显示的局域网地址。详细步骤见安装手册。

运行模式 `ISH_MODE`：`DEMO`（本机演示，SQLite、演示账号，仅回环访问）/ `LAN`（正式，PostgreSQL，禁止 DEBUG 与演示数据）/ `CLOUD`（保留的 Docker 部署，本期不验收）。

## 本机演示（DEMO，仅开发与演示）

`启动-Windows本机试用.bat` 保留为**演示入口**：SQLite、演示账号（`leader / assistant / sales / finance / admin`，密码 `Demo@2026`）、仅 127.0.0.1。
**不得用于正式数据**；正式部署请使用上面的安装包。

```bash
cd frontend && npm install && npm run build && cd ..
cd backend && pip install -r requirements.txt
python manage.py migrate && python manage.py seed_demo && python manage.py runserver
```

## 运行测试

```bash
cd backend
pip install -r requirements.txt
python -m pytest -q                                      # 默认 SQLite（并发与备份测试自动跳过）
DATABASE_URL=postgres://user:pass@host:5432/db python -m pytest -q   # PostgreSQL：全部测试（需 pg_dump/pg_restore 在 PATH）
# 浏览器验收（LAN 模式，需 playwright）：见 e2e/e2e_v21_lan.py 头部说明
```

## 目录

```text
backend/
  config/            Django 配置、路由
  apps/
    common/          角色能力表、金额计算、PDF 基础设施、错误格式、工作台
    accounts/        用户与登录；seed_demo / verify_integrity 命令
    organizations/   三家出具主体、客户组织、联系人
    products/        产品类别、参数字典、产品型号
    projects/        项目、参与方、售前/执行日志、执行节点、财务信息
    quotations/      报价、版本、报价行、三套 PDF 模板
    contracts/       合同/订单确认（业绩唯一入口，幂等）
    performances/    内部业绩、对外案例（白名单）
    files/           私有附件
    imports/         Excel 预检与导入
    audit/           审计日志
  tests/             POC-A / POC-B / POC-C / G4 自动化测试
frontend/            React + TypeScript 前端（构建产物 dist 不入库）
deploy/windows/      Windows 局域网安装包构建脚本与 01–08/99 运维脚本
e2e/                 浏览器验收脚本（V2.0 基线、V2.1 LAN）
infra/               CLOUD 模式：Dockerfile、compose、Nginx（本期不验收）
docs/
  00-项目基线.md
  adr/               架构决策记录
  decisions/         甲方待确认事项（BLOCKED）
  evidence/G0/       测试证据、PDF 样张、浏览器截图
  design/            设计包归档（PRD、原型、任务书、确认表）
  api.md             API 一览
```

## 关键业务规则（已由测试保护）

- 项目可以不关联客户先建立；一个项目可关联多家主机厂/业主/投资方，同组织可有多种身份。
- 报价必须指定出具公司和接收客户；生成 PDF 后版本冻结（快照 + 哈希），修改只能新建版本；产品库改价不影响历史报价。
- 报价“已接受”不等于业绩；业绩只能通过合同/订单确认生成，重复或并发确认只生成一条；撤销保留记录。
- 销售在服务端就拿不到报价、合同金额、业绩金额和财务数据；执行进度可以随时查看。
- 中标确认/转交后销售对该项目只读；交付与资金状态只有状态和公开说明，不含金额与内部说明（RC3）。
- 对外案例只取白名单字段；业主名称需勾选“已获授权公开”才会出现。

## 协作规范

分支、提交信息（Conventional Commits）、PR 自检清单见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；版本变更见 [`CHANGELOG.md`](CHANGELOG.md)；安全说明见 [`SECURITY.md`](SECURITY.md)。

本仓库为内部项目代码，未附开源许可证，未经授权不得复制或分发。
