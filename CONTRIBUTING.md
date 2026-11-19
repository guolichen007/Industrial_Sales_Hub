# 贡献与协作规范

## 分支策略

| 分支 | 用途 | 规则 |
|---|---|---|
| `main` | 可部署的稳定代码 | 受保护；只能通过 Pull Request 合并；CI 必须通过 |
| `feature/<简述>` | 新功能，例如 `feature/quote-template-b` | 从 `main` 拉出，完成后提 PR |
| `fix/<简述>` | 缺陷修复 | 同上 |
| `release/vX.Y.Z` | 上线前冻结（可选） | 只接受修复 |
| `hotfix/<简述>` | 生产紧急修复 | 合并回 `main` 并打标签 |

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，发布时打标签 `vX.Y.Z`，并更新 `CHANGELOG.md`。

## 提交信息（Conventional Commits）

```
<type>(<scope>): <简短说明>

<正文：为什么改、改了什么（可选）>

<页脚：Refs #12 / BREAKING CHANGE: …（可选）>
```

- `type`：`feat` 新功能 · `fix` 修复 · `docs` 文档 · `test` 测试 · `refactor` 重构 · `perf` 性能 · `build` 构建/依赖 · `ci` 持续集成 · `chore` 杂项
- `scope`（建议）：`backend` `frontend` `quotations` `projects` `contracts` `performances` `auth` `infra` `docs`
- 标题不超过 72 字符，不以句号结尾；一次提交只做一件事。

示例：`feat(quotations): 支持报价行折扣率`、`fix(projects): 销售不能修改他人项目`

## 提交前自检

```bash
cd backend && python -m pytest -q                 # 后端测试（建议连接 PostgreSQL 跑全部）
python manage.py makemigrations --check --dry-run # 模型改动必须附带迁移
cd ../frontend && npm run typecheck && npm run build
```

## 红线

- 禁止提交：`.env`、密钥/证书、数据库文件、真实客户资料、真实合同金额、带印章的正式报价。
- 权限只能在后端实现；前端隐藏按钮不算权限控制，新增接口必须补充权限测试。
- 已生成报价的快照与 PDF 不可修改；改动报价模板必须递增 `TEMPLATE_VERSION`。
- 对外案例新增字段必须加入白名单并补充脱敏测试。
- 涉及甲方待确认事项（`docs/decisions/G0-决策记录.md`）的改动，须在 PR 中注明依据。
