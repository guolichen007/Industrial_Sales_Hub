# 01-安装部署：首次安装 / 重复执行（幂等，不破坏已有 data 与 config）
param(
    [int]$AppPort = 8000,
    [int]$DbPort = 55432,
    [string]$AdminUser = '',
    [string]$AllowedRemote = 'LocalSubnet',   # 防火墙放行范围：LocalSubnet 或具体网段，如 192.168.1.0/24
    [switch]$Https,                           # 已配置可信 HTTPS 反向代理时使用
    [switch]$NoBrowser,
    [switch]$Unattended,                      # 无人值守（自动化验收）：不交互、管理员密码取自环境变量 ISH_INIT_ADMIN_PASSWORD
    [string]$PreferredIp = ''                 # 多网卡主机：指定员工访问用的局域网 IP（写入 config\app.env 的 ISH_PREFERRED_IP）
)
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'install-security.ps1')
$script:LogFile = $null
$script:InstallState = $null
function Set-InstallState([string]$phase) {
    # data\install-state.json：记录初始化进度，中断后重复运行据此安全续做（不记录任何密码）
    New-Item -ItemType Directory -Force -Path $Data | Out-Null
    $script:InstallState = [ordered]@{ phase = $phase; at = (Get-Date -Format s); version = (Get-Content -LiteralPath (Join-Path $Root 'VERSION.txt') -Raw -ErrorAction SilentlyContinue) }
    Write-AtomicText (Join-Path $Data 'install-state.json') ($script:InstallState | ConvertTo-Json -Compress)
}
function Get-InstallState {
    $f = Join-Path $Data 'install-state.json'
    if (Test-Path -LiteralPath $f) { try { return (Get-Content -LiteralPath $f -Raw -Encoding UTF8 | ConvertFrom-Json).phase } catch { return 'unknown' } }
    return $null
}

Write-Host "工业销售管理系统 V2.1 — 局域网正式安装" -ForegroundColor White
Write-Host "安装目录：$Root"

# ---------- 1. 环境检查 ----------
Write-Step '检查运行环境'
if (-not [Environment]::Is64BitOperatingSystem) { Fail '需要 64 位 Windows。' 13 }
$os = Get-CimInstance Win32_OperatingSystem
Write-Ok ("系统：{0} {1}（版本 {2}）" -f $os.Caption, $os.OSArchitecture, $os.Version)
if ([Version]$os.Version -lt [Version]'10.0.17763') { Fail '需要 Windows 10 1809 / Windows Server 2019 或更新版本。' 13 }
Assert-Admin '安装 Windows 服务、配置防火墙与目录权限'
$synced = Test-SyncedFolder $Root
if ($synced) { Fail "安装目录位于同步盘（$synced）。数据库目录放入同步盘会损坏数据，请解压到本地磁盘（例如 D:\IndustrialSalesHub）。" 14 }
if ($Root.StartsWith('\\')) { Fail '不能安装在网络共享路径上，请使用本机磁盘。' 14 }
if ($Root -match '[&<>"''%!^;`$]') {
    # 这些字符会破坏服务 XML、批处理或 PowerShell 参数传递（即使转义，也增加现场排错难度）
    Fail ('安装路径包含不支持的特殊字符（& < > " '' % ! ^ ; ` $）：' + $Root + "`n请解压到例如 D:\IndustrialSalesHub。") 14
}
if (-not (Test-AsciiPath $Root)) {
    # PostgreSQL 的 initdb/postgres 按系统 ANSI 代码页处理路径，中文路径在部分系统上会导致数据库无法启动
    Fail "安装路径包含中文或特殊字符：$Root`n请解压到纯英文路径（例如 D:\IndustrialSalesHub）。路径中可以包含空格。" 14
}
# 从已验证并保护的目录运行，尤其不能信任可由普通账户替换的脚本、运行时或父目录。
try {
    Assert-InstallTree
    New-InstallPrivateDirectory $Logs
} catch { Fail ("安装前权限校验失败：" + $_.Exception.Message) 27 }
Start-Log 'install'
$drive = (Get-Item -LiteralPath $Root).PSDrive
$freeGb = [math]::Round($drive.Free / 1GB, 1)
if ($freeGb -lt 5) { Fail "磁盘 $($drive.Name): 剩余 $freeGb GB，至少需要 5 GB（建议预留 50 GB）。" 15 }
if ($freeGb -lt 50) { Write-Warn2 "磁盘剩余 $freeGb GB，建议预留 50 GB 以上用于数据与备份。" } else { Write-Ok "磁盘剩余 $freeGb GB" }
$mem = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
if ($mem -lt 4) { Write-Warn2 "内存 $mem GB，建议 8 GB。" } else { Write-Ok "内存 $mem GB" }
Verify-PackageChecksums

# 同名服务必须指向本安装目录：防止从另一个目录重复安装时“劫持”已有服务（或被已有服务劫持）
foreach ($svc in @($SvcDb, $SvcApp)) {
    $w = Get-CimInstance Win32_Service -Filter "Name='$svc'" -ErrorAction SilentlyContinue
    if ($w) {
        $expect = Join-Path $Services "$svc\$svc.exe"
        $actual = ($w.PathName -replace '^"([^"]+)".*$', '$1').Trim()
        if (-not $actual.Equals($expect, [StringComparison]::OrdinalIgnoreCase)) {
            Fail "已存在同名服务 $svc，但它属于另一个安装目录：`n  $actual`n本目录：$expect`n请先在原目录运行 99-卸载服务.bat，或从原目录执行修复安装。" 18
        }
    }
}
New-InstallPrivateDirectory $Data
$tok, $busy = Enter-OpsLock 'install' 0
if (-not $tok) { Fail $busy 75 }
try {
$cur = Get-MaintenanceOwner
if ($cur -and $cur.task -notlike 'install*') { Fail "系统处于维护状态（$($cur.task)），可能是升级/恢复失败后保持的只读状态。请先处理（见手册）再修复安装。" 19 }

$installed = Test-Path -LiteralPath $AppEnv
if ($installed) {
    $cfg = Read-AppEnv
    $AppPort = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
    $DbPort = [int]$cfg['ISH_DB_PORT']
    Write-Ok "检测到已有安装（config\app.env）：沿用应用端口 $AppPort、数据库端口 $DbPort；不会重新生成密钥或覆盖数据"
} else {
    if (Test-Path -LiteralPath $PgData) { Fail '存在未关联配置的数据库目录，请保留现场并联系实施人员。' 21 }
}
foreach ($listener in @(@($SvcApp, $AppPort), @($SvcDb, $DbPort))) {
    if ((Get-ServiceState $listener[0]) -ne 'Running' -and -not (Test-PortFree $listener[1])) { Fail "端口 $($listener[1]) 已被占用，未改动占用程序。" 16 }
}

# ---------- 2. 运行库 ----------
Write-Step '安装 Microsoft Visual C++ 运行库（PostgreSQL 依赖）'
$vc = Join-Path $Runtime 'vc_redist.x64.exe'
$vcKey = 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
if ((Get-InstallState) -eq 'vc-reboot-required') {
    # 上次运行时运行库要求重启：确认本次运行发生在那之后的一次开机（否则继续会导致数据库初始化失败）
    $stFile = Join-Path $Data 'install-state.json'
    $stAt = (Get-Item -LiteralPath $stFile).LastWriteTime
    $boot = if ((Test-SimPlatform) -and $env:ISH_SIM_BOOT_TIME) { [datetime]$env:ISH_SIM_BOOT_TIME } else { (Get-CimInstance Win32_OperatingSystem).LastBootUpTime }
    if ($boot -lt $stAt) { Write-Warn2 '上次安装要求重启以使 Visual C++ 运行库生效，但电脑尚未重启。请先重启，再运行 01-安装部署.bat。'; Exit-OpsLock; exit 3010 }
    Write-Ok '已在重启后继续安装'
}
$vcOk = (Test-Path $vcKey) -and ((Get-ItemProperty $vcKey).Installed -eq 1) -and ([int](Get-ItemProperty $vcKey).Bld -ge 30000)
if ($vcOk) { Write-Ok '已安装' } else {
    if (-not (Test-Path -LiteralPath $vc)) { Fail '缺少 runtime\vc_redist.x64.exe' 17 }
    $code = if ($env:ISH_SIM_VC_EXIT -and (Test-SimPlatform)) { [int]$env:ISH_SIM_VC_EXIT } else { (Start-Process -FilePath $vc -ArgumentList '/install', '/quiet', '/norestart' -Wait -PassThru).ExitCode }
    if ($code -notin 0, 1638, 3010) { Fail "VC++ 运行库安装失败（退出码 $code）" 17 }
    if ($code -eq 3010) {
        # RC3-G5：3010 = 安装成功但需要重启。此时继续初始化 PostgreSQL 可能因运行库未生效而失败，被误判为数据库损坏 → 保存续装状态并停止
        Set-InstallState 'vc-reboot-required'
        Write-Host "`nVisual C++ 运行库已安装，但 Windows 需要重启后才能生效。" -ForegroundColor Yellow
        Write-Host '请重启电脑，然后再次以管理员身份运行 01-安装部署.bat（将从数据库初始化继续，不会重复安装运行库）。' -ForegroundColor Yellow
        Exit-OpsLock
        exit 3010
    }
    Write-Ok "已安装（退出码 $code）"
}

# ---------- 3. 目录与配置 ----------
Write-Step '准备目录与配置'
foreach ($d in @($Config, $Data, $AdminOnly, (Join-Path $Data 'private'), $Logs, $Backups, $Services)) {
    New-InstallPrivateDirectory $d
}
$cfgTmp = "$AppEnv.install.tmp"
if (Test-Path -LiteralPath $cfgTmp) { Remove-Item -LiteralPath $cfgTmp }
if (-not $installed) {
    $dbPass = New-RandomSecret 24
    $lan = Get-LanAddresses
    $hosts = @('localhost', '127.0.0.1', $env:COMPUTERNAME) + $lan
    $scheme = if ($Https) { 'https' } else { 'http' }
    # HTTP 试用：浏览器直接访问应用端口；HTTPS：浏览器访问反向代理的 443（应用只在回环），来源为 https://<主机>
    if ($Https) { $origins = ($hosts | Where-Object { $_ -ne 'localhost' } | ForEach-Object { "https://${_}" }) -join ',' }
    else { $origins = ($hosts | Where-Object { $_ -ne 'localhost' } | ForEach-Object { "${scheme}://${_}:$AppPort" }) -join ',' }
    $listen = if ($Https) { "127.0.0.1:$AppPort" } else { "0.0.0.0:$AppPort" }
    $proxyLines = if ($Https) { @('ISH_TRUST_PROXY_HEADER=1', 'ISH_TRUSTED_PROXY=127.0.0.1') } else { @() }
    try {
    @(
        '# 工业销售管理系统 正式配置（自动生成）。包含数据库密码与签名密钥，仅管理员与服务账户可读。'
        'ISH_MODE=LAN'
        "DJANGO_SECRET_KEY=$(New-RandomSecret 64)"
        "DJANGO_ALLOWED_HOSTS=$($hosts -join ',')"
        "DJANGO_CSRF_TRUSTED_ORIGINS=$origins"
        "DATABASE_URL=postgres://ish_app:$dbPass@127.0.0.1:$DbPort/ish"
        "ISH_DB_PORT=$DbPort"
        "DATA_DIR=$Data"
        "ISH_LOG_DIR=$Logs"
        "FRONTEND_DIST=$(Join-Path $App 'frontend\dist')"
        "ISH_LISTEN=$listen"
        'ISH_THREADS=6'
        "ISH_HTTPS=$([int][bool]$Https)"
        'ISH_PROJECT_VISIBILITY=ALL'
        'PERFORMANCE_CONFIRM_MODE=OR'
        "ISH_BACKUP_DIR=$Backups"
        'ISH_BACKUP_KEEP_DAYS=30'
        'ISH_BACKUP_COPY_TO='
    ) + $proxyLines | Set-Content -LiteralPath $cfgTmp -Encoding UTF8
    Move-Item -LiteralPath $cfgTmp -Destination $AppEnv   # 原子生成：不替换现有配置
    } finally {
        if (Test-Path -LiteralPath $cfgTmp) { Remove-Item -LiteralPath $cfgTmp }
    }
    Write-Ok '已生成 config\app.env（随机密钥与数据库密码）'
    if (-not $Https) { Write-Warn2 '当前为 HTTP 隔离内网试用模式：数据在局域网中未加密传输。正式使用建议配置可信 HTTPS 反向代理（见安装手册）。' }
}
$cfg = Read-AppEnv

# ---------- 4. 数据库 ----------
Write-Step '初始化 PostgreSQL（仅监听 127.0.0.1）'
$dbUrl = [Uri]($cfg['DATABASE_URL'] -replace '^postgres:', 'http:')
if ($dbUrl.Host -ne '127.0.0.1' -or $dbUrl.Port -ne $DbPort -or $dbUrl.AbsolutePath -ne '/ish' -or $dbUrl.UserInfo -notlike 'ish_app:*') { Fail '数据库连接配置未指向当前实例的本机数据库。' 21 }
$dbPass = [Uri]::UnescapeDataString($dbUrl.UserInfo.Split(':', 2)[1])
if (-not (Test-Path -LiteralPath $PgData)) { Set-InstallState 'initdb' }
try { $initialized = Initialize-InstallDatabase $DbPort }
catch { Fail ("数据库初始化检查失败：" + $_.Exception.Message) 21 }
if ($initialized) { Set-InstallState 'initdb-done'; Write-Ok '数据库目录已初始化' }
else { Write-Ok '数据库目录已存在，跳过 initdb' }

# 服务账户与目录权限（低权限：虚拟服务账户 NT SERVICE\...）
Write-Step '注册 Windows 服务（开机自启、故障自动重启）'
$pgExe = Join-Path $PgBin 'pg_ctl.exe'
function X([string]$v) { return [Security.SecurityElement]::Escape($v) }   # 服务 XML 中所有路径按 XML 转义
$dbXml = @"
<service>
  <id>$SvcDb</id>
  <name>Industrial Sales Hub 数据库</name>
  <description>工业销售管理系统 PostgreSQL（仅本机 127.0.0.1:$DbPort）</description>
  <executable>$(X (Join-Path $PgBin 'postgres.exe'))</executable>
  <arguments>-D &quot;$(X $PgData)&quot;</arguments>
  <stopexecutable>$(X $pgExe)</stopexecutable>
  <stoparguments>stop -D &quot;$(X $PgData)&quot; -m fast -w</stoparguments>
  <stoptimeout>60 sec</stoptimeout>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <resetfailure>1 hour</resetfailure>
  <logpath>$(X (Join-Path $Logs 'service'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
$appXml = @"
<service>
  <id>$SvcApp</id>
  <name>Industrial Sales Hub 应用</name>
  <description>工业销售管理系统 Web 应用（Waitress，端口 $AppPort）</description>
  <executable>$(X $Python)</executable>
  <arguments>-X utf8 -B &quot;$(X (Join-Path $Backend 'serve.py'))&quot;</arguments>
  <workingdirectory>$(X $Backend)</workingdirectory>
  <env name="ISH_CONFIG_FILE" value="$(X $AppEnv)"/>
  <depend>$SvcDb</depend>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>30 sec</stoptimeout>
  <logpath>$(X (Join-Path $Logs 'service'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
foreach ($x in @($dbXml, $appXml)) { [xml]$x | Out-Null }   # XML 必须可解析（路径转义校验）
Install-Svc $SvcDb $dbXml
Install-Svc $SvcApp $appXml

Write-Step '设置目录权限（根目录普通用户只读；数据/配置/备份仅管理员与服务账户）'
Set-IshAcls
$aclProblems = @(Test-SensitiveAcl)
if ($aclProblems.Count) { Fail ("敏感目录权限不符合要求：`n  " + ($aclProblems -join "`n  ")) 27 }
Write-Ok '权限已设置'

Write-Step '启动数据库'
try { Start-Service -Name $SvcDb -ErrorAction Stop } catch { Fail "数据库服务启动失败：$($_.Exception.Message)（见 logs\service 与 data\pgdata\log）" 23 }
if (-not (Wait-Db $DbPort 60)) { Fail '数据库未能在 60 秒内就绪（见 data\pgdata\log 与 logs\service）' 23 }
Write-Ok '数据库就绪'

Write-Step '创建应用数据库账号与数据库'
$superPw = ((Get-Content -LiteralPath (Join-Path $AdminOnly 'postgres-superuser.txt') -Encoding UTF8) -split '：', 2)[1].Trim()
$env:PGPASSWORD = $superPw
try {
    $psql = Join-Path $PgBin 'psql.exe'
    $exists = & $psql -w -h 127.0.0.1 -p $DbPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='ish_app'"
    if ($LASTEXITCODE -ne 0) { Fail '无法认证本机数据库管理连接' 24 }
    if ($exists -ne '1') {
        if ((Invoke-InstallRoleSql $psql $DbPort $dbPass) -ne 0) { Fail '创建数据库账号失败' 24 }
    }
    $exists = & $psql -w -h 127.0.0.1 -p $DbPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='ish'"
    if ($LASTEXITCODE -ne 0) { Fail '无法查询本机数据库状态' 24 }
    if ($exists -ne '1') {
        & $psql -h 127.0.0.1 -p $DbPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE ish OWNER ish_app ENCODING 'UTF8' TEMPLATE template0" | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail '创建数据库失败' 24 }
    }
} finally { Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Ok '应用账号 ish_app（非超级用户）与数据库 ish 就绪'

Set-InstallState 'migrate'
Write-Step '数据库迁移'
Invoke-Manage @('migrate', '--noinput') | Out-Null
Write-Ok '迁移完成'

Write-Step '初始化缺失的报价出具主体（create-only：已有主体与签收状态一律不改）'
$ent = Join-Path $Root 'config-templates\entities.json'
Invoke-Manage @('seed_entities', $ent) | Out-Null
Write-Host '    真实主体信息（全称、税号、银行账号、条款）请由负责人在“系统设置”维护，或用 09-导入主体配置.bat（需负责人授权）。'

Write-Step '初始化管理员（仅首次）'
$hasAdmin = Invoke-Manage @('init_admin', '--check') -AllowFail
if ($hasAdmin -ne 0) {
    if (-not $AdminUser) {
        if ($Unattended) { Fail '无人值守安装必须指定 -AdminUser' 26 }
        $AdminUser = Read-Host '请输入管理员用户名（例如 admin）'
    }
    if ($Unattended -and -not $env:ISH_INIT_ADMIN_PASSWORD) { Fail '无人值守安装需要设置环境变量 ISH_INIT_ADMIN_PASSWORD' 26 }
    # 密码在 manage.py 中交互输入两次（不回显、不进日志）；-Unattended 时取 ISH_INIT_ADMIN_PASSWORD（只在进程环境中，用后清除）
    try { Invoke-Manage @('init_admin', '--username', $AdminUser) | Out-Null } finally { Remove-Item env:ISH_INIT_ADMIN_PASSWORD -ErrorAction SilentlyContinue }
} else { Write-Ok '已存在管理员，跳过（不修改任何账号与密码）' }
Set-InstallState 'admin-ready'

Write-Step '配置 Windows 防火墙（仅专用/域网络，仅应用端口）'
Get-AppFirewallRules | Remove-NetFirewallRule   # 只删除本程序创建的应用端口规则；HTTPS 443 等手工规则保留
if ($cfg['ISH_LISTEN'] -like '127.0.0.1:*') {
    Write-Ok "应用只监听 127.0.0.1:$AppPort（HTTPS 反向代理模式），不为应用端口开放防火墙；代理的 443 端口规则由代理安装步骤创建（见 HTTPS 手册），重复安装不会删除它"
    if (-not (Get-NetFirewallRule -DisplayName 'Industrial Sales Hub HTTPS*' -ErrorAction SilentlyContinue)) { Write-Warn2 '尚未发现 HTTPS 代理的防火墙规则（Industrial Sales Hub HTTPS (443)）：其他电脑暂时无法访问，请按 HTTPS 手册第 4.3 节创建' }
} else {
    $ruleName = "$($script:AppFwRulePrefix)$AppPort)"
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $AppPort -Action Allow -Profile Private, Domain -RemoteAddress $AllowedRemote | Out-Null
    Write-Ok "已放行 TCP $AppPort（远程范围：$AllowedRemote；数据库端口 $DbPort 不对外开放）"
}
$pub = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' }
if ($pub) { Write-Warn2 ("网络 {0} 当前为「公用」类型，防火墙规则不对公用网络生效。请在 Windows 设置中改为「专用网络」，否则其他电脑无法访问。" -f (($pub | Select-Object -ExpandProperty Name) -join '、')) }

Write-Step '启动应用'
try { Start-Service -Name $SvcApp -ErrorAction Stop } catch { Fail "应用服务启动失败：$($_.Exception.Message)（见 logs\service 与 logs\app.log）" 25 }
$health = Wait-Http "http://127.0.0.1:$AppPort/healthz" 120
if (-not $health) { Fail '应用未能在 120 秒内就绪（见 logs\service 与 logs\app.log）' 25 }
Write-Ok "应用就绪：$health"

Write-Step '生成客户端快捷方式'
if ($PreferredIp) {
    if ($PreferredIp -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { Fail "PreferredIp 格式错误：$PreferredIp" 14 }
    $lines = @(Get-Content -LiteralPath $AppEnv -Encoding UTF8 | Where-Object { $_ -notmatch '^ISH_PREFERRED_IP=' }) + "ISH_PREFERRED_IP=$PreferredIp"
    try {
        $lines | Set-Content -LiteralPath $cfgTmp -Encoding UTF8
        [IO.File]::Replace($cfgTmp, $AppEnv, [NullString]::Value)
    } finally {
        if (Test-Path -LiteralPath $cfgTmp) { Remove-Item -LiteralPath $cfgTmp }
    }
    if ((Read-AppEnv)['DJANGO_ALLOWED_HOSTS'] -notmatch [regex]::Escape($PreferredIp)) { Write-Warn2 "$PreferredIp 不在 DJANGO_ALLOWED_HOSTS 中：请编辑 config\app.env 加入后重启应用服务" }
}
$lanAll = @(Get-LanCandidates | ForEach-Object { $_.IP })
$lan = Get-LanAddresses
$scheme = if ($cfg['ISH_HTTPS'] -eq '1') { 'https' } else { 'http' }
if ($scheme -eq 'https') { $urls = @($lan | ForEach-Object { "https://${_}/" }); $urls += "https://$($env:COMPUTERNAME)/" }
else { $urls = @($lan | ForEach-Object { "${scheme}://${_}:$AppPort/" }); $urls += "${scheme}://$($env:COMPUTERNAME):$AppPort/" }
$sc = Join-Path $Root '客户端快捷方式'
New-Item -ItemType Directory -Force -Path $sc | Out-Null
$first = $urls[0]
if ($lanAll.Count -gt 1) { Write-Warn2 ("检测到多个网卡地址：{0}。快捷方式使用 {1}（默认网关所在的实体网卡）。如不对，重新运行 01-安装部署.bat -PreferredIp <地址>" -f ($lanAll -join '、'), $lan[0]) }
Set-Content -LiteralPath (Join-Path $sc '工业销售管理系统.url') -Value "[InternetShortcut]`r`nURL=$first`r`n" -Encoding ASCII
Set-Content -LiteralPath (Join-Path $sc '访问地址.txt') -Value (@('局域网其他电脑在浏览器中打开以下任一地址（无需安装任何软件）：') + $urls + @('', '建议在路由器中为本机设置 DHCP 保留，保证 IP 不变。')) -Encoding UTF8
Write-Ok "快捷方式：$sc"

Write-Step '核对敏感目录权限（普通用户不可读）'
$aclProblems = @(Test-SensitiveAcl)
if ($aclProblems.Count) { Fail ("敏感目录权限不符合要求：`n  " + ($aclProblems -join "`n  ")) 27 }
Write-Ok 'config、data、logs、backups 仅 SYSTEM/Administrators/服务账户可访问；安装根目录普通用户只读'
Set-InstallState 'done'
} finally { Exit-OpsLock }
Write-Host "`n安装完成。" -ForegroundColor Green
Write-Host '局域网访问地址（以本机实际网卡为准）：' -ForegroundColor Green
$urls | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }
if ($scheme -eq 'http') { Write-Host '注意：当前为 HTTP 隔离内网试用，传输未加密。' -ForegroundColor Yellow }
Write-Host '下一步：用管理员登录 → 系统设置 → 创建负责人、商务、销售、财务账号；运行 04-立即备份 并配置计划任务（见手册）。'
Write-IshLog '==== 安装完成'
if (-not $NoBrowser) {
    if ($scheme -eq 'https') {
        # RC3-G5：HTTPS 模式下 Cookie 为 Secure，回环 HTTP 地址无法登录；提示配置代理后用正式地址访问
        Write-Host '提示：HTTPS 模式下请在完成反向代理与证书配置后，用 https://<内网域名>/ 访问（见 HTTPS内网证书配置手册）；不自动打开 HTTP 地址。' -ForegroundColor Yellow
    } else { Start-Process "http://127.0.0.1:$AppPort/" }
}
exit 0
