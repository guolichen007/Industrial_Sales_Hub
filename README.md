# Industrial Sales Hub · 工业销售管理系统 V2.0

[![ci](https://github.com/guolichen007/Industrial_Sales_Hub/actions/workflows/ci.yml/badge.svg)](https://github.com/guolichen007/Industrial_Sales_Hub/actions/workflows/ci.yml)
![Django 5.2 LTS](https://img.shields.io/badge/Django-5.2_LTS-0C4B33) ![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791) ![React 18](https://img.shields.io/badge/React-18-61DAFB) ![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6)

面向 1～5 人小型工业设备销售团队的轻量级业务系统：
**客户与项目 → 商务报价（三公司固定模板）→ 合同/订单确认 → 项目执行进度 → 内部业绩 → 对外案例 PDF**。

依据：设计包（PRD、交互原型、开发任务书、二轮确认表），已归档在 [`docs/design/`](docs/design/)。

> 当前状态：G0 三项技术验证通过，G1–G3 主线功能可运行，G4 已含导入与备份恢复脚本。
> 甲方六项待确认事项均按设计包默认建议实现并标记 **BLOCKED**，详见 [`docs/decisions/G0-决策记录.md`](docs/decisions/G0-决策记录.md)。
> 测试证据见 [`docs/evidence/G0/README.md`](docs/evidence/G0/README.md)（PostgreSQL 16 下 88 项后端测试 + 23 项浏览器检查全部通过）。

## 快速试用（Windows 本机，单机 SQLite）

需要 Python 3.10+ 和 Node.js 18+。双击 `启动-Windows本机试用.bat`，首次会自动构建前端、创建虚拟环境、安装依赖、写入演示数据并打开浏览器。

| 账号 | 角色 | 能看到什么 |
|---|---|---|
| `leader` | 部门负责人 | 全部；确认业绩、撤销订单 |
| `assistant` | 商务助理 | 产品库、报价中心、执行进度、案例 |
| `sales` / `sales2` | 销售人员 | 自己的项目、售前跟进、执行进度（只读）、自己的业绩；**无报价中心、无金额** |
| `finance` | 财务人员 | 执行节点、合同/订单确认、项目财务信息 |
| `admin` | 管理员 | 账号与角色管理 |

演示密码均为 `Demo@2026`（仅演示数据，正式环境请勿使用 `seed_demo`）。

手动启动等价命令：

```bash
cd frontend && npm install && npm run build && cd ..     # 构建前端（产物 frontend/dist 不入库）
cd backend
python -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver                              # 打开 http://127.0.0.1:8000
```

构建后的 `frontend/dist` 由 Django 直接提供。开发前端时：

```bash
cd frontend && npm install && npm run dev               # http://localhost:5173，/api 代理到 8000
npm run build                                           # 重新生成 dist
```

## 云服务器部署（推荐）

一台 Linux 云服务器（初始评估 2 vCPU / 4GB / 80GB SSD）+ Docker：

```bash
cp .env.example .env            # 填写 SECRET_KEY、域名、数据库密码
mkdir -p infra/certs            # 放入 fullchain.pem / privkey.pem
cd infra && docker compose --env-file ../.env up -d --build
docker compose --env-file ../.env exec api python manage.py createsuperuser   # 然后在“系统设置”里把角色改为管理员
```

备份与恢复演练：

```bash
infra/scripts/backup.sh                          # 数据库 + PDF/附件 一起备份，带 SHA256SUMS
infra/scripts/restore-verify.sh <备份目录>        # 恢复到临时库并校验报价/案例 PDF 哈希
```

> 部署模板尚未在甲方服务器上首次运行（开发环境无 Docker），上线前须按 G4 清单实测。

## 运行测试

```bash
cd backend
pip install -r requirements.txt
python -m pytest -q                                      # 默认 SQLite（并发测试自动跳过）
DATABASE_URL=postgres://user:pass@host:5432/db python -m pytest -q   # PostgreSQL：全部 88 项
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
infra/               Dockerfile、compose、Nginx、备份/恢复脚本
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
- 对外案例只取白名单字段；业主名称需勾选“已获授权公开”才会出现。

## 协作规范

分支、提交信息（Conventional Commits）、PR 自检清单见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；版本变更见 [`CHANGELOG.md`](CHANGELOG.md)；安全说明见 [`SECURITY.md`](SECURITY.md)。

本仓库为内部项目代码，未附开源许可证，未经授权不得复制或分发。
