# API 一览（RC3，2.1.0-rc3）

所有接口前缀 `/api/`，同源 Session + CSRF（写操作需带 `X-CSRFToken` 头）。错误统一为：

```json
{"code": "permission_denied", "message": "当前角色无权执行此操作", "field_errors": {}, "request_id": "a1b2c3…"}
```

角色缩写：AD=管理员（技术角色，不浏览业务）LE=部门负责人 AS=商务助理 SA=销售 FI=财务。“读/写”为服务端实际授权
（唯一来源 `backend/apps/common/roles.py` 的 CAP 表与各视图的行级范围；本表由 `tests/test_rc2_api_doc.py` 检查与路由一致）。
常见状态码：401/403 未登录或无权；404 不存在或不在范围；409 业务冲突（版本冻结、模板未签收、试用报价、并发修改）；
503 维护中（备份/恢复/升级窗口，写请求被拒，`Retry-After: 60`）。

| 方法 | 路径 | 读 | 写 | 说明 |
|---|---|---|---|---|
| GET | `auth/csrf/` | 匿名 | — | 下发 CSRF Cookie |
| POST | `auth/login/` `auth/logout/` | — | 匿名/登录 | 登录限速；维护窗口内仍可登录（见一致性边界） |
| GET | `auth/me/` | 登录 | — | 当前用户、角色能力 caps |
| GET | `public-info/` | 匿名 | — | 登录页所需的非敏感信息（版本、模式、HTTP 试用提示、维护状态） |
| POST | `auth/password/` | — | 登录 | 修改本人密码 |
| GET/POST/PATCH | `users/` | AD | AD | 停用/改角色立即清除会话；最后一个管理员不可停用 |
| GET | `user-options/` | 业务角色 | — | 下拉框（姓名与角色） |
| GET | `system/status/` | AD LE | — | 运行模式、备份状态、维护状态 |
| GET | `dashboard/` | 全部 | — | 按角色裁剪；SA/FI 无报价与金额；LE/AS 含到期与“过期仍已发送”报价提醒 |
| GET/POST/PATCH | `legal-entities/` | 全部（无写权限者只见名称/模板键） | AD LE AS | 出具主体；关键配置变化使签收失效 |
| POST | `legal-entities/{id}/signoff/` | — | LE | RC2：签收“模板+模板版本+主体关键配置”，必填依据 |
| POST | `legal-entities/{id}/signoff/revoke/` | — | LE | 撤销签收（之后只能生成试用报价） |
| GET | `legal-entities/{id}/signoffs/` | AD LE AS | — | 签收记录 |
| POST | `legal-entities/{id}/logo/` | — | AD LE AS | Logo（PNG/JPG≤1MB，重编码） |
| GET | `legal-entities/{id}/logo/image/` | AD LE AS | — | |
| GET/POST/PATCH | `account-categories/` | LE AS SA FI | LE AS | RC2：客户自定义类别字典 |
| GET/POST/PATCH/DELETE | `accounts/` | LE AS SA FI | LE AS SA（删除仅 LE） | `?q=&kind=&category=&active=`；被引用的客户不可删除 |
| GET | `accounts/check-duplicate/?name=` | LE AS SA FI | — | 名称标准化后的重复提示 |
| GET | `accounts/{id}/projects/` | LE AS SA FI | — | 受可见范围限制；`?q=&role=&presale_phase=&execution_phase=` |
| GET/POST/PATCH/DELETE | `contacts/` | LE AS SA FI | LE AS SA | |
| GET/POST/PATCH | `product-categories/` `parameter-definitions/` `products/` | 业务角色（SA/FI 不含价格） | LE AS | 无删除，停用代替 |
| GET/POST/PATCH/DELETE | `projects/` | LE AS SA FI（SA 可见≠可编辑） | LE AS FI SA（SA 仅本人/参与且未中标；成员仅 LE AS 指派） | 删除仅 LE；RC3 FI 写入随 `ISH_FINANCE_SCOPE=BUSINESS` |
| GET/POST/DELETE | `projects/{id}/parties/…` | 同上 | 同上 | 参与方，同项目同组织同身份去重 |
| GET/POST | `projects/{id}/updates/` | 同上 | 售前：LE AS SA；执行：LE AS FI | |
| GET/POST/PATCH | `projects/{id}/milestones/…` | 同上 | LE AS FI | 延期须填原因 |
| GET | `projects/{id}/related/` | 同上 | — | 关联报价/订单/业绩，按角色裁剪 |
| GET/POST/PATCH | `inquiries/` | LE AS FI | LE AS FI | RC3：询价登记（不含价格；明细中价格/金额键被拒绝）；返回关联报价编号。状态 待报价/报价中/已报价 由关联报价自动计算（已报价 = 关联报价已标记发送），人工只能 `OPEN`（重新打开）/`CLOSED` |
| GET | `inquiries/{id}/pdf/` | LE AS FI | — | RC3 收口 D-7：内部《项目询价登记单》PDF（只读该询价记录，不含价格；已关闭可下载；`?inline=1` 预览；写审计含 SHA256） |
| POST | `projects/{id}/handoff/` `projects/{id}/handoff/revert/` | — | LE AS / 仅 LE | RC3：中标确认转交（锁定销售写入）/ 撤销中标 |
| GET/POST | `projects/{id}/tracks/` | 业务角色（SA 无 private_note） | 交付线 LE AS FI；资金线 LE FI | RC3：生产交付/资金两条状态线，不含金额。摘要含 `scope=PROJECT`、`active_order_count`、`multi_order`、`needs_reconfirmation`（各线 `coverage_stale`：完结记录后有效合同/订单集合变化）；事件 `label` 为记录时名称、`current_label` 为现名；有 ≥2 张有效合同/订单、或该线原完结确认已失效（合同集合任何变化，含 1→0、1→另一张）时，记录完结类阶段须 `confirm_all_covered=true`，否则 409 `confirm_coverage_required`（D-5）。失效判定：完结后合同集合变化，或记录当时 ≥2 张却未确认覆盖（含迁移前旧数据） |
| POST | `projects/{id}/tracks/{eid}/revert/` | — | 同上 | 撤销录错的状态事件（只标记不删除） |
| GET/POST/PATCH | `stage-defs/` | 业务角色 | LE AS | RC3：阶段展示名/排序/停用；code、track、`is_final`（完结属性）不可改——显式提交不同的 `is_final` 返回 400，新增阶段一律非完结；同一状态线展示名（忽略空白/全半角/大小写）不得重复（400）；无删除 |
| GET/POST/PATCH | `quotations/` | LE AS FI（RC3 D-2；`ISH_FINANCE_QUOTE=0` 或 RC2 口径时不含 FI） | 同左 | SA/AD 一律 403；FI 的报价权限**不含**合同金额与业绩内部金额 |
| POST | `quotations/{id}/status/` | — | LE AS FI | 试用版本不能标记已发送/已接受（409 trial_quote） |
| POST | `quotations/{id}/revisions/` | — | LE AS FI | 复制最新版本为新草稿 |
| GET/PATCH | `quote-revisions/{id}/` | LE AS FI | LE AS FI | 冻结版本修改 409；`host_qty/host_label/warranty` |
| POST/PATCH/DELETE | `quote-revisions/{id}/lines/…` | — | LE AS FI | 后端 Decimal 重算 |
| POST | `quote-revisions/{id}/save-product/` | — | LE AS | 报价行存入产品库 |
| GET | `quote-revisions/{id}/preview/` | LE AS FI | — | 草稿预览永远带水印（草稿/试用） |
| POST | `quote-revisions/{id}/render/` | — | LE AS FI | 未签收 409 template_unsigned；`{"trial": true}` 生成试用 PDF |
| GET | `quote-revisions/{id}/pdf/` | LE AS FI | — | 存档原字节；`X-Quote-Trial` |
| GET | `quote-lines/history/` | LE AS FI | — | RC2：历史价格组合查询（仅冻结且非试用） |
| GET/POST | `order-confirmations/` | LE AS FI | LE AS FI | FI 无 `contract_amount`（读不返回、写 403），可见/可关联报价编号（RC3 D-2）；试用报价不能作为依据 |
| POST | `order-confirmations/{id}/confirm/` `…/revoke/` `…/amount/` | — | 见 D3 / LE / LE AS | |
| GET/POST/PATCH | `performances/` | LE AS FI SA（SA 仅本人；SA/FI 无金额） | LE AS；FI（RC3，金额/内部备注/公开授权 403） | 筛选：q/year/year_from/year_to/status/source/industry/machine_type/region/sales_owner/project |
| POST | `performances/{id}/approve/` `…/revoke/` | — | LE | |
| GET | `performances/stats/` `performances/facets/` | 同列表 | — | |
| GET | `performances/export/` | LE AS FI SA | — | RC2：内部 Excel，按范围与字段白名单裁剪，写审计 |
| GET | `performances/export-pdf/` | LE AS FI SA | — | RC3：内部安全版 PDF（同列表范围与筛选，**列表 `status` 不参与**；`export_status`=CONFIRMED（默认）/PENDING/REVOKED/ALL，其他值 400；标题与筛选摘要注明导出状态；拒绝 `ids`；≤1000 条；无金额/合同号/内部备注；写审计含 SHA256 与导出状态） |
| GET | `projects/{id}/activity/` | LE AS SA FI | — | RC3：项目操作记录；SA 仅动作/操作人/时间，报价动作仅有报价权限者可见 |
| GET | `public-cases/` | LE AS SA | — | 白名单字段；`?industry=&machine_type=&region=&product=&facets=1` |
| POST | `case-preview/` | LE AS SA | — | 白名单字段（含已授权照片） |
| GET/POST | `case-releases/` | LE AS SA | LE AS SA | 仅已确认且允许公开的业绩 |
| GET | `case-releases/{id}/pdf/` | LE AS SA | — | 撤回后 410 |
| POST | `case-releases/{id}/withdraw/` | — | LE | |
| GET/POST | `case-photos/` | LE AS | LE AS | RC2：业绩照片（重编码去 EXIF） |
| PATCH/DELETE | `case-photos/{id}/` | — | 授权仅 LE；其他 LE AS | |
| GET | `case-photos/{id}/image/` | LE AS；SA 仅已授权可公开 | — | |
| GET/POST | `attachments/?owner_type=&owner_id=` | 跟随所属对象；SA 对可见不可编辑项目只得到已授权交付附件 | 跟随所属对象 | owner_type：project/order/performance/quote |
| GET | `attachments/{id}/download/` | 同上 | — | |
| PATCH/DELETE | `attachments/{id}/` | — | 类别/销售可见：LE AS；删除：上传人或 LE | RC2 |
| GET | `import-batches/template/{kind}/` | LE AS | — | products / accounts / performances |
| POST | `import-batches/preview/` `import-batches/{id}/commit/` | — | LE AS | |
| GET | `audit-events/` | AD LE | — | 只读 |
| GET | `/healthz` | 匿名 | — | 版本、模式、维护状态 |

已关闭（D5）：`projects/{id}/finance/` 独立财务台账——任何角色 403，表结构保留。
