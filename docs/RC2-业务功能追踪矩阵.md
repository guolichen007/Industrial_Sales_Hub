# 业务功能追踪矩阵（RC2，2.1.0-rc2）

> 对照甲方原始需求（PRD 01、开发执行任务书 03、交互原型 02、评审稿 04、二轮确认表 05、G0/V2.1-LAN 决策记录）与 RC2 代码逐条核对。
> RC1 基线由独立只读分析得出（134 条），RC2 更新的条目在“状态”列标注“（RC2）”。证据路径：后端相对 `backend/apps/`，前端相对 `frontend/src/`，测试相对 `backend/tests/`。
> “已完成”= API + UI + 自动化测试（服务端授权为准）。**“已完成”不代表甲方现场验收**；现场项见第 3 节。

## 1. 状态汇总

| 状态 | RC1 | RC2 |
|---|---|---|
| 已完成 | 91 | 108 |
| 部分完成 | 15 | 5 |
| 未实现 | 7 | 1 |
| 后置（PRD P1 / 外部条件 BLOCKED） | 11 | 10 |
| 明确不做 | 10 | 10 |
| 合计 | 134 | 134 |

## 2. 复审重点项结论（RC2）

| # | 项目 | RC1 | RC2 结论与证据 |
|---|---|---|---|
| 1 | 客户详情关联项目 + 筛选 | 部分完成 | **已完成**：名称/身份/售前/执行阶段筛选，显示负责人；受可见范围限制（R-045） |
| 2 | 客户自定义类别 | 实质未实现 | **已完成**：类别字典（负责人/商务维护）、客户多选、列表筛选（R-044） |
| 3 | 历史价格组合查询 | 部分完成 | **已完成**：型号×客户×主体×项目×日期，仅冻结且非试用行；销售/财务/管理员 403（R-081） |
| 4 | 业绩筛选 / 内部 Excel 导出 | 部分 / 未实现 | **已完成**：筛选补齐；导出按范围与字段白名单裁剪，写审计（R-126/127） |
| 5 | 销售常用案例筛选 | 部分完成 | **已完成**：行业/主机/地区/产品筛选 + “以此册再选”复用（R-138） |
| 6 | 公开授权照片 | 未实现 | **已完成**：去 EXIF、负责人授权 + 依据、白名单与 PDF 只含已授权（R-133） |
| 7 | 报价附件 | 未实现 | **已完成**：owner_type=quote，仅报价权限（R-084） |
| 8 | 提醒 | 站内基础版 | **已完成**：到期 + 过期仍已发送；有测试（R-085） |
| 9 | 附件权限 | 无分型 | **已完成**：内部/售前/交付三类；交付附件经商务/负责人授权才对销售可见，可撤回；直链/枚举/合同/报价不外泄（R-111/112） |
| 10 | 五角色 D1–D7 | 符合 | 保持；新增能力 template.signoff（仅 LE）、account.category（LE AS）、performance.export（按白名单） |
| — | 三公司真实模板 | BLOCKED | **工程完成，签收待负责人**：r3 三版式按真实样张；未签收只能出带水印的试用报价（R-022） |
| — | Word/Excel 任意模板上传 | — | **明确不做**：首期固定 PDF 三模板 |

## 3. 必须在现场/甲方完成的验收（不能由开发环境替代）

- 三家模板：负责人逐项比对真实样张后在系统内签收；财务提供并签字 10 组黄金用例；样张 A 的税口径需财务确认。
- Windows 11 实机安装/重复安装/重启/恢复/升级/回滚、两机断网、HTTPS（见 RC2 实机验收手册、HTTPS 手册）。
- 五账号现场协作与权限复核（含附件授权流程）。

## 4. 仍为“部分完成/未实现/后置”的条目说明

- R-022 三套真实固定模板（Q09，PRD §5）：真实样张私有对照：三份合计/大写与样张一致（A 仅在含税口径下一致）；**负责人签收与财务 10 组黄金用例仍需甲方完成**
- R-031 产品图片（PRD §2.1）：首期优先级低
- R-032 按类别参数字典（PRD §6 parameter_definition）：**前端无参数字典维护界面**（只有“+ 类别”`Products.tsx:96`），字典只能靠 API 维护
- R-061 跟进记录附件（PRD §2.1“附件”）：单条跟进无附件
- R-105 执行进度附件（PRD §2.1）：财务上传执行凭证需负责人决定（D5 无金额原则下的文件分级），首期保持
- R-140 数据导出（PRD §2.1 P0）：客户/项目/报价列表导出仍后置（PRD P1“多条件批量导出”）

## 5. 完整追踪表

### A. 账号、角色与安全

| 编号 | 需求（来源） | 状态 | 实现证据（API/UI/测试） | 缺口/说明 |
|---|---|---|---|---|
| R-001 | 账号密码登录（PRD §2.1） | 已完成 | `accounts/views.py:82` LoginView；`App.tsx:47` Login；test_unauthenticated_is_rejected | — |
| R-002 | 五类角色 + 服务端授权（PRD §3，任务书 §4 POC-C） | 已完成 | `common/roles.py:23-65` CAP 表；`projects/scope.py`；test_poc_c_permissions.py（47 项） | — |
| R-003 | 账号停用立即失效（PRD §2.1） | 已完成 | test_disabled_user_session_invalidated、test_role_change_kills_sessions | — |
| R-004 | 密码重置（管理员）/本人改密（PRD §2.1） | 已完成 | `Settings.tsx:97`（管理员重置）、`accounts/views.py:118` ChangePasswordView | 无“忘记密码”自助（LAN 内部系统，合理） |
| R-005 | 登录限速、密码策略（PRD §8 安全） | 已完成 | test_login_rate_limited、test_password_policy_and_no_password_in_audit | 反向代理场景见 剩余问题 B3 |
| R-006 | CSRF / Session 安全（任务书 §2） | 已完成 | test_login_csrf_enforced_for_real_clients | HTTP 试用 Cookie 非 Secure（B2） |
| R-007 | 最后一个管理员不可停用/降级 | 已完成 | test_last_admin_cannot_be_demoted_or_disabled、test_concurrent_last_admin_demotion | — |
| R-008 | 1～5 人并发（Q03） | 已完成 | test_five_roles_mixed_concurrent；evidence LAN-10（沙箱） | 目标主机未复测（B8） |

### B. 工作台

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-010 | 按角色工作台（PRD §2.1、§7.2） | 已完成 | `common/dashboard.py:34`；`Dashboard.tsx:137-218`（ADMIN/SALES/FINANCE/LEADER/ASSISTANT 五套） | — |
| R-011 | 我的待办（PRD §2.1） | 已完成 | `dashboard.py:52-55` todos（跟进“下一步”14 天内）；`Dashboard.tsx:68` | 无测试断言 todos 计算 |
| R-012 | 最近项目 / 项目动态 | 已完成 | `dashboard.py:48-51`；`Dashboard.tsx:50` Timeline | — |
| R-013 | 业绩概览 | 已完成 | `dashboard.py:84-85` | — |
| R-014 | 销售首页无商业金额（PRD §7.1） | 已完成 | test_d2_sales_no_quote_count_no_scale_note | — |

### C. 出具主体与模板

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-020 | 三公司配置：名称/地址/联系方式/税号/银行/默认条款/页脚（PRD §2.1） | 已完成 | `organizations/models.py:21-49`；`Settings.tsx:110,190`；e2e“负责人维护三家出具主体” | — |
| R-021 | 每家公司计价口径（未税/含税）、税列显示 | 已完成 | `LegalEntity.price_basis/show_tax_columns`；test_gross_basis_and_no_tax_column_template | — |
| R-022 | 三套真实固定模板（Q09，PRD §5） | **部分完成（工程完成，签收待负责人）**（RC2） | r3 三版式依据真实样张：`quotations/pdf.py`；签收 `TemplateSignoff` + `legal-entities/{id}/signoff/`；未签收试用水印；test_rc2_templates.py（18 项） | 真实样张私有对照：三份合计/大写与样张一致（A 仅在含税口径下一致）；**负责人签收与财务 10 组黄金用例仍需甲方完成** |
| R-023 | Logo（PRD §2.1“如有”） | **已完成**（RC2） | `LegalEntity.logo_key/logo_sha256`、`legal-entities/{id}/logo/`；模板 A/B/C 绘制 Logo；verify_integrity 校验 | Logo 文件只在安装端上传，不入库 |
| R-024 | 禁止前端自定义模板/执行脚本（任务书 §6） | 已完成 | ReportLab 纯 Python 渲染（ADR-002） | — |

### D. 产品型号库

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-030 | 名称/型号/类别/品牌/图号/单位/税率/技术说明/参数（PRD §2.1） | 已完成 | `products/models.py:29-48`；`Products.tsx:10-52` | — |
| R-031 | 产品图片（PRD §2.1） | 未实现 | Product 无图片字段；Attachment 无 product 类型 | 首期优先级低 |
| R-032 | 按类别参数字典（PRD §6 parameter_definition） | 部分完成 | API `/parameter-definitions/`（`products/views.py`）；产品表单可“套用字典” `Products.tsx:14-19` | **前端无参数字典维护界面**（只有“+ 类别”`Products.tsx:96`），字典只能靠 API 维护 |
| R-033 | 商务/负责人新增、编辑、停用；无删除（Q11） | 已完成 | test_product_write_matrix、test_products_cannot_be_deleted | — |
| R-034 | 产品 Excel 导入（PRD §2.1） | 已完成 | `imports/`；`Products.tsx:127`；test_product_import | — |
| R-035 | 非报价角色看产品不含价格 | 已完成 | `products/views.py` ProductPublicSerializer；test_d2_sales_products_without_price | — |
| R-036 | 报价中手工行另存为产品 | 已完成 | `quotations/views.py:286`；test_manual_line_save_as_product_does_not_touch_quote | — |

### E. 客户组织

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-040 | 一个组织多业务身份（PRD §2.1） | 已完成 | `Account.kinds`；`Customers.tsx:29-33` | 身份列表写死 `options.ts:3` |
| R-041 | 多联系人/部门/职务（PRD §2.1） | **已完成**（RC2） | `Customers.tsx` ContactForm 支持编辑；API `/contacts/` PATCH/DELETE | — |
| R-042 | 名称标准化查重提示（PRD §2.1、任务书 §5） | 已完成 | `organizations/views.py:98` check-duplicate；`Customers.tsx:12,28` | 无功能测试（仅参数校验用例） |
| R-043 | 按条件筛选客户（PRD §2.1） | **已完成**（RC2） | UI 增加身份 + 客户类别筛选；API `?category=` | — |
| R-044 | **客户自定义类别/标签**（原型“可编辑行业字典”、PRD §7.1“行业标签”、审阅人关注 #2） | **已完成**（RC2） | `AccountCategory` + `Account.categories`；`/account-categories/`（LE AS 维护）；客户表单多选、列表筛选；test_account_categories_and_filter | 导入模板暂不含类别列（后续小步） |
| R-045 | **客户详情关联项目**（任务书 §5、PRD §7.1、关注 #1） | **已完成**（RC2） | `accounts/{id}/projects/?q=&role=&presale_phase=&execution_phase=`，返回负责人/更新时间；`Customers.tsx` 关联项目筛选；test_account_projects_filters_and_visibility | — |
| R-046 | 被引用客户不可删除，仅负责人删除 | 已完成 | test_customer_referenced_by_project_cannot_be_deleted | — |
| R-047 | 客户停用 | **已完成**（RC2） | `Account.active`；客户详情“停用/启用”按钮 | — |
| R-048 | 客户 Excel 导入（任务书 §2） | 已完成 | `imports/service.py:10,103,155`；`Settings.tsx:206` | 无客户导入专门测试 |

### F. 项目台账

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-050 | 独立建项、可无客户（PRD §4.1、U01） | 已完成 | test_U01_sales_creates_project_without_customer_then_links_parties；e2e“销售登记无客户项目” | — |
| R-051 | 多参与方 + 角色 + 去重（PRD §2.1） | 已完成 | `projects/views.py:216-245`；`ProjectDetail.tsx:20,151` | — |
| R-052 | 区域/行业/主机类型/投资方/地点 | 已完成 | `projects/models.py:11-29` | — |
| R-053 | 负责人 + 项目成员 | **已完成**（RC2） | `Projects.tsx` ProjectForm 成员多选（仅 project.edit_any 角色） | — |
| R-054 | 售前 8 阶段 / 执行 5 阶段分离（PRD §4.4） | 已完成 | `projects/models.py:4-5` | — |
| R-055 | 项目列表筛选（PRD §7.2） | 已完成 | API q/售前/执行/mine；`Projects.tsx:67-82` | — |
| R-056 | 项目 360° 关联（客户/报价/订单/进度/业绩） | 已完成 | `projects/views.py:343-357` related（按角色裁剪）；`ProjectDetail.tsx:198-290` | — |
| R-057 | 项目删除受保护 | 已完成 | `projects/views.py:205-213` | — |
| R-058 | 销售不能直接设“成交” | 已完成 | test_sales_cannot_mark_won_without_order、test_sales_cannot_move_won_project_back | — |
| R-059 | “预计规模”自由文本仅商务/负责人 | 已完成 | test_d2_sales_no_quote_count_no_scale_note | — |

### G. 售前跟进

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-060 | 时间/阶段/联系对象/内容/下一步（PRD §2.1） | 已完成 | `ProjectUpdate` `projects/models.py:53-70`；`ProjectDetail.tsx:46-69` | — |
| R-061 | 跟进记录附件（PRD §2.1“附件”） | 部分完成 | 只有项目级附件 | 单条跟进无附件 |
| R-062 | 销售与商务写售前、财务只读（Q补充） | 已完成 | test_update_stream_write_matrix | — |

### H. 商务报价

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-070 | 仅负责人/商务（Q11、LAN-D2） | 已完成 | `roles.py:46`；test_quote_list_access、test_U05_* | — |
| R-071 | 出具公司 + 接收客户 + 联系人 + 四步向导（PRD §7.2） | 已完成 | `Quotes.tsx:7-66` | — |
| R-072 | 产品行（复制并解耦）+ 参数可选显示 | 已完成 | `quotations/views.py:233-267`；test_snapshot_contains_only_visible_params | — |
| R-073 | 单价/数量/税率/折扣/乘数编辑，后端 Decimal 重算 | 已完成 | test_backend_recalculates_ignoring_client_totals、test_golden_amounts | 黄金样例为工程样例（A2） |
| R-074 | 合计 + 大写金额 | 已完成 | test_rmb_upper、test_upper_amount_edge_cases | — |
| R-075 | 商务条款/交货/付款/有效期/备注 | 已完成 | `QuoteRevision` `quotations/models.py:39-45`；`QuoteEditor.tsx:239-250` | — |
| R-076 | 编号与版本；生成即冻结（快照+哈希）；V2 复制 | 已完成 | test_frozen_revision_is_immutable_and_v2_reflects_changes、test_frozen_preview_returns_archived_bytes | — |
| R-077 | PDF 预览与下载、失败可重试 | 已完成 | `quotations/views.py:310-350`；test_pdf_failure_does_not_hang_and_records_error | — |
| R-078 | 报价状态 6 态（PRD §4.4） | 已完成 | `quotations/views.py:20-27`；test_quote_codes_unique_and_status_flow | — |
| R-079 | 同项目多报价、不同客户/主体（U02） | 已完成 | test_U02_two_quotes_same_project_different_recipients_and_entities | — |
| R-080 | 历史搜索：项目/客户/型号/日期/主体（PRD §2.1、任务书 §6） | 已完成 | API `quotations/views.py:140-157`（q 含行型号、project/recipient/legal_entity/status/date）；UI `Quotes.tsx:76-94` | UI 无项目/客户下拉（以 q 文本代替）；无搜索功能测试 |
| R-081 | **历史价格组合查询**（产品×客户×主体×日期，关注 #3） | **已完成**（RC2） | `GET /quote-lines/history/`（只含冻结非试用行）；`Quotes.tsx` “历史价格查询”页签；test_price_history_* | 编辑器内“该行历史价”快捷入口为后续小步 |
| R-082 | 铜价/汇率仅手工参考（Q 补充、LAN-D4） | 已完成 | `quotations/models.py:46-50`；test_gross_price_basis_and_reference_rates | 自动接口明确不做（R-158） |
| R-083 | 草稿并发乐观锁 | 已完成 | test_stale_version_rejected、test_render_races_with_edits | — |
| R-084 | **报价附件**（关注 #7；PRD §6 attachment 通用） | **已完成**（RC2） | `Attachment.owner_type=quote`（仅 quote.access）；QuoteEditor 报价附件卡片；test_order_and_quote_files_cannot_be_reclassified_to_sales；E2E | — |
| R-085 | 报价有效期到期提醒（关注 #8） | **已完成**（RC2） | `dashboard.py` quotes_expiring（每份报价最新版本）+ quotes_overdue（过期仍已发送）；test_dashboard_overdue_sent_quote_reminder | — |

### I. 合同/订单确认

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-090 | 关联项目/报价/依据/日期/金额/产品（PRD §2.1） | 已完成 | `contracts/models.py:13-56`；`Execution.tsx:8-60` OrderForm | — |
| R-091 | D3 “或”规则：合同/订单号，或负责人填写理由 | 已完成 | `contracts/services.py:59-80`；test_leader_basis_requires_reason_and_leader、test_and_mode_requires_leader | — |
| R-092 | 同主体订单号去重（归一化） | 已完成 | `contracts/models.py:44-51`；test_order_ref_normalized_uniqueness | — |
| R-093 | 幂等防重复确认（U06） | 已完成 | test_U06_repeat_confirm_is_idempotent、test_concurrent_confirm_creates_single_performance | — |
| R-094 | 撤销留痕（仅负责人） | 已完成 | test_revoke_keeps_trace_and_allows_reuse_of_order_ref | — |
| R-095 | 绑定冻结版本 | 已完成 | test_order_binds_frozen_revision_and_checks_consistency | — |
| R-096 | 合同/订单证据附件（PRD §2.1） | **已完成**（RC2） | ProjectDetail 订单区附件（仅 order.amount） | — |
| R-097 | 金额由负责人/商务补录（LAN-D5） | 已完成 | `contracts/views.py:147-163`；test_finance_confirm_then_leader_sets_amount_syncs_performance | — |

### J. 项目执行

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-100 | 8 个执行节点，订单确认后自动建立 | 已完成 | `projects/views.py:360-367`；test_milestones_only_after_order | — |
| R-101 | 状态/责任人/预计/实际/说明/延期原因/不适用 | 已完成 | test_milestone_validation_and_delay_reason；e2e“延期未填原因时保存按钮禁用” | — |
| R-102 | 执行日志自动写、暂停/恢复 | 已完成 | `projects/views.py:315,320-340`；test_finance_can_pause_and_resume_only_with_order | — |
| R-103 | 商务/财务写，销售只读安全摘要（U07） | 已完成 | test_U07_finance_updates_milestone_sales_sees_safe_timeline | — |
| R-104 | 执行页：按节点看项目列表与逾期项（PRD §7.1） | 已完成 | `Execution.tsx:73-120`；`dashboard.py:56-59` milestone_alerts | — |
| R-105 | 执行进度附件（PRD §2.1） | **部分完成**（RC2） | 附件分型 DELIVERY + 商务/负责人授权“销售可见”；财务仍不能上传项目附件 | 财务上传执行凭证需负责人决定（D5 无金额原则下的文件分级），首期保持 |
| R-106 | 独立财务台账（合同金额/开票/回款）（G0-D5） | 后置 | LAN-D5 关闭；`roles.py:64` 空能力；test_d5_finance_ledger_closed_for_everyone | `FinanceEntry` 表保留；**docs/api.md:33 仍列出 `/projects/{id}/finance/`，文档过期** |

### K. 附件（关注 #9）

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-109 | 附件白名单/签名/大小上限（PRD §2.1、任务书 §2） | 已完成 | `files/views.py:20-31,97-104`；test_attachment_signature_and_scope | — |
| R-110 | 私有下载、按所属对象重新鉴权（PRD §6） | 已完成 | `files/views.py:112-123`；test_attachment_owner_must_exist | — |
| R-111 | 项目附件仅“可编辑项目者”可列/下（LAN 其他工程决定） | **已完成（RC2 调整）**（RC2） | `files/views.py` can_read_attachment：编辑范围或“已授权交付附件 + 项目可见”；test_sales_sees_only_explicitly_authorized_delivery_files；E2E 两个销售账号 | 列表接口对可见不可编辑者返回 200（仅授权项），直链/枚举仍 403 |
| R-112 | 附件删除/替换 | **已完成**（RC2） | `PATCH/DELETE /attachments/{id}/`（上传人或 LE；写审计）；test_attachment_delete_rules | 删除后文件本体保留在私有目录（备份/追溯） |

### L. 业绩台账

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-120 | 订单确认自动生成唯一业绩 | 已完成 | `contracts/services.py:33`；e2e“订单生成唯一业绩” | — |
| R-121 | 手工补录 → 待核实 → 负责人确认/撤销 | 已完成 | `performances/views.py:194-239`；test_manual_performance_is_pending_until_leader_approves、test_performance_revoke_must_go_through_order | — |
| R-122 | 产品/主机/主机厂/业主/投资方/年份/地区/行业字段 | 已完成 | `performances/models.py:15-29` | — |
| R-123 | 业绩证明文件（PRD §2.1） | **已完成**（RC2） | 业绩编辑弹窗附件区（仅 performance.write） | — |
| R-124 | 销售按归属、无金额；财务全部无金额 | 已完成 | `scope.py` performances_for；test_performance_amount_field_level、test_d7_project_visibility_does_not_widen_performance | — |
| R-125 | 负责人全部 + 内部统计 | 已完成 | `performances/views.py:119-131` stats（数量/金额/按年） | 仅按年；丰富图表 P1（R-171） |
| R-126 | **业绩筛选**（关注 #4） | **已完成**（RC2） | API 增加 region/year_from/year_to/sales_owner/project + facets；UI 行业/主机/地区/来源/年份区间；test_performance_filters | — |
| R-127 | **内部 Excel 导出**（关注 #4；PRD §2.1“数据导出”） | **已完成**（RC2） | `GET /performances/export/`（openpyxl；范围与字段白名单；公式注入防护；审计）；“导出 Excel”按钮；test_performance_export_*；E2E | — |
| R-128 | 历史业绩 Excel 导入（Q13、U09） | 已完成 | test_U09_performance_import_preview_commit_idempotent、test_import_400_performances_idempotent | — |

### M. 对外案例

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-130 | 仅“已确认 + 允许对外展示”业绩入选（PRD §4.2） | 已完成 | `performances/views.py:146-152`；test_case_release_rejects_unconfirmed_or_unpublishable | — |
| R-131 | 白名单字段、无金额/合同号/主机厂（U08） | 已完成 | `performances/public.py:5`；test_U08_case_pdf_and_api_contain_no_sensitive_data | — |
| R-132 | 业主名称授权后才公开（00-交付说明） | 已完成 | `public.py:9` public_name_authorized | — |
| R-133 | **公开授权照片**（PRD §2.1“图片与简介”、任务书 §4“明确授权图片”、关注 #6） | **已完成**（RC2） | `CasePhoto`（重编码去 EXIF）、负责人授权 + 依据；PUBLIC_FIELDS 增 photos；案例册嵌图；test_case_photos_authorization_and_exif_stripped | — |
| R-134 | 固定版式 PDF + 快照/哈希冻结 | 已完成 | `performances/views.py:314-332` | — |
| R-135 | 生成前人工核对 | 已完成 | `Cases.tsx:87`；e2e“未人工确认前不能生成” | — |
| R-136 | 撤回后不可下载 | 已完成 | test_case_withdraw_blocks_download | — |
| R-137 | 销售浏览/生成可公开案例（LAN-D2/D7） | 已完成 | `roles.py:58-59`；test_sales_can_browse_and_generate_public_cases_only；e2e“销售生成案例册 PDF” | 与 PRD §3 原表“只读或申请”不同，以 LAN-D2 为准 |
| R-138 | **销售常用案例筛选**（关注 #5；PRD §7.2“左侧经过筛选的候选业绩”） | **已完成**（RC2） | `/public-cases/?industry=&machine_type=&region=&product=&facets=1`；Cases.tsx 下拉 + “以此册再选”；test_public_case_filters_and_facets_whitelist | — |

### N. 数据、审计、运维与部署

| 编号 | 需求（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-140 | 数据导出（PRD §2.1 P0） | **部分完成**（RC2） | 业绩导出已完成（R-127） | 客户/项目/报价列表导出仍后置（PRD P1“多条件批量导出”） |
| R-141 | 操作日志（PRD §2.1） | 已完成 | `audit/`；`Settings.tsx:211-217`；test_audit_log_records_and_redacts、test_admin_audit_hides_business_summaries | UI 只看最近 50 条，无筛选（API 有 resource 过滤） |
| R-142 | 敏感下载审计 | 已完成 | `quotations/views.py:344`、`files/views.py:118`、`performances/views.py:355` | — |
| R-143 | 备份 + 恢复演练（PRD §2.1、U10） | 已完成（沙箱） | `ish_backup`/`ish_verify_backup`；test_v21_backup_restore.py（7 项） | Windows 正式恢复/升级脚本未实机（A7）；任务 #3-#5 pending |
| R-144 | Excel 导入预检 → 提交、幂等、拒绝宏 | 已完成 | test_import_rejects_wrong_header_and_xlsm、test_corrupt_xlsx_and_nan_values | — |
| R-145 | 统一错误结构 + request_id | 已完成 | test_error_shape_has_request_id | — |
| R-146 | PWA/桌面入口（Q01、G0-D1） | 已完成 | `frontend/index.html:7` manifest | — |
| R-147 | 局域网 Windows 集中部署（LAN-D1） | **后置（BLOCKED，RC2 已提供实机手册）**（RC2） | RC2 安装器加固 + `docs/RC2-Windows实机验收手册.md` | 必须在干净 Windows 11 实机执行 |
| R-148 | 断网完整可用（LAN-D6） | **后置（BLOCKED）**（RC2） | 同上手册第 5 节（两机断网） | 需第二台设备 |
| R-149 | HTTPS | **后置（BLOCKED，RC2 已提供配置手册）**（RC2） | `docs/RC2-HTTPS内网证书配置手册.md`；安装 -Https 仅回环监听 | 需内部 CA 或甲方书面例外 |
| R-150 | 移动端进度查看/轻量跟进（PRD §7.2） | 已完成 | e2e“手机 390px 无横向溢出” | P1 “移动端轻操作”深化后置 |

### O. 明确不做

| 编号 | 需求（来源） | 状态 | 依据 |
|---|---|---|---|
| R-151 | 离线本地应用 + 双向同步 | 明确不做 | PRD §2.3；G0-D1/LAN-D1 |
| R-152 | 客户登录门户 | 明确不做 | Q02；PRD §2.3 |
| R-153 | 地图标注 / GIS | 明确不做 | Q14；PRD §2.3 |
| R-154 | 多级审批 / 电子签章 / 盖章 | 明确不做 | Q10；PRD §2.3 |
| R-155 | 任意 Word/Excel 模板上传自动适配 / 版式设计器 | 明确不做 | Q09；PRD §5；LAN-D4 |
| R-156 | 公有云部署 / 公网域名 | 明确不做（本期） | LAN-D1、LAN-D6 覆盖 Q04/Q05 |
| R-157 | ERP 采购/库存/总账/开票 | 明确不做 | PRD §2.3 |
| R-158 | 自动铜价/汇率第三方接口 | 明确不做 | PRD §2.3；LAN-D4 |
| R-159 | 手机原生 APP | 明确不做 | PRD §2.3 |
| R-160 | 生产排程 | 明确不做 | PRD §2.3 |

### P. PRD P1（后置）

| 编号 | 需求（来源） | 状态 | 说明 |
|---|---|---|---|
| R-170 | 项目阶段看板、可配置常用视图（PRD §2.2） | 后置 | 未实现，PRD P1 |
| R-171 | 丰富图表 / 多角色汇报看板（PRD §2.2） | 后置 | 仅 KPI 卡片 |
| R-172 | 进度通知邮件/企业微信（PRD §2.2） | 后置 | LAN 断网场景也不适用；提醒仅站内（R-011/R-085/R-104） |
| R-173 | 多条件批量导出（PRD §2.2） | 后置 | 但 P0 基本导出未做（R-140） |
| R-174 | 案例 PDF 主题、更多模板条款后台项（PRD §2.2） | 后置 | — |

### T. 五角色规则 D1–D7 符合性（关注 #10）

| 编号 | 规则（来源） | 状态 | 实现证据 | 缺口/说明 |
|---|---|---|---|---|
| R-T1 | ADMIN 仅技术：账号、出具主体、诊断；不浏览项目/客户/报价/业绩/金额（LAN 其他决定） | 已完成 | `roles.py:25-28,36,53`（project/account/performance.read 不含 ADMIN）；`scope.py` visible_projects 对 ADMIN 返回空；test_admin_has_no_business_sensitive_access、test_admin_cannot_browse_business_data、test_admin_dashboard_has_no_business_numbers；e2e“管理员访问报价 API 被拒” | ADMIN 仍可读产品（无价格）与类别（read_cap `*`），无敏感性，可接受 |
| R-T2 | LEADER/ASSISTANT 报价（Q11、LAN-D2） | 已完成 | `roles.py:46` quote.access；test_quote_list_access | — |
| R-T3 | SALES 只售前、无报价/价格/合同/业绩金额（LAN-D2） | 已完成 | `roles.py:42-43,46,50,55`；test_sales_project_detail_and_related_never_contain_amounts、test_sales_never_gets_quote_summary_even_with_legacy_flag、test_d2_sales_performance_has_no_order_ref、test_sales_execution_log_has_no_order_ref | **偏差**：销售可新建/修改**任意**客户（`account.write` 无行级范围，`organizations/views.py:59`），PRD §3 为“授权范围”；需确认是否接受 |
| R-T4 | FINANCE 确认订单 + 执行，无金额（LAN-D5） | 已完成 | `roles.py:43,48-50,55`；test_d5_finance_cannot_write_contract_amount、test_d5_finance_can_confirm_without_amount_and_never_sees_amount | 财务能上传订单证据但不能下载自己上传的文件（设计如此，test 第 602 行）；财务看不到项目附件（R-105） |
| R-T5 | D3 OR 模式 | 已完成 | 见 R-091 | — |
| R-T6 | D7 默认 ALL 可看、仅改本人；业绩范围独立 | 已完成 | `scope.py`；test_d7_default_sales_sees_all_projects_but_edits_only_own、test_d7_sales_cannot_grant_membership_or_owner | 成员维护无 UI（R-053） |
| R-T7 | D1/D4/D6 现场项 | 后置（BLOCKED） | 见 R-022、R-147、R-148 | — |
| R-T8 | 文档一致性 | **已完成**（RC2） | `docs/api.md` 全量更新；`tests/test_rc2_api_doc.py` 保证每个路由族都在文档中 | — |

（R-T 行已计入汇总。）


## 6. 明确不在首期范围

公有云部署、电子签章、客户门户、地图、复杂 ERP、审批流、任意 Word/Excel 报价模板上传、实时铜价行情接口与自动调价。
