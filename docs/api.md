# API 一览

所有接口前缀 `/api/`，同源 Session + CSRF（写操作需带 `X-CSRFToken` 头）。错误统一为：

```json
{"code": "permission_denied", "message": "当前角色无权执行此操作", "field_errors": {}, "request_id": "a1b2c3…"}
```

角色缩写：AD=管理员 LE=负责人 AS=商务助理 SA=销售 FI=财务。“读/写”列为服务端实际授权。

| 方法 | 路径 | 读 | 写 | 说明 |
|---|---|---|---|---|
| GET | `auth/csrf/` | 匿名 | — | 下发 CSRF Cookie |
| POST | `auth/login/` `auth/logout/` | — | 匿名/登录 | 登录限速：8 次失败锁 15 分钟 |
| GET | `auth/me/` | 登录 | — | 当前用户、角色能力、待确认开关 |
| POST | `auth/password/` | — | 登录 | 修改本人密码 |
| GET/POST/PATCH | `users/` | AD | AD | 停用账号立即清除会话 |
| GET | `user-options/` | 全部 | — | 下拉框用，仅姓名与角色 |
| GET | `dashboard/` | 全部 | — | 按角色裁剪；销售/财务无报价数据 |
| GET/POST/PATCH | `legal-entities/` | 全部 | AD LE AS | 三家出具主体及模板键 |
| GET/POST/PATCH/DELETE | `accounts/` | 全部 | AD LE AS SA（删除仅 AD LE） | 被引用的客户不可删除（409） |
| GET | `accounts/check-duplicate/?name=` | 全部 | — | 名称标准化后的重复提示 |
| GET | `accounts/{id}/projects/` | 全部 | — | 受销售行级范围限制 |
| GET/POST/PATCH | `contacts/` | 全部 | AD LE AS SA | |
| GET/POST/PATCH | `product-categories/` `parameter-definitions/` | 全部 | AD LE AS | 类别与参数字典 |
| GET/POST/PATCH | `products/` | 全部（SA/FI 不含价格） | AD LE AS | 无 DELETE，停用代替 |
| GET/POST/PATCH/DELETE | `projects/` | 全部（SA 仅自己/参与） | AD LE AS SA | 可不关联客户创建；SA 不能改负责人 |
| GET/POST | `projects/{id}/parties/` | 同上 | 同上 | 同项目同组织同身份去重 |
| DELETE | `projects/{id}/parties/{party_id}/` | — | 同上 | |
| GET/POST | `projects/{id}/updates/` | 同上 | 售前：AD LE AS SA；执行：AD LE AS FI | `stream=PRESALE/EXECUTION` |
| GET/POST | `projects/{id}/milestones/` | 同上 | AD LE AS FI | POST 初始化 8 个标准节点 |
| PATCH | `projects/{id}/milestones/{mid}/` | — | AD LE AS FI | 自动写执行日志，刷新执行阶段 |
| GET/POST | `projects/{id}/finance/` | AD LE FI | AD LE FI | 合同金额/开票/回款，独立授权 |
| GET | `projects/{id}/related/` | 全部 | — | 关联报价/订单/业绩，按角色裁剪字段 |
| GET/POST/PATCH | `quotations/` | AD LE AS | AD LE AS | 搜索：项目/客户/型号/日期/主体 |
| POST | `quotations/{id}/status/` | — | AD LE AS | 状态流转校验；不等于业绩 |
| POST | `quotations/{id}/revisions/` | — | AD LE AS | 复制最新版本为新草稿 |
| GET/PATCH | `quote-revisions/{id}/` | AD LE AS | AD LE AS | 已生成版本修改返回 409 |
| POST | `quote-revisions/{id}/lines/` | — | AD LE AS | 传 `product` 时从产品库复制并解耦 |
| PATCH/DELETE | `quote-revisions/{id}/lines/{line_id}/` | — | AD LE AS | 后端重算金额，忽略前端合计 |
| GET | `quote-revisions/{id}/preview/` | AD LE AS | — | 草稿预览 PDF（不存档） |
| POST | `quote-revisions/{id}/render/` | — | AD LE AS | 生成 PDF 并冻结（快照+哈希） |
| GET | `quote-revisions/{id}/pdf/` | AD LE AS | — | 私有下载，校验哈希，写审计 |
| GET/POST | `order-confirmations/` | AD LE AS FI | AD LE AS FI | 支持 `idempotency_key`；`confirm_now` |
| POST | `order-confirmations/{id}/confirm/` | — | 见 D3 | 幂等，生成唯一业绩并初始化执行节点 |
| POST | `order-confirmations/{id}/revoke/` | — | AD LE | 必填原因，业绩标记撤销 |
| GET/POST/PATCH | `performances/` | 全部（SA 无金额、仅自己） | AD LE AS | 手工补录为“待核实” |
| POST | `performances/{id}/approve/` `…/revoke/` | — | AD LE | |
| GET | `performances/stats/` | 全部 | — | 金额汇总仅授权角色 |
| POST | `case-preview/` | AD LE AS SA | — | 白名单字段 |
| GET/POST | `case-releases/` | AD LE AS SA | AD LE AS | 仅已确认且允许公开的业绩 |
| GET | `case-releases/{id}/pdf/` | AD LE AS SA | — | |
| GET/POST | `attachments/?owner_type=&owner_id=` | 跟随所属对象 | 跟随所属对象 | 扩展名+签名校验，20MB |
| GET | `attachments/{id}/download/` | 跟随所属对象 | — | |
| GET | `import-batches/template/{kind}/` | AD LE AS | — | products / accounts / performances |
| POST | `import-batches/preview/` | — | AD LE AS | 预检不写库，返回行号与错误 |
| POST | `import-batches/{id}/commit/` | — | AD LE AS | 提交前重新校验；重复提交幂等 |
| GET | `audit-events/` | AD LE | — | |
| GET | `/healthz` | 匿名 | — | 健康检查（含数据库） |
