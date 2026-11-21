# API 一览（RC2，2.1.0-rc2）

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
| GET/POST/PATCH/DELETE | `projects/` | LE AS SA FI（SA 可见≠可编辑） | LE AS SA（SA 仅本人/参与；成员仅 LE AS 指派） | 删除仅 LE |
| GET/POST/DELETE | `projects/{id}/parties/…` | 同上 | 同上 | 参与方，同项目同组织同身份去重 |
| GET/POST | `projects/{id}/updates/` | 同上 | 售前：LE AS SA；执行：LE AS FI | |
| GET/POST/PATCH | `projects/{id}/milestones/…` | 同上 | LE AS FI | 延期须填原因 |
| GET | `projects/{id}/related/` | 同上 | — | 关联报价/订单/业绩，按角色裁剪 |
| GET/POST/PATCH | `inquiries/` | LE AS FI | LE AS FI | RC3：询价登记（不含价格；明细中价格/金额键被拒绝）；返回关联报价编号 |
| POST | `projects/{id}/handoff/` `projects/{id}/handoff/revert/` | — | LE AS / 仅 LE | RC3：中标确认转交（锁定销售写入）/ 撤销中标 |
| GET/POST | `projects/{id}/tracks/` | 业务角色（SA 无 private_note） | 交付线 LE AS FI；资金线 LE FI | RC3：生产交付/资金两条状态线，不含金额 |
| POST | `projects/{id}/tracks/{eid}/revert/` | — | 同上 | 撤销录错的状态事件（只标记不删除） |
| GET/POST/PATCH | `stage-defs/` | 业务角色 | LE AS | RC3：阶段展示名/排序/停用；code 不可改，无删除 |
| GET/POST/PATCH | `quotations/` | LE AS（`ISH_FINANCE_QUOTE=1` 时含 FI） | 同左 | D2：SA/AD 一律 403 |
| POST | `quotations/{id}/status/` | — | LE AS | 试用版本不能标记已发送/已接受（409 trial_quote） |
| POST | `quotations/{id}/revisions/` | — | LE AS | 复制最新版本为新草稿 |
| GET/PATCH | `quote-revisions/{id}/` | LE AS | LE AS | 冻结版本修改 409；`host_qty/host_label/warranty` |
| POST/PATCH/DELETE | `quote-revisions/{id}/lines/…` | — | LE AS | 后端 Decimal 重算 |
| POST | `quote-revisions/{id}/save-product/` | — | LE AS | 报价行存入产品库 |
| GET | `quote-revisions/{id}/preview/` | LE AS | — | 草稿预览永远带水印（草稿/试用） |
| POST | `quote-revisions/{id}/render/` | — | LE AS | 未签收 409 template_unsigned；`{"trial": true}` 生成试用 PDF |
| GET | `quote-revisions/{id}/pdf/` | LE AS | — | 存档原字节；`X-Quote-Trial` |
| GET | `quote-lines/history/` | LE AS | — | RC2：历史价格组合查询（仅冻结且非试用） |
| GET/POST | `order-confirmations/` | LE AS FI | LE AS FI | FI 无金额字段；试用报价不能作为依据 |
| POST | `order-confirmations/{id}/confirm/` `…/revoke/` `…/amount/` | — | 见 D3 / LE / LE AS | |
| GET/POST/PATCH | `performances/` | LE AS FI SA（SA 仅本人；SA/FI 无金额） | LE AS | 筛选：q/year/year_from/year_to/status/source/industry/machine_type/region/sales_owner/project |
| POST | `performances/{id}/approve/` `…/revoke/` | — | LE | |
| GET | `performances/stats/` `performances/facets/` | 同列表 | — | |
| GET | `performances/export/` | LE AS FI SA | — | RC2：内部 Excel，按范围与字段白名单裁剪，写审计 |
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
