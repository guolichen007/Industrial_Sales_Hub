# G0 门禁证据矩阵

> 生成日期 2026-11-19。同一份代码在同一次运行中得到以下结果。

## 运行环境与命令

| 项目 | 内容 |
|---|---|
| 数据库 | PostgreSQL 16.2（本地实例）；另在 SQLite 上回归 86 通过 / 2 跳过（跳过的是需要行锁的并发测试） |
| 后端 | Python 3.10 · Django 5.2.8 · DRF 3.18.3 · ReportLab 5.1.0 |
| 前端 | Node 22 · TypeScript 5.6（strict）· Vite 5 · React 18；`tsc -b` 0 错误，`vite build` 成功 |
| 浏览器 | Chromium（Playwright headless），桌面 1440×900 与手机 390×844 |
| 后端测试命令 | `cd backend && DATABASE_URL=postgres://… python -m pytest -q` → **88 passed** |
| 浏览器测试 | 23 项端到端检查 → **23/23 PASS**（截图见 `e2e/`） |
| 原始结果 | `pytest-postgresql16.xml`（JUnit）、`e2e/e2e_results.json` |

## 门禁结论

| 门禁项 | 结论 | 证据 |
|---|---|---|
| POC-A 三套固定模板 PDF（中文、分页、页码、大写金额） | **PASS（技术）** / 业务验收 **BLOCKED（D4）** | `pdf/报价样张_*.pdf` 与对应 PNG；甲方真实样张未到，不能宣布报价模块验收通过 |
| POC-A V1 冻结：改产品库后重渲染 PDF 哈希不变，V2 才变化 | **PASS** | `test_frozen_revision_is_immutable_and_v2_reflects_changes` |
| POC-A 金额 Decimal 黄金样例 | **PASS（10 组建议口径）** / 财务口径 **BLOCKED（D4）** | `test_golden_amounts` |
| POC-B 无客户建项目 → 关联多个参与方 | **PASS** | U01 测试 + 浏览器截图 06 |
| POC-B 同项目两份报价（不同接收客户/出具公司），一份失标不影响项目 | **PASS** | U02 + `test_lost_quote_does_not_lose_project…` |
| POC-B 重复/并发确认只生成一条业绩；撤销留痕 | **PASS**（PostgreSQL 5 线程并发） | U06、`test_concurrent_confirm_creates_single_performance`、`test_revoke_keeps_trace…` |
| POC-C 五角色服务端权限矩阵（≥20 条） | **PASS（47 条）** | `test_poc_c_permissions.py` |
| POC-C 销售 API 不返回任何金额字段 | **PASS** | U05、`test_sales_project_detail_and_related_never_contain_amounts`，浏览器检查 |
| POC-C 对外案例 API / PDF 字节 / 元数据 / 文本层无敏感数据 | **PASS** | U08 + `pdf/案例册样张_已脱敏.pdf` |
| 备份恢复演练（DB + 私有文件，哈希校验） | **PASS**；删除一个 PDF 后校验**正确失败** | `manage.py verify_integrity`，脚本 `infra/scripts/restore-verify.sh` |
| 六项待确认事项有决策记录 | **已记录，均为 BLOCKED** | `docs/decisions/G0-决策记录.md` |
| Docker 镜像构建与云服务器部署 | **未执行**（当前环境无 Docker、无甲方服务器） | `infra/` 模板已提供，需在目标服务器上首次验证 |

**G0 判定**：三个技术 POC 均可复现 PASS；业务层面 D1–D6 仍为 BLOCKED，按任务书规则**报价模块不宣布业务验收通过**。代码已覆盖 G1–G3 主线和 G4 的导入/备份部分，可直接用于甲方演示与确认会。

## UAT 场景对照（PRD 第 11 节）

| ID | 场景 | 自动化结果 |
|---|---|---|
| U01 | 销售新建项目，先不关联客户 | PASS（API + 浏览器） |
| U02 | 同一项目给两家主机厂分别报价 | PASS |
| U03 | 修改价格、选择部分参数 | PASS（快照仅含勾选参数；产品库价格不变） |
| U04 | V1 冻结、V2 变化 | PASS |
| U05 | 销售直接请求报价/合同/财务 | PASS（403 或字段不存在） |
| U06 | 重复确认只生成一条业绩 | PASS（含并发） |
| U07 | 财务更新执行，销售看安全时间轴 | PASS（API + 浏览器） |
| U08 | 公开案例 PDF 无敏感数据 | PASS |
| U09 | 导入 100 条历史业绩含重复/错误 | PASS（行号、预检不写库、重复提交幂等） |
| U10 | 备份恢复演练 | PASS（本地 PostgreSQL；云端需复测） |

## 浏览器端到端检查明细

| 检查 | 结果 |
|---|---|
| 商务助理可见报价中心与产品库 | PASS |
| 取消显示参数后状态由服务端回写 | PASS |
| 报价合计由后端 Decimal 计算 | PASS |
| 生成后版本冻结（输入框禁用） | PASS |
| 显示 PDF 与快照哈希 | PASS |
| PDF 下载 | PASS |
| 创建 V2 草稿 | PASS |
| 销售看不到报价中心/产品库菜单 | PASS |
| 销售可建无客户项目 | PASS |
| 项目关联多个参与方 | PASS |
| 销售无执行进展按钮 | PASS |
| 销售直接调用报价 API 被拒(403) | PASS |
| 销售直接访问报价页显示无权 | PASS |
| 订单确认后项目进入执行并生成节点 | PASS |
| 业绩台账出现订单业绩 | PASS |
| 案例预览不含金额 | PASS |
| 财务无报价中心 | PASS |
| 销售可见执行进度（技术资料确认 已完成） | PASS |
| 销售执行页无金额 | PASS |
| 销售请求财务 API 被拒 | PASS |
| 销售项目关联接口不含金额字段 | PASS |
| 移动端无页面级横向溢出 | PASS |
| 浏览器无 JS 运行错误 | PASS |

## 后端自动化测试明细（PostgreSQL 16）


### G4 预备：导入 / 附件

| 测试 | 结果 |
|---|---|
| `test_U09_performance_import_preview_commit_idempotent` | PASS |
| `test_import_rejects_wrong_header_and_xlsm` | PASS |
| `test_product_import` | PASS |
| `test_attachment_signature_and_scope` | PASS |

### POC-A 报价 PDF / 金额 / 版本冻结

| 测试 | 结果 |
|---|---|
| `test_three_templates_generate_pdf` | PASS |
| `test_long_quote_paginates` | PASS |
| `test_frozen_revision_is_immutable_and_v2_reflects_changes` | PASS |
| `test_snapshot_contains_only_visible_params` | PASS |
| `test_empty_quote_cannot_generate` | PASS |
| `test_backend_recalculates_ignoring_client_totals` | PASS |
| `test_negative_and_invalid_inputs_rejected` | PASS |
| `test_quote_codes_unique_and_status_flow` | PASS |
| `test_totals_follow_line_edits_and_deletes` | PASS |

### POC-B 项目多参与方 / 订单 / 业绩

| 测试 | 结果 |
|---|---|
| `test_U01_sales_creates_project_without_customer_then_links_parties` | PASS |
| `test_U02_two_quotes_same_project_different_recipients_and_entities` | PASS |
| `test_lost_quote_does_not_lose_project_and_one_order_one_performance` | PASS |
| `test_U06_repeat_confirm_is_idempotent` | PASS |
| `test_leader_basis_requires_reason_and_leader` | PASS |
| `test_and_mode_requires_leader` | PASS |
| `test_revoke_keeps_trace_and_allows_reuse_of_order_ref` | PASS |
| `test_customer_referenced_by_project_cannot_be_deleted` | PASS |
| `test_manual_performance_is_pending_until_leader_approves` | PASS |

### POC-C 权限矩阵 / 案例脱敏

| 测试 | 结果 |
|---|---|
| `test_quote_list_access[admin-200]` | PASS |
| `test_quote_list_access[leader-200]` | PASS |
| `test_quote_list_access[assistant-200]` | PASS |
| `test_quote_list_access[sales-403]` | PASS |
| `test_quote_list_access[finance-403]` | PASS |
| `test_U05_sales_finance_cannot_read_quote_detail_revision_or_pdf[sales]` | PASS |
| `test_U05_sales_finance_cannot_read_quote_detail_revision_or_pdf[finance]` | PASS |
| `test_sales_project_detail_and_related_never_contain_amounts` | PASS |
| `test_sales_quote_summary_flag_returns_whitelisted_fields_only` | PASS |
| `test_sales_row_level_scope` | PASS |
| `test_sales_see_all_flag_still_blocks_writes_to_others` | PASS |
| `test_sales_cannot_change_owner` | PASS |
| `test_update_stream_write_matrix[admin-201-201]` | PASS |
| `test_update_stream_write_matrix[leader-201-201]` | PASS |
| `test_update_stream_write_matrix[assistant-201-201]` | PASS |
| `test_update_stream_write_matrix[sales-201-403]` | PASS |
| `test_update_stream_write_matrix[finance-403-201]` | PASS |
| `test_U07_finance_updates_milestone_sales_sees_safe_timeline` | PASS |
| `test_finance_cannot_edit_quotes_or_presale` | PASS |
| `test_product_write_matrix[admin-201]` | PASS |
| `test_product_write_matrix[leader-201]` | PASS |
| `test_product_write_matrix[assistant-201]` | PASS |
| `test_product_write_matrix[sales-403]` | PASS |
| `test_product_write_matrix[finance-403]` | PASS |
| `test_products_cannot_be_deleted` | PASS |
| `test_user_management_admin_only[admin-200]` | PASS |
| `test_user_management_admin_only[leader-403]` | PASS |
| `test_user_management_admin_only[assistant-403]` | PASS |
| `test_user_management_admin_only[sales-403]` | PASS |
| `test_user_management_admin_only[finance-403]` | PASS |
| `test_disabled_user_session_invalidated` | PASS |
| `test_unauthenticated_is_rejected` | PASS |
| `test_login_rate_limited` | PASS |
| `test_performance_amount_field_level[admin-True]` | PASS |
| `test_performance_amount_field_level[leader-True]` | PASS |
| `test_performance_amount_field_level[assistant-True]` | PASS |
| `test_performance_amount_field_level[finance-True]` | PASS |
| `test_performance_amount_field_level[sales-False]` | PASS |
| `test_U08_case_pdf_and_api_contain_no_sensitive_data` | PASS |
| `test_case_release_rejects_unconfirmed_or_unpublishable` | PASS |
| `test_case_release_write_matrix[admin-201]` | PASS |
| `test_case_release_write_matrix[leader-201]` | PASS |
| `test_case_release_write_matrix[assistant-201]` | PASS |
| `test_case_release_write_matrix[sales-403]` | PASS |
| `test_case_release_write_matrix[finance-403]` | PASS |
| `test_audit_log_records_and_redacts` | PASS |
| `test_error_shape_has_request_id` | PASS |

### POC-B 项目多参与方 / 订单 / 业绩

| 测试 | 结果 |
|---|---|
| `test_concurrent_confirm_creates_single_performance` | PASS |
| `test_concurrent_project_creation_gets_unique_codes` | PASS |

### POC-A 报价 PDF / 金额 / 版本冻结

| 测试 | 结果 |
|---|---|
| `test_golden_amounts[2-39000-0.13-0-78000.00-10140.00-88140.00]` | PASS |
| `test_golden_amounts[4-18200-0.13-0-72800.00-9464.00-82264.00]` | PASS |
| `test_golden_amounts[120-126-0.13-0-15120.00-1965.60-17085.60]` | PASS |
| `test_golden_amounts[3-0.335-0.13-0-1.01-0.13-1.14]` | PASS |
| `test_golden_amounts[1-100-0.13-0.05-95.00-12.35-107.35]` | PASS |
| `test_golden_amounts[2.5-1999.99-0.09-0-4999.98-450.00-5449.98]` | PASS |
| `test_golden_amounts[7-33.33-0.06-0.1-209.98-12.60-222.58]` | PASS |
| `test_golden_amounts[1-0-0.13-0-0.00-0.00-0.00]` | PASS |
| `test_golden_amounts[1000-0.01-0.13-0-10.00-1.30-11.30]` | PASS |
| `test_golden_amounts[0.5-28600-0.13-0.03-13871.00-1803.23-15674.23]` | PASS |
| `test_float_is_never_used_for_money` | PASS |
| `test_rmb_upper[88140-\u634c\u4e07\u634c\u4edf\u58f9\u4f70\u8086\u62fe\u5143\u6574]` | PASS |
| `test_rmb_upper[10010-\u58f9\u4e07\u96f6\u58f9\u62fe\u5143\u6574]` | PASS |
| `test_rmb_upper[1001.05-\u58f9\u4edf\u96f6\u58f9\u5143\u96f6\u4f0d\u5206]` | PASS |
| `test_rmb_upper[100000000-\u58f9\u4ebf\u5143\u6574]` | PASS |
| `test_rmb_upper[0.5-\u4f0d\u89d2\u6574]` | PASS |
| `test_rmb_upper[2010000-\u8d30\u4f70\u96f6\u58f9\u4e07\u5143\u6574]` | PASS |

## 已知限制

- 报价模板使用虚构公司资料；三份真实报价单未用于生成任何正式文件。
- 当前 PDF 字体在开发环境为 DroidSansFallback + DejaVu；生产镜像使用 Noto Sans CJK，版面会略有差异，部署后需重新出样张核对。
- 性能未做压测；1～5 人规模下列表与 PDF 生成在开发机上均为秒级以内，正式 SLA 需在目标服务器测量后写入。
