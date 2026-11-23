# RC3 门禁证据（2.1.0-rc3，分支 `feature/rc3-two-boards`，基线 RC2 `baa9ef6`）

> 依据：《RC2 两板块需求独立审计报告》（F01–F15、T01–T24）与《RC3 两板块开发任务书》（G0–G6）。
> 结论：**NO-GO（正式发布）**——代码门禁 G0–G4 通过；G5 代码与单元/模拟证据通过但 **Windows 实机未执行**；G6 远端同 SHA CI、模板签收、财务黄金用例未完成。
> 可用于隔离测试机与受控现场试用。未合并 main、未打正式 tag。

## 1. 门禁状态

| 门禁 | 内容 | 状态 | 证据 |
|---|---|---|---|
| G0 | 两板块、询价 vs 报价、财务口径、角色与字段矩阵（CI 请求矩阵） | **PASS（代码）**；D-1/D-2 业务口径待甲方确认 | `docs/RC3-业务角色与字段矩阵.md`；`test_rc3_g0_g2_business.py::test_business_role_request_matrix`（15 行 × 5 角色）、`test_matrix_doc_in_sync` |
| G1 | 中标转交锁定销售写入（P0） | **PASS** | `test_rc3_g1_handoff.py`（12）；反例 `G1-before-baa9ef6.txt`（RC2 上 4 项失败：200/201/(201,201)/can_edit=True） |
| G2 | 交付/资金两条状态线、公开/内部说明、阶段配置 | **PASS** | `test_rc3_g0_g2_business.py`（T06–T10、T24、迁移、资金线内部说明不经进展流外泄给商务——封板前复审发现并修复于 8a198b3） |
| G3 | 内部安全版业绩 PDF | **PASS** | `test_rc3_g3_internal_pdf.py`（5）；反例 `G3-before.txt`（RC2 无此接口，5 项失败）；样张 `内部业绩PDF-虚构样张.pdf`（虚构数据） |
| G4 | 字段级审计、发生/录入时间、操作记录 | **PASS** | `test_rc3_g4_audit.py`（5）；反例 `G4-before-baa9ef6.txt`（RC2 上 4 项失败） |
| G5 | Windows 安装加固 T17–T21 | **代码 PASS / 实机 未执行** | `deploy/windows/tests/unit-rc3.ps1`（T17/T18）、`unit-rc3-vc.ps1`（T19）；反例 `G5-T17-before-rc2.txt`；`sim/results.txt`（RC2→RC3，83/0）；实机见 `docs/RC3-Windows实机验收手册.md` |
| G6 | 无回退、同 SHA CI、签收、交付 | **本地 PASS / 远端 CI 待推送 / 签收未完成** | 后端 392/392（基线 292 全部保留）；E2E 116/116（基线 72 全部保留）；`e2e/e2e_v21_lan_results.json` |

本地测试环境：Linux 沙箱，Python 3.12，PostgreSQL 16（pgserver），Chromium（Playwright），PowerShell 7.4 + PSScriptAnalyzer 1.22.0（0 告警）。

## 1.1 基于 5c83523 的独立审计（2026-11-22）后续修复

| 编号 | 发现 | 修复 | 反例（修复前失败） |
|---|---|---|---|
| V-01（P1） | 撤销状态记录时 `revert_reason` 写入执行进展 `content`，无 `payment.write` 的商务可经 `projects/{id}/updates/` 读到资金线撤销原因 | 原因只保存在 `TrackEvent.revert_reason`（`tracks/` 按该线写权限返回）；进展流只记固定文案 | `V01-before-5c83523.txt`；`test_V01_revert_reason_stays_in_track_event`（2 条线 × 5 角色 × 6 个接口） |
| V-02（P1） | 仅创建报价草稿即把询价改为“已报价” | 新增“报价中”；已报价 = 关联报价已标记发送（`Quote.sent_at`）；作废/改关联重算；人工仅关闭/重新打开；迁移回填 | `V02-before-5c83523.txt`；`test_V02_*`（3 项）；E2E 浏览器检查 |

两项均已关闭（代码 + 反例测试）。审计中的 D-01（财务报价权限）、D-02（询价单含义）、D-03（内部 PDF 默认含待确认业绩）、D-04（多合同分批）为业务口径，列入 `docs/剩余问题.md` D 节等待负责人/甲方决定，未擅自修改。

## 1.2 首期业务收口 C1～C4（2026-11-22 负责人决策 D-1～D-7）

| 项 | 决策 | 实现 | 测试 | 反例（02b5464 上失败） |
|---|---|---|---|---|
| C1 | D-2 财务报价与金额权限解耦 | `FINANCE_QUOTE_CAPS={quote.access, product.see_price}`；订单 `OrderQuoteSerializer`（见报价编号、不见合同金额） | `test_rc3_closeout_c1.py`（4）；矩阵 M06–M08、M16、M17 | `C1-before-02b5464.txt`（财务建报价 403；旧开关授予 order.amount/performance.see_amount） |
| C2 | D-6 内部业绩 PDF 默认仅已确认 | `export_status` 显式（默认 CONFIRMED），列表 `status` 不参与，标题/摘要注明 | `test_rc3_closeout_c2.py`（6） | `C2-before-02b5464.txt`（无参数导出 3 条含待核实/已撤销） |
| C3 | D-7 内部《项目询价登记单》PDF | `GET /inquiries/{id}/pdf/`，白名单字段、不含价格、审计含 SHA256 | `test_rc3_closeout_c3.py`（3） | `C3-before-02b5464.txt`（接口 404） |
| C4 | D-5 多合同 = 项目总体状态 | 摘要 `scope/active_order_count/multi_order`；完结须 `confirm_all_covered`，事件与审计留痕 | `test_rc3_closeout_c4.py`（5） | `C4-before-02b5464.txt`（无标识；两张订单直接记完结 201） |
| 工程 | 迁移稳定性 | `quotations/0005` 内联冻结函数；迁移不得 import apps.*（静态检查）；空库迁移与 0005 回退/重做 | `test_migrations_do_not_import_runtime_app_code`、`test_V02_migration_helper_matches_runtime` | — |

样张（虚构数据，以最终代码生成）：`内部询价登记单-虚构样张.pdf`、`内部询价登记单-长文本分页-虚构样张.pdf`、`内部业绩PDF-默认仅已确认-虚构样张.pdf`。

## 1.3 对 74616ed 独立审查的定向修复（2026-11-22）

| 编号 | 发现 | 修复前反例（真实 HTTP） | 修复 | 回归 |
|---|---|---|---|---|
| P0-01 | 商务可 `PATCH /stage-defs/{id}/ {"is_final":false}` → 不确认覆盖记录“已交付/完结” → 改回，项目显示完结但无覆盖确认 | `R74-before-74616ed.txt` 第 1 节完整链：409 → 200 → 201 → 200，摘要 is_final=true / covers_all_orders=false | `is_final` 只读；显式修改 400；新增阶段一律非完结 | `test_P0_01_*`（3）；E2E 400/409 |
| P1-02 | 完结后新增/撤销/替换合同，完结状态与覆盖确认不变 | `KeyError: 'coverage_stale'`（无任何失效判断） | `TrackEvent.covered_order_ids`（迁移 0006 按时间回填）；按订单 ID 集合比较得出 `coverage_stale`/`needs_reconfirmation`；失效时不算交付完结；页面红色提示并要求重新确认 | `test_P1_02_*`（4，含“数量相同成员不同”与迁移回填）；E2E 截图 `e2e/14_*` |
| P1-03 | 阶段改名后历史事件显示新名称 | 历史 label = “排产与制造中”（应为“生产中”） | 历史 `label`=`label_snapshot`，另给 `current_label`；当前摘要用最新名称 | `test_P1_03_*` |
| P1-04 | 安装手册/包内使用说明仍写 rc2 与“财务看不到报价”；交付报告旧 G0 节矛盾；追踪矩阵旧口径 | 文档核查 | 统一为 D-2 口径；追踪矩阵第 1–6 节加“旧口径，以 RC3 矩阵为准”提示；交付报告旧节改写 | 包内 `使用说明.md` 随构建更新 |
| P1-05 | Windows 实机手册未覆盖 C1～C4 | — | 新增 U11–U14（财务报价与金额隔离、三状态业绩 PDF、询价登记单与审计、两/三合同覆盖确认与失效、完结属性篡改 400） | 需实机执行 |
| P2-06 | 询价单“不含价格”表述过度（自由文本可能人工写价格） | — | 改为“不含价格字段”，PDF 与表单提示需求说明/备注勿填价格、外发前人工核对；不引入金额识别 | `test_C3_*` |
| P3-07 | CI 日志 1 条 SyntaxWarning（`import_entity_config.py` 文档字符串 `\ `） | CI Run 45024116355 后端日志 | 原始字符串 | 后端以 `-W error::SyntaxWarning` 运行通过 |

## 1.4 对 6912049 第二轮审查的定向修复（2026-11-23）

| 编号 | 发现 | 修复前反例（真实 HTTP / 迁移） | 修复 | 回归 |
|---|---|---|---|---|
| R75-01 | 单合同撤销（1→0）或替换（1→另一张）后完结确认不失效；失效后可不确认直接重记完结 | `R75-before-6912049.txt`：两条线 1→0、1→另一张 `coverage_stale=False`；重记返回 201 | 失效判定改为“完结后合同集合任何变化”；该线原完结已失效时重记完结须 `confirm_all_covered`（否则 409） | `test_R75_01_*`（11：两条线 × 4 种变化、重记、合法状态不误伤） |
| R75-02 | 迁移前“当时 ≥2 张合同、未确认覆盖”的完结，回填 `covered_order_ids` 后被视为有效 | 同上：迁移后 `coverage_stale=False` | 记录当时 ≥2 张却未确认 → 失效、提示重新确认；当时已确认、单合同保持有效；不伪造确认 | `test_R75_02_*`（3） |
| R75-03 | 可新建与“已交付/完结”同名的非完结阶段（假完结） | 同上：创建返回 201 | 同一状态线展示名（NFKC、去空白、忽略大小写）不得重复，新建/改名撞名 400 | `test_R75_03_*`；E2E |
| R75-04 | 实机手册 U3 期望“已报价”过期 | 文档 | 拆为 U3a（草稿/未发送→报价中）、U3b（标记发送→已报价） | `test_uat_inquiry_status_expectation_matches_v02` |
| R75-05 | HTTPS 手册（含包内）用 `Industrial Sales Hub*` 通配删除防火墙规则，会删手工 HTTPS 443 规则 | 文档 | 改为精确过滤应用规则或重跑 `-Https` 安装；静态守卫禁止文档/脚本再出现通配删除（对旧文档验证可识别） | `test_no_wildcard_firewall_rule_removal_in_docs_and_scripts` |
| R75-06 | 询价登记单样张为旧文案 | — | 以当前代码重新生成虚构样张，并新增 45 行明细的长文本分页样张（3 页，表头重复） | 人工核对渲染 |
| R75-07 | 交付报告章节顺序、旧 G0 默认值描述、包内计数口径 | — | 已修正（见交付报告） | — |

## 1.5 对 5753b5f 第三轮业务验收的定向修复（2026-11-23）

| 编号 | 发现 | 修复前反例 | 修复 | 回归 |
|---|---|---|---|---|
| R76-P1-01 | 中标已转交、尚无合同、已有交付/资金状态的项目，在销售常用进度入口中找不到 | `R76-before-5753b5f.txt`：另一位销售首页“全团队执行中项目”为空、KPI=0、进度页默认列表无该项目（截图 `r76-before/`），项目 `execution_phase` 仍为“未启动”；HTTP 用例 2 项失败 | 列表 API `tracking=1/all` + 每项 `status` 安全摘要（批量 3 次查询）；销售首页与进度页默认“中标跟踪中”，可切换“含已完结”；旧执行阶段不伪造，保留为高级筛选 | `test_rc3_review_5753b5f.py`（3，含 30 项目查询数上限、管理员 403、无金额）；E2E（另一位销售首页/进度页，截图 `r76-after/`） |
| R76-P1-02 | 北京时间凌晨表单默认业务日期为前一天 | Node 固定时刻：2026-11-23 02:00（北京）→ `toISOString` 得 2026-11-22 | `todayBusinessDate()`（Asia/Shanghai），三处表单复用；不改数据库与审计时间 | `frontend/tests/todayBusinessDate.test.mjs`（8 个边界 × 3 个浏览器时区，CI 运行）；E2E 询价默认日期 |
| R76-P2-03 | “需重新确认”提示断言“合同已变化”，对历史缺少确认的情形不准确 | 源码 | 中性描述“合同变化或缺少覆盖确认”，后端 409 文案同步 | E2E 文案检查 |
| R76-P2-04 | 列表只显示旧执行阶段与日期 | 源码 | 列表/工作台显示交付/资金（项目总体）与资料最后修改人/时间 | E2E |

现场增补：`RC3-Windows实机验收手册.md` 第 4.2 节 U15–U17。

## 1.6 对 acc7198 第四轮审查的定向修复（2026-11-23）

| 编号 | 发现 | 修复前反例 | 修复 | 回归 |
|---|---|---|---|---|
| R77-P1-01 | `dt()` 把纯日期解析成 UTC 午夜，西半球时区显示前一天；时间戳按浏览器当地时区显示，不同电脑看到不同时刻 | `R77-before-acc7198.txt`：dt 测试在 acc7198 的 api.ts 上，UTC 失败 4 项、America/Los_Angeles 失败 8 项 | 纯日期原样显示；时间戳统一按北京时间显示（`fmtBeijing`）；状态摘要单元格也改用 `dt()` | `frontend/tests/dt.test.mjs`（直接导入真实 `api.ts`，8 个用例 × 3 个时区，CI 运行）；E2E：洛杉矶时区与北京时区的浏览器显示相同 |
| R77-P2-02 | 进度页卡片加载详情后，只剩阶段标签 | 浏览器反例：`exec_card_has_recorder=false`（截图 `r77-before/`） | 卡片始终显示安全摘要（含日期和记录人），无状态时显示“尚未录入”；只有存在旧执行节点的卡片才请求详情 | E2E（截图 `r77-after/`） |
| R77-P2-03 | 状态历史的撤销行不显示撤销时间 | 浏览器反例：`revert_time_shown=false` | 撤销行显示“撤销人 · 撤销时间 · 原因” | 浏览器复测 |
| R77-P2-04 | 跨团队浏览售前项目时存在 N+1 查询 | 销售 B 浏览销售 A 的 1/10/30/100 个售前项目，SQL 次数 9/18/38/108，线性增长（已中标项目因提前判断返回，不受影响） | `can_edit` 的成员判断改用已预取的成员数据 | `test_rc3_review_acc7198.py`：三种列表的 SQL 次数恒为 7，另加成员语义回归测试 |

## 2. 三个必须解释的问题

### 2.1 为什么先锁定中标后的销售写权限（G1 先于其他门禁）

- **它是唯一的“数据正在被改坏”的问题**。RC2 中项目“成交”只是 `presale_phase` 的一个值，销售在合同确认前后都能继续改项目资料、参与方、售前跟进和附件（反例：RC2 上 `PATCH` 返回 200、`POST updates` 201、`can_edit=True`）。
  中标之后的项目资料是商务报价、合同、交付和业绩的依据；销售此时再改，会让后续的报价/订单/业绩与项目档案不一致，而且这种不一致事后难以发现。
- **其他门禁都依赖“中标”这个事件**：两条状态线只在中标后开始记录（G2）；操作记录要能看到“谁在何时转交”（G4）；财务进入商务板块后，商务/财务与销售的分工边界也以中标为界（G0）。
  先把“中标确认/转交”做成独立、可追溯的事件（`won_at/won_by/sales_locked_at` + 审计 + 进展记录），后续门禁才有稳定的锚点。
- **它可以早于合同**：甲方的实际流程是“中标通知 → 签合同 → 生产”，中标到签合同之间常有数周；RC2 只在订单确认时才进入执行，中间这段时间销售仍可写。RC3 的中标确认独立于订单。
- 锁定在**服务端统一入口** `projects/scope.py::assert_sales_writable` 实现，覆盖项目、参与方、进展、附件的全部写接口；读接口不受影响；前端只按 `can_edit` 隐藏按钮。撤销中标仅负责人、必须写原因、已有有效订单时拒绝；订单撤销不解锁。

### 2.2 预付款等资金状态如何不泄露金额（G2）

1. **数据模型上没有金额字段**：资金状态线 `TrackEvent(track=PAYMENT)` 只有阶段代码（未确认/预付款已到账/尾款待收/已结清/有争议）、发生日期、操作人、系统时间、`public_note`、`private_note`。不存在可以“漏”的金额列。
2. **自由文本分为两栏**：`public_note`（销售可见，页面提示“勿填金额”）与 `private_note`（内部）。`track_summary()` 与 `TrackEventSerializer` 对没有对应写权限的角色**不输出** `private_note` 键（不是置空，而是字段不存在）。
   状态事件同步到项目进展流时只带公开说明，内部说明只保存在状态事件本身（封板前复审发现资金线内部说明曾可经进展流被商务读到，已修复并加回归测试）。
3. **原有自由文本也收口**：RC2 中销售能读到的执行进展 `content`、执行节点 `note/delay_reason` 可能含“预付款 30 万已到账”（审计 F15/T24）。RC3 对销售只返回 `public_content`/`public_note`（销售本人写的售前跟进除外）。
4. **操作记录只给动作**：销售的活动时间线只有动作名、操作人、时间，不含摘要和字段名。
5. **测试**：`test_T24_internal_amount_text_never_reaches_sales` 在 6 个销售可访问接口（项目详情、进展、节点、状态线、项目列表、工作台）中搜索“30 万/12 万/88 万/300000”均不存在；E2E 在真实浏览器中以销售身份检查页面与接口均无“36 万/8 万/内部：”。
6. **残余风险**：录入人把金额写进 `public_note` 是人为错误，系统无法可靠识别（已列入剩余问题 E-5）；页面在该输入框标注“勿填金额”。

### 2.3 为什么内部销售业绩 PDF 必须与对外案例 PDF 分离（G3）

| | 内部业绩 PDF（RC3） | 对外案例册（RC2 已有） |
|---|---|---|
| 目的 | 销售/商务内部查看、汇报、核对本人业绩 | 发给客户的资质材料 |
| 数据范围 | 当前用户可见范围内的筛选结果（销售仅本人），**包括未公开、待确认的业绩** | 仅“已确认 + 负责人批准公开”的业绩 |
| 字段 | 10 个内部白名单字段（可含主机厂、业主名称） | 公开白名单字段；业主名需单独授权 |
| 标识 | “公司内部资料 · 禁止外发 / INTERNAL · 不是对外案例”水印与页眉 | 正式案例版式，可撤回（撤回后 410） |
| 记录 | 审计（筛选条件、行数、PDF SHA256），不生成发布记录 | `CaseRelease` 发布记录、预览人工确认 |

如果合并成一个导出：要么内部 PDF 只能包含已公开业绩（无法满足“筛选本人业绩导出”需求，审计 F04），要么对外 PDF 会混入未授权公开的业主名称、未确认业绩（违反 D 系列决策与客户保密）。
分成两个接口、两套字段白名单、两种明显不同的版式标识，可以在服务端保证“未经负责人批准的数据不可能出现在对外 PDF”，同时内部 PDF 带“禁止外发”水印降低误发风险。
两者**都不含**内部金额、合同号、单价、内部备注（测试在 PDF 文本与二进制流中均检索）。测试 `test_T13_internal_pdf_is_not_a_public_case` 证明内部导出不产生 `CaseRelease`，且未公开业绩仍被对外案例拒绝。

## 3. 审计问题 F01–F15 关闭情况

| # | 问题 | 处理 | 测试 |
|---|---|---|---|
| F01 | 中标后销售未只读 | G1 | T01–T05 |
| F02 | 财务权限与新要求冲突 | `ISH_FINANCE_SCOPE=BUSINESS`（默认）/`RC2`；`ISH_FINANCE_QUOTE` | 请求矩阵、`test_rc2_scope_switch_restores_d5`、T06/T07 |
| F03 | 执行状态不能表达资金/供货 | 两条状态线 + 阶段配置 | T08–T10 |
| F04 | 业绩 PDF 不完整 | 内部安全版 PDF | T11–T13 |
| F05 | 询价单 ≠ 报价单 | 询价登记（无价格）+ 报价关联询价；口径 D-1 待确认 | T06 |
| F06 | 关键变更无审计 | 报价条款/行、联系人、项目、业绩字段级审计 | T14 |
| F07 | 操作时间与发生时间混淆 | `happened_at` 可回填不可未来；`created_at` 服务端；页面分别显示 | T15 |
| F08 | 未收敛为两大入口 | 商务工作区 / 销售工作区 / 技术设置 | E2E |
| F09 | 重复安装删 HTTPS 规则 | 应用规则专用前缀 | T17（单元 + 反例） |
| F10 | VC++ 3010 | 重启续装状态 | T19（单元） |
| F11 | 多网卡选错 IP | 网关实体网卡优先、`-PreferredIp` | T18（单元） |
| F12 | HTTPS 打开 HTTP 页 | HTTPS 模式不打开 | 静态检查 + 实机手册 |
| F13 | 多订单共享进度 | **未改**：一个项目一条主交付线，分批写说明；列入 D-5 | — |
| F14 | 陈旧描述 | 报价中心副标题、权限矩阵、api.md、安装/HTTPS 手册更新 | `test_rc2_api_doc` |
| F15 | 执行自由文本含金额 | `public_*` 字段 + 销售裁剪 | T24 |

## 4. 测试 T01–T24 对照

| T | 自动化 | Windows 实机 / 现场 |
|---|---|---|
| T01–T05 | `test_rc3_g1_handoff.py` PASS；E2E PASS | — |
| T06–T10 | `test_rc3_g0_g2_business.py` PASS；E2E PASS | RC3 手册 U2–U8 |
| T11–T13 | `test_rc3_g3_internal_pdf.py` PASS；E2E PASS | RC3 手册 U9 |
| T14–T16 | `test_rc3_g4_audit.py` PASS；E2E（操作记录）PASS | — |
| T17 | 单元测试 PASS（反例成立） | **未执行** |
| T18 | 单元测试 PASS | **未执行** |
| T19 | 单元测试 PASS | **未执行** |
| T20 | — | **未执行** |
| T21 | Linux 模拟 83/0 | **未执行** |
| T22 | — | **未完成**（负责人签收、财务 10 组） |
| T23 | — | **待推送**（同 SHA 同 Attempt 六项） |
| T24 | `test_T24_…` PASS；E2E PASS | — |

## 5. 可重复命令

```bash
cd backend && DATABASE_URL=postgres://… python -m pytest -q                       # 392 passed
python -m pytest -q tests/test_rc3_*.py                                           # RC3 新增 99 项（tests/test_rc3_*.py）
# E2E：见 e2e/e2e_v21_lan.py 头部（需 LAN 模式 Waitress + 已构建前端）
pwsh -File deploy/windows/tests/unit-rc3.ps1; pwsh -File deploy/windows/tests/unit-rc3-vc.ps1
ISH_SIM_TOOLS=<含 env/venv env/pwsh 的目录> bash deploy/windows/tests/sim/run_sim.sh <输出目录>   # 需 PostgreSQL
```
反例复现：`git worktree add /tmp/base baa9ef6` 后把 `backend/tests/test_rc3_g1_handoff.py`（或 g3/g4）复制进去运行，即得到 `G*-before*.txt` 中的失败。
