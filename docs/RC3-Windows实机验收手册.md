# RC3 Windows 实机验收手册（T17–T21 增补）

> 适用：`Industrial_Sales_Hub_2.1.0-rc3_LAN_Windows_x64.zip`（SHA256 见同名 `.sha256`）。
> 基础步骤沿用 [`RC2-Windows实机验收手册.md`](RC2-Windows实机验收手册.md)（把其中的 `rc2`/`ISH-RC2-Test` 换成 `rc3`/`ISH-RC3-Test`、证据目录换成 `RC3证据`）。
> 本文件只列 RC3 新增/变更的检查。**开发环境没有 Windows 主机**：下列项目在 Linux 上只有单元/模拟证据（见第 6 节），
> 只有按本手册在 Windows 11 x64 上实际执行并回传证据后，才能记为“Windows 实测 PASS”。在此之前 RC3 结论为 **NO-GO（正式发布）**。

## 0. 准备

同 RC2 手册第 0 节；另外准备：
- 一台有**两块以上网卡**的 Windows 11（例如实体网卡 + Hyper-V 虚拟交换机/VPN 网卡），用于 T18；
- 一个**未安装** VC++ 2015–2022 x64 运行库的干净 VM 快照，用于 T19。

```powershell
$E = "$env:USERPROFILE\Desktop\RC3证据"; New-Item -ItemType Directory -Force $E | Out-Null
function Rec($name, [scriptblock]$cmd) { $o = & $cmd 2>&1 | Out-String; $o | Tee-Object -FilePath "$E\$name.txt"; "exit=$LASTEXITCODE" | Add-Content "$E\$name.txt" }
```

## 1. T17 — 重复安装不删除手工 HTTPS 443 规则

```powershell
cd D:\ISH-RC3-Test
.\01-安装部署.bat -Https                                    # 首次以 HTTPS 模式安装（应用只监听 127.0.0.1）
New-NetFirewallRule -DisplayName 'Industrial Sales Hub HTTPS (443)' -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -Profile Private,Domain -RemoteAddress LocalSubnet
Rec 71-fw-before { Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' | Select DisplayName,Enabled,Profile }
.\01-安装部署.bat -Https                                    # 重复安装
Rec 71-fw-after  { Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' | Select DisplayName,Enabled,Profile }
.\06-检查系统.bat | Tee-Object "$E\71-check.txt"
```
期望：`71-fw-after` 中 `Industrial Sales Hub HTTPS (443)` 仍存在且 Enabled；HTTPS 模式下**没有** `Industrial Sales Hub App (TCP 8000)`；
`06-检查系统` 显示“手工防火墙规则（安装器不会删除）：Industrial Sales Hub HTTPS (443)”。
再以 HTTP 模式卸载/重装各一次（`99-卸载服务.bat` → `01-安装部署.bat`），443 规则仍保留，应用规则名为 `Industrial Sales Hub App (TCP 8000)`。

**HTTPS 不打开 HTTP 登录页（F12）**：上面两次 `-Https` 安装结束时**不应**自动弹出 `http://127.0.0.1:8000/` 浏览器窗口；`02-启动系统.bat` 同样不弹出。截图安装结束画面。

## 2. T18 — 多网卡选择正确 IP

```powershell
Rec 72-nics { Get-NetIPConfiguration | Select InterfaceAlias,IPv4Address,IPv4DefaultGateway,NetAdapter }
.\01-安装部署.bat                                           # 观察安装结束的“局域网地址”与多网卡提示
Rec 72-url { Get-Content '.\客户端快捷方式\工业销售管理系统.url'; Get-Content .\访问地址.txt }
```
期望：首选地址是**有默认网关的实体网卡**（不是 vEthernet/VPN/169.254.x.x）；有多个候选时屏幕列出全部并提示 `-PreferredIp`。
然后指定另一地址重装：`.\01-安装部署.bat -PreferredIp <另一块网卡 IP>`，`.url` 与 `访问地址.txt` 使用该 IP。
（首次安装时本机所有局域网 IP 已写入 `DJANGO_ALLOWED_HOSTS`；若指定的是之后新增的 IP，安装程序会提示把它加入 `config\app.env` 并重启应用服务。）
**第二台设备**用快捷方式中的地址打开登录页（截图，含第二台设备的 IP）。

## 3. T19 — VC++ 返回 3010 时安全重启续装

在“未安装 VC++ 运行库”的快照上：
```powershell
cd D:\ISH-RC3-Test
.\01-安装部署.bat; "exit=$LASTEXITCODE" | Tee-Object "$E\73-first.txt"
```
- 若运行库安装返回 3010：安装程序提示“需要重启后再次运行”，退出码 **3010**；`data\install-state.json` 的 `phase` 为 `vc-reboot-required`；
  **此时没有初始化数据库**（`data\pgdata` 不存在或为空）。
  1. **不重启**直接再次运行 `01-安装部署.bat` → 再次提示需要重启，退出码 3010（不继续、不报数据库损坏）。
  2. 重启 Windows → 再次运行 → 正常完成安装。
- 若该机器实际返回 0（运行库已存在或不需要重启），真实 3010 无法在此机器复现：在同一台机器上用 **Windows PowerShell 5.1** 执行
  `powershell -NoProfile -ExecutionPolicy Bypass -File <源码目录>\deploy\windows\tests\unit-rc3-vc.ps1`（来自源码仓库，安装包内不含测试），
  保存输出；结论记为“3010 流程单元测试（Windows PS5.1）PASS，真实 3010 未复现”，**不得**记为 T19 实机 PASS。
  （安装器的模拟开关要求同时设置模拟钩子与 `data\ALLOW_SIMULATION`，会绕过真实服务注册，只用于开发环境，**不要在验收机上使用**。）
截图与 `logs\install-*.log`、`data\install-state.json` 放入证据目录，并注明是“真实 3010”还是“仅单元测试”。

## 4. T20 — 全新 Windows 11 脱网一键安装 + 重复安装 + 开机自启 + 双机访问

按 RC2 手册第 1、2、5 节执行（断开外网，第二台设备访问），另外完成 RC3 业务 UAT（至少两台设备）：

| # | 操作者 / 设备 | 操作 | 期望 |
|---|---|---|---|
| U1 | 销售 / 设备 B | 新建项目、写一条售前跟进（发生时间回填为昨天） | 显示“发生 昨天 · 录入于 现在” |
| U2 | 财务 / 设备 A | 询价登记：该项目 + 明细（不填价格） | 生成 `XJ-` 编号 |
| U3a | 商务或财务 / 设备 A | 以该询价新建报价（仅草稿或已生成 PDF，**未**标记发送） | 询价变为“报价中”，显示报价编号（不是“已报价”） |
| U3b | 同上 | 该报价生成正式 PDF 后点“标记已发送” | 询价变为“已报价”；之后标记失标仍为“已报价”；报价作废且无其他报价时回到“待报价” |
| U4 | 商务 / 设备 A | 项目页“中标确认 · 转交商务” | 顶部“已中标 · 已转交商务” |
| U5 | 销售 / 设备 B | 刷新项目页 | “销售只读”，无编辑/跟进/参与方按钮 |
| U6 | 商务 / 设备 A | 记录交付“生产中”，销售可见说明“已排产”，内部说明“成本超预算 8 万” | 商务可见内部说明 |
| U7 | 财务 / 设备 A | 记录资金“预付款已到账”，内部说明“到账 36 万” | 两条线同时显示 |
| U8 | 销售 / 设备 B | 刷新项目页、操作记录页签；F12 搜索“36”“8 万” | 看到“生产中/预付款已到账/已排产”；**无金额与内部说明** |
| U9 | 销售 / 设备 B | 我的业绩 → 导出内部 PDF | PDF 有筛选条件、生成人、页码、水印；无金额/合同号 |
| U10 | 负责人 | 撤销中标（填原因） | 销售恢复可编辑；操作记录中出现“撤销中标” |

### 4.1 首期业务收口 C1～C4 现场增补（U11–U14，两台设备，虚构数据）

| # | 操作者 / 设备 | 操作 | 期望（每项截图 + 记录时间） |
|---|---|---|---|
| U11 | 财务 / 设备 A | 报价中心新建报价（模板已签收的主体）→ 添加产品 → 生成正式 PDF → 下载 → 标记已发送；查“历史价格查询” | 正式含税 PDF 可下载，历史价格可查。**同一财务账号**：项目页“合同/订单”无金额列；登记合同时无金额输入框；F12 中调用 `POST /api/order-confirmations/{id}/amount/` 返回 403；业绩台账无“内部金额/内部备注”，编辑业绩时不能改“允许公开”；出具主体处无“负责人签收”按钮 |
| U12 | 负责人 / 设备 A，销售 / 设备 B | 负责人为某销售准备 3 条虚构业绩：已确认、待核实、已撤销各 1 条；销售在“我的业绩”直接点“导出内部 PDF”（下拉保持“仅已确认”），再选“全部状态”导出一次 | 默认 PDF 标题“内部业绩查询结果（仅已确认）”，只含已确认 1 条；“全部状态”PDF 标题注明并含 3 条且每行状态清楚；两份 PDF 都无金额/合同号，有“禁止外发”水印与页码 |
| U13 | 财务 / 设备 A，销售 / 设备 B | 财务在询价登记页对一条已关联报价的询价点“登记单 PDF”；再把一条询价“关闭”后下载；销售在浏览器地址栏直接访问 `/api/inquiries/<id>/pdf/` | 登记单标注“内部询价登记单 · 非对外正式报价 · 不含价格”，只有报价编号无价格；已关闭的可下载；销售 403；负责人在“审计日志”看到 `export_pdf / inquiry` 记录（操作人、时间、SHA256） |
| U14 | 负责人 + 商务 / 设备 A，销售 / 设备 B | 同一项目登记并确认 **2 张**合同 → 商务记录“已交付/完结”不勾选覆盖确认 → 勾选后再记录 → 负责人再确认第 3 张合同 → 销售刷新项目页；另：商务在“阶段配置”尝试修改完结阶段（F12 发 `PATCH /api/stage-defs/<id>/ {"is_final":false}`） | 不勾选时被拒绝（提示有 2 张合同）；勾选后记录成功，状态历史显示“已确认覆盖全部 2 张合同/订单”；第 3 张合同确认后两台设备都显示“完结确认已失效，需重新确认”；修改完结属性返回 400 |

## 5. T21 — 一致性备份、正式恢复、升级失败自动回滚

按 RC2 手册第 4、6 节执行，其中升级测试改为：**先安装 RC2（`baa9ef6` 构建包）并写入业务数据 → 用 RC3 包升级**。
期望：升级后 `/healthz` 为 `2.1.0-rc3`；原有报价 PDF 下载哈希不变；新增“交付与资金状态”面板（RC2 时期已成交项目自动锁定销售写入，
原执行节点状态迁移为交付状态线的初始事件，内部说明“RC3 迁移：由原执行节点…推导”）。
故障注入 `upgrade.switch_half`、`upgrade.migrate`、`upgrade.verify` 每次退出码 56，回到 RC2 且数据/PDF/附件哈希不变。

## 6. 开发环境已有的非 Windows 证据（不计为实机 PASS）

| 项 | 证据 | 性质 |
|---|---|---|
| T17 | `deploy/windows/tests/unit-rc3.ps1`（防火墙删除只针对应用规则；Mock `Get/Remove-NetFirewallRule`）；反例 `docs/evidence/RC3/G5-T17-before-rc2.txt` | PowerShell 7 单元测试，CI windows-2022 用 PowerShell 5.1 执行 |
| T18 | `unit-rc3.ps1`（`Select-LanAddress` 排序：网关实体网卡优先、虚拟/APIPA 靠后、`ISH_PREFERRED_IP` 优先） | 单元测试 |
| T19 | `unit-rc3-vc.ps1`（模拟 3010 → 状态 `vc-reboot-required`；未重启重跑仍 3010；重启后继续） | 单元测试（Linux 上 3010 截断为 194，测试按平台比较） |
| T21 | `docs/evidence/RC3/sim/results.txt`：RC2(baa9ef6) → RC3 升级、5 个阶段故障注入、迁移后失败、断电回滚、篡改包、遇忙、备份/恢复，83 PASS / 0 FAIL | Linux 模拟（真实 pwsh + PostgreSQL 16 + Waitress；服务/ACL/防火墙为模拟） |

## 7. 回传证据清单（RC3 增补）

| # | 内容 | 对应 |
|---|---|---|
| 1 | `71-*.txt`、两次安装结束截图（无 HTTP 弹窗） | T17、F12 |
| 2 | `72-*.txt`、第二台设备访问截图 | T18 |
| 3 | `73-first.txt`、重启前后截图、`install-state.json`、安装日志 | T19 |
| 4 | 第 4 节 U1–U10 截图（两台设备）、操作时间、`logs\app.log` 片段 | T20 |
| 4a | 第 4.1 节 U11–U14 截图、下载的 PDF（虚构数据）、F12 请求/响应截图、审计日志截图 | C1～C4 现场验收 |
| 5 | RC2 → RC3 升级与故障注入日志 `data\ops\upgrade-*.json`、哈希对照 | T21 |

证据中不得包含真实客户资料、报价样张或口令。
