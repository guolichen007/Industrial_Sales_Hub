# RC2 Windows 实机验收手册（R4）

> 适用：`Industrial_Sales_Hub_2.1.0-rc2_LAN_Windows_x64.zip`（SHA256 见同名 `.sha256`）。
> 开发环境**没有 Windows 主机**：本手册给出负责人在可控 Windows 11 x64 测试机/干净 VM 上的**精确命令与证据清单**。
> 只有按本手册实际执行并回传证据后，相关门禁才能记为“Windows 实测 PASS”。Linux 上的模拟验收（`deploy/windows/tests/sim`）与静态检查
> 不能代替本手册。**禁止**在存有真实业务数据的主机上做第 4、6、7 节的破坏性测试。

## 0. 测试机准备与记录约定

| 项 | 要求 |
|---|---|
| 主机 | 干净 **Windows 11 x64 中文版**（家庭版/专业版均可），不安装 Python/Node/PostgreSQL；建议 VM 并在第 1 步前**做快照** |
| 可选 | Windows Server 2019/2022（如做，单独记录；未做则手册结论标“Server 未验证”） |
| 目录 | `D:\ISH-RC2-Test`（没有 D 盘用 `C:\ISH-RC2-Test`）；空格路径测试用 `D:\ISH RC2 Space` |
| 账号 | 一个本机管理员账号 + 一个**普通用户**账号 `ishuser`（非管理员组），用于权限测试 |
| 第二台设备 | 同一交换机/路由器下的另一台电脑或手机（第 5 节） |
| 证据目录 | 在测试机桌面建 `RC2证据\`，所有截图/日志/命令输出放这里，最后整体压缩回传 |

每一步都记录：**时间、执行的命令、退出码、关键输出截图**。命令均在“以管理员身份运行”的 PowerShell 中执行，除非注明“普通用户”。

```powershell
# 证据记录辅助：把命令输出同时保存到证据目录
$E = "$env:USERPROFILE\Desktop\RC2证据"; New-Item -ItemType Directory -Force $E | Out-Null
function Rec($name, [scriptblock]$cmd) { $o = & $cmd 2>&1 | Out-String; $o | Tee-Object -FilePath "$E\$name.txt"; "exit=$LASTEXITCODE" | Add-Content "$E\$name.txt" }
Rec 00-os { Get-ComputerInfo | Select-Object OsName,OsVersion,OsArchitecture,CsTotalPhysicalMemory,OsLanguage; Get-Culture; [Text.Encoding]::Default.EncodingName }
```

## 1. 安装包完整性与 RC1 反例（先验证问题存在）

```powershell
cd D:\
Get-FileHash .\Industrial_Sales_Hub_2.1.0-rc2_LAN_Windows_x64.zip -Algorithm SHA256   # 与 .sha256 文件比对，截图
Unblock-File .\Industrial_Sales_Hub_2.1.0-rc2_LAN_Windows_x64.zip
Expand-Archive .\Industrial_Sales_Hub_2.1.0-rc2_LAN_Windows_x64.zip -DestinationPath D:\ISH-RC2-Test-unzip
Move-Item D:\ISH-RC2-Test-unzip\Industrial_Sales_Hub_2.1.0-rc2_LAN_Windows_x64 D:\ISH-RC2-Test
```

**RC1 反例（可选但建议，用快照前的干净机）**：先装 RC1（`96da94b5…` 包）到 `D:\ISH-RC1`，在系统设置中把主体 A 名称改成“反例测试”，再运行一次 `01-安装部署.bat`，
观察主体名称被改回占位（RC1 问题 R01），截图后恢复快照。RC2 的对应结果见第 2.3 步。

## 2. 首次安装、重复安装、重启

### 2.1 离线首次安装
断开外网（拔网线或禁用 WAN，保留局域网），然后：
```powershell
cd D:\ISH-RC2-Test
.\01-安装部署.bat            # 按提示输入管理员用户名与密码（两次，不回显）
```
检查并截图：安装结束的“安装完成”与局域网地址；`logs\install-*.log` **不含**管理员密码（`Select-String -Path logs\install-*.log -Pattern <你输入的密码>` 应无结果）。
```powershell
Rec 21-check { .\06-检查系统.bat }
Rec 21-services { Get-CimInstance Win32_Service -Filter "Name like 'IndustrialSalesHub%'" | Select Name,State,StartMode,StartName,PathName | Format-List }
Rec 21-listen { Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 8000,55432 | Select LocalAddress,LocalPort,OwningProcess }
Rec 21-fw { Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' | Get-NetFirewallAddressFilter; Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' | Select DisplayName,Profile,Enabled }
```
期望：两个服务 Running、Automatic（应用为延迟自动），StartName 为 `NT SERVICE\IndustrialSalesHub-*`；**55432 只在 127.0.0.1**；8000 在 0.0.0.0；防火墙规则仅 Private/Domain + LocalSubnet。

### 2.2 浏览器无 CDN、无公网依赖
本机浏览器打开 `http://127.0.0.1:8000/`，F12 → Network，勾选“Disable cache”刷新：所有请求都指向 127.0.0.1（截图）。

### 2.3 重复安装不改数据（R01）
1. 用管理员登录，建负责人账号 `leader`；用负责人登录，把主体 A 全称改为“验收甲测试有限公司”，并上传一个 Logo；在“出具主体”里**签收**主体 A（写依据“实机测试”）。
2. 建一个项目、一份报价并生成正式 PDF；记录 PDF SHA256（报价详情页显示）。
3. 记录指纹：
```powershell
$env:ISH_CONFIG_FILE="D:\ISH-RC2-Test\config\app.env"
cd D:\ISH-RC2-Test\app\backend
Rec 23-before { ..\..\runtime\python\python.exe -X utf8 manage.py verify_integrity; Get-FileHash -Algorithm SHA256 (Get-ChildItem ..\..\data\private -Recurse -File).FullName | Format-Table -AutoSize }
cd D:\ISH-RC2-Test; .\01-安装部署.bat
cd D:\ISH-RC2-Test\app\backend
Rec 23-after { ..\..\runtime\python\python.exe -X utf8 manage.py verify_integrity; Get-FileHash -Algorithm SHA256 (Get-ChildItem ..\..\data\private -Recurse -File).FullName | Format-Table -AutoSize }
```
期望：`23-before` 与 `23-after` 文件哈希完全一致；主体名称、Logo、**签收状态**不变；账号未增减；安装日志出现“已存在，保持不变：A”。

### 2.4 重启与无人登录
重启 Windows，**不要登录**；用第二台设备访问 `http://<主机IP>:8000/healthz`（返回 `"status":"ok"`）。登录后 `Rec 24-after-reboot { .\06-检查系统.bat }`。

### 2.5 正常/异常停止
```powershell
.\03-停止系统.bat               # 输入 Y；然后 02-启动系统.bat
Stop-Process -Name python -Force # 模拟应用崩溃：WinSW 应在 10 秒内自动重启
Start-Sleep 20; Rec 25-autorestart { Get-Service IndustrialSalesHub-App; Invoke-WebRequest http://127.0.0.1:8000/healthz -UseBasicParsing | Select StatusCode,Content }
```

## 3. 安装防护与权限

### 3.1 路径与重名服务
```powershell
# 中文路径：应拒绝（退出码 14）
Expand-Archive <zip> -DestinationPath "D:\工业销售测试"; & "D:\工业销售测试\Industrial_Sales_Hub_2.1.0-rc2_LAN_Windows_x64\01-安装部署.bat"
# 特殊字符路径（&）：应拒绝（14）
# 空格路径 D:\ISH RC2 Space：允许，但同名服务已属于 D:\ISH-RC2-Test → 应拒绝（退出码 18，提示属于另一个安装目录）
```
截图三种结果。（若要验证空格路径能完整安装，需先在 `D:\ISH-RC2-Test` 运行 `99-卸载服务.bat` 再装到空格路径，完成后卸载并恢复原安装。）

### 3.2 普通用户读取秘密/修改 SYSTEM 脚本被拒（用普通用户 `ishuser` 登录执行）
```powershell
Get-Content D:\ISH-RC2-Test\config\app.env                     # 期望：拒绝访问
Get-ChildItem D:\ISH-RC2-Test\data\private                     # 期望：拒绝访问
Get-ChildItem D:\ISH-RC2-Test\backups                          # 期望：拒绝访问
Get-ChildItem D:\ISH-RC2-Test\logs                             # 期望：拒绝访问
Add-Content D:\ISH-RC2-Test\scripts\backup.ps1 "# tamper"      # 期望：拒绝访问（计划任务以 SYSTEM 执行此脚本）
Set-Content D:\ISH-RC2-Test\runtime\python\x.txt "x"           # 期望：拒绝访问
```
管理员执行 `Rec 32-acl { icacls D:\ISH-RC2-Test; icacls D:\ISH-RC2-Test\config; icacls D:\ISH-RC2-Test\data; icacls D:\ISH-RC2-Test\logs; icacls D:\ISH-RC2-Test\backups }`，
确认敏感目录无 `BUILTIN\Users`/`Everyone`/`Authenticated Users`；中文系统中显示为“BUILTIN\Users”或“BUILTIN\用户”均可（脚本按 SID 授权）。
**说明**：Windows 管理员在操作系统层拥有绝对控制权，这不属于应用权限隔离范围。

### 3.3 磁盘不足（可选，VM 推荐）
在 VM 中挂一个 3 GB 的虚拟盘 `F:`，解压并安装到 `F:\ISH` → 期望拒绝（退出码 15，“至少需要 5 GB”）。

### 3.4 安装中断恢复
在 2.1 前的快照上：运行 `01-安装部署.bat`，看到“初始化 PostgreSQL”后立即关闭窗口；再次运行 `01-安装部署.bat` → 期望：提示“检测到上次中断的数据库初始化…已移到 …incomplete-…”，然后正常完成；
`config\admin-only\postgres-superuser.txt` 存在且仅管理员可读。

## 4. 备份、计划任务、恢复（R02/R04/R05）

```powershell
.\04-立即备份.bat
.\08-配置每日备份.bat            # 然后在“任务计划程序”中右键 IndustrialSalesHub-DailyBackup → 运行；截图“上次运行结果 0x0”
notepad config\app.env           # 设置 ISH_BACKUP_COPY_TO=E:\ISH-copy（另一块盘或 U 盘）；再次运行 04，确认 E:\ISH-copy 下有同名备份
Rec 41-backups { Get-ChildItem backups; Get-Content data\backup_status.json }
```
**并发互斥（R05）**：同时打开两个管理员 PowerShell，几乎同时运行 `.\04-立即备份.bat` → 一个成功，另一个“另一个运维任务正在进行”退出码 75；`data\MAINTENANCE` 在成功的备份结束后才消失；
另开窗口在备份进行中运行 `.\07-升级程序.bat`（从新包目录，见第 6 节）→ 同样遇忙退出。截图并收集 `data\ops.history.log`。

**空库恢复**：在全新安装（只有管理员账号）上运行 `04` 得到空备份 → 写入一些业务数据 → `.\05-恢复备份.bat -BackupPath <空备份> -Production`（输入“确认恢复”）→ 业务数据回到空、`data\private` 为空目录、应用正常。
**有业务恢复**：备份 → 上传一个约 15 MB 的附件并新建报价 → 正式恢复 → 附件与新报价消失、旧 PDF 哈希不变（`verify_integrity` 通过）。
**演练**：`.\05-恢复备份.bat -BackupPath <备份>`（不加 -Production）→ “恢复演练通过；正式数据未被修改”。
**撤销/清理**：正式恢复成功后记录屏幕上的恢复 ID，运行 `.\05-恢复备份.bat -Rollback <ID>` 回到恢复前；确认无误后 `-Cleanup <ID>`。
**故障注入（仅测试机）**：
```powershell
New-Item D:\ISH-RC2-Test\data\ALLOW_FAULT_INJECTION -ItemType File
$env:ISH_FAULT_INJECT='restore.switch_files'; .\05-恢复备份.bat -BackupPath <备份> -Production   # 期望：退出码 40，“已自动回退”，数据为恢复前
$env:ISH_FAULT_INJECT='restore.ps1.start';    .\05-恢复备份.bat -BackupPath <备份> -Production   # 期望：退出码 41，撤销切换后应用回到恢复前
Remove-Item env:ISH_FAULT_INJECT; Remove-Item D:\ISH-RC2-Test\data\ALLOW_FAULT_INJECTION
```
收集 `data\ops\restore-*.json/.log`。

## 5. 第二台设备、断开外网、五账号协作（不能用单机多浏览器代替）

1. 主机与客户端接同一交换机/路由器；**断开路由器 WAN 口**（或在路由器后台禁用外网），保留局域网。
2. 客户端浏览器打开 `访问地址.txt` 中的地址（`http://<主机IP>:8000/`）。
3. 五个账号（管理员、负责人、商务、销售、财务）至少分布在两台设备上同时登录：
   - 商务创建报价 → 因模板已签收（2.3）生成正式 PDF → 标记“已发送”；
   - 财务登记合同/订单并更新一个执行节点（例如“生产/备货”延期并填原因）；
   - 销售在**另一台设备**刷新执行页或等待 60 秒轮询，看到节点变化，且页面与接口无任何金额（F12 中搜索合同金额关键数字应无结果）；
   - 商务把一个“交付/进度”附件授权销售可见，销售能下载，内部附件不可见。
4. 记录：主机 OS、客户端设备与浏览器、客户端 IP（截图时可打码后两段）、每个操作的时间、截图；主机 `logs\app.log` 中对应请求时间段。

## 6. 升级、同版本修复、失败回滚（R03/R06）

准备“下一版本”测试包：负责人从 CI 制品或构建机获得版本号更高的测试包（例如 2.1.0-rc3-test），解压到 `D:\ISH-next`（**不要**解压到安装目录内）。
```powershell
cd D:\ISH-next
.\07-升级程序.bat -InstallDir D:\ISH-RC2-Test          # 升级器从新包目录运行；输入 Y
Rec 61-upgrade { Get-Content D:\ISH-RC2-Test\VERSION.txt; Get-ChildItem D:\ISH-RC2-Test\data\ops\upgrade-*.json | % { Get-Content $_ } }
```
期望：顺序为 PRECHECK→LOCK→BACKUP（旧程序生成 `*-pre-upgrade-<ID>` 并恢复演练）→SNAPSHOT→STAGE→SWITCH→MIGRATE→VERIFY→DONE；`/healthz` 版本为新版本；历史报价 PDF 下载哈希不变。
- **同版本修复**：`D:\ISH-next\07-升级程序.bat -InstallDir D:\ISH-RC2-Test` 再执行一次 → 退出码 52（同版本需 `-Repair`）；加 `-Repair` 后完成。
- **降级拒绝**：用 RC2 包对已升级的安装执行 → 退出码 52。
- **失败回滚**（测试机）：`New-Item D:\ISH-RC2-Test\data\ALLOW_FAULT_INJECTION`，然后分别设置 `$env:ISH_FAULT_INJECT` 为 `upgrade.switch_half`、`upgrade.migrate`、`upgrade.verify` 运行升级 → 每次退出码 56，程序/运行时/脚本/VERSION/SHA256SUMS 与旧版本一致（`Get-FileHash` 对照旧包 SHA256SUMS），数据与 PDF 哈希不变，服务正常。
- **断电模拟**：`$env:ISH_FAULT_CRASH='upgrade.switch_half'` 运行升级 → 进程退出码 99，系统维护只读；然后 `D:\ISH-next\07-升级程序.bat -InstallDir D:\ISH-RC2-Test -Rollback <ID>` → 恢复旧版本。
- **已完成升级的回滚**：`D:\ISH-RC2-Test\07-升级程序.bat -Rollback <ID>`（会提示升级后的新数据将丢失）。
- **清理**：确认新版本运行正常后 `-Cleanup <ID>`。

## 7. 卸载

`99-卸载服务.bat` → 服务、防火墙规则、计划任务删除，`data`/`config`/`backups` 保留；重新运行 `01-安装部署.bat` 可恢复使用（数据仍在）。

## 8. 回传证据清单

| # | 内容 | 对应门禁 |
|---|---|---|
| 1 | ZIP SHA256 截图、OS 信息 `00-os.txt` | 包一致性 |
| 2 | 2.1 安装截图、`21-*.txt`、`logs\install-*.log` | 离线首次安装 |
| 3 | 2.3 前后指纹 `23-before/after.txt`、主体与签收截图 | R01 重复安装 |
| 4 | 2.4/2.5 截图与 `24/25-*.txt` | 自启/自恢复 |
| 5 | 3.1–3.4 截图、`32-acl.txt` | 路径/重名服务/ACL/磁盘/中断恢复 |
| 6 | 第 4 节截图、`backups` 列表、`data\ops\*`、`data\ops.history.log`、计划任务结果 | 备份/互斥/恢复 |
| 7 | 第 5 节截图（两台设备）、时间记录、`logs\app.log` 片段 | 双机断网 UAT |
| 8 | 第 6 节截图、`data\ops\upgrade-*.json`、版本与哈希对照 | 升级/回滚 |
| 9 | 测试机快照名称、执行人、日期 | 追溯 |

把 `RC2证据\` 与 `D:\ISH-RC2-Test\logs`、`D:\ISH-RC2-Test\data\ops`（**不含** `data\private`、`config`）压缩后回传。证据中不得包含真实客户资料或口令。
