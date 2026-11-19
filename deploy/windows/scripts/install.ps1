# 01-安装部署：首次安装 / 重复执行（幂等，不破坏已有 data 与 config）
param(
    [int]$AppPort = 8000,
    [int]$DbPort = 55432,
    [string]$AdminUser = '',
    [string]$AllowedRemote = 'LocalSubnet',   # 防火墙放行范围：LocalSubnet 或具体网段，如 192.168.1.0/24
    [switch]$Https,                           # 已配置可信 HTTPS 反向代理时使用
    [switch]$NoBrowser,
    [switch]$Unattended                       # 无人值守（自动化验收）：不交互、管理员密码取自环境变量 ISH_INIT_ADMIN_PASSWORD
)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'install'

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
if (-not (Test-AsciiPath $Root)) {
    # PostgreSQL 的 initdb/postgres 按系统 ANSI 代码页处理路径，中文路径在部分系统上会导致数据库无法启动
    Fail "安装路径包含中文或特殊字符：$Root`n请解压到纯英文路径（例如 D:\IndustrialSalesHub）。路径中可以包含空格。" 14
}
$drive = (Get-Item -LiteralPath $Root).PSDrive
$freeGb = [math]::Round($drive.Free / 1GB, 1)
if ($freeGb -lt 5) { Fail "磁盘 $($drive.Name): 剩余 $freeGb GB，至少需要 5 GB（建议预留 50 GB）。" 15 }
if ($freeGb -lt 50) { Write-Warn2 "磁盘剩余 $freeGb GB，建议预留 50 GB 以上用于数据与备份。" } else { Write-Ok "磁盘剩余 $freeGb GB" }
$mem = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
if ($mem -lt 4) { Write-Warn2 "内存 $mem GB，建议 8 GB。" } else { Write-Ok "内存 $mem GB" }
Verify-PackageChecksums

$installed = Test-Path -LiteralPath $AppEnv
if ($installed) {
    $cfg = Read-AppEnv
    $AppPort = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
    $DbPort = [int]$cfg['ISH_DB_PORT']
    Write-Ok "检测到已有安装（config\app.env）：沿用应用端口 $AppPort、数据库端口 $DbPort；不会重新生成密钥或覆盖数据"
} else {
    foreach ($p in @($AppPort, $DbPort)) { if (-not (Test-PortFree $p)) { Fail "端口 $p 已被占用。请关闭占用程序，或用参数指定其他端口：01-安装部署.bat -AppPort 8010 -DbPort 55433" 16 } }
    Write-Ok "端口可用：应用 $AppPort，数据库 $DbPort（仅本机）"
}

# ---------- 2. 运行库 ----------
Write-Step '安装 Microsoft Visual C++ 运行库（PostgreSQL 依赖）'
$vc = Join-Path $Runtime 'vc_redist.x64.exe'
$vcKey = 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
$vcOk = (Test-Path $vcKey) -and ((Get-ItemProperty $vcKey).Installed -eq 1) -and ([int](Get-ItemProperty $vcKey).Bld -ge 30000)
if ($vcOk) { Write-Ok '已安装' } else {
    if (-not (Test-Path -LiteralPath $vc)) { Fail '缺少 runtime\vc_redist.x64.exe' 17 }
    $p = Start-Process -FilePath $vc -ArgumentList '/install', '/quiet', '/norestart' -Wait -PassThru
    if ($p.ExitCode -notin 0, 1638, 3010) { Fail "VC++ 运行库安装失败（退出码 $($p.ExitCode)）" 17 }
    Write-Ok "已安装（退出码 $($p.ExitCode)）"
}

# ---------- 3. 目录与配置 ----------
Write-Step '准备目录与配置'
foreach ($d in @($Config, $Data, (Join-Path $Data 'private'), $Logs, $Backups, $Services)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
if (-not $installed) {
    $dbPass = New-RandomSecret 24
    $lan = Get-LanAddresses
    $hosts = @('localhost', '127.0.0.1', $env:COMPUTERNAME) + $lan
    $scheme = if ($Https) { 'https' } else { 'http' }
    $origins = ($hosts | Where-Object { $_ -ne 'localhost' } | ForEach-Object { "${scheme}://${_}:$AppPort" }) -join ','
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
        "ISH_LISTEN=0.0.0.0:$AppPort"
        'ISH_THREADS=6'
        "ISH_HTTPS=$([int][bool]$Https)"
        'ISH_PROJECT_VISIBILITY=ALL'
        'PERFORMANCE_CONFIRM_MODE=OR'
        "ISH_BACKUP_DIR=$Backups"
        'ISH_BACKUP_KEEP_DAYS=30'
        'ISH_BACKUP_COPY_TO='
    ) | Set-Content -LiteralPath $AppEnv -Encoding UTF8
    Write-Ok '已生成 config\app.env（随机密钥与数据库密码）'
    if (-not $Https) { Write-Warn2 '当前为 HTTP 隔离内网试用模式：数据在局域网中未加密传输。正式使用建议配置可信 HTTPS 反向代理（见安装手册）。' }
}
$cfg = Read-AppEnv

# ---------- 4. 数据库 ----------
Write-Step '初始化 PostgreSQL（仅监听 127.0.0.1）'
$dbUrl = [Uri]($cfg['DATABASE_URL'] -replace '^postgres:', 'http:')
$dbPass = [Uri]::UnescapeDataString($dbUrl.UserInfo.Split(':', 2)[1])
if (-not (Test-Path -LiteralPath (Join-Path $PgData 'PG_VERSION'))) {
    $super = New-RandomSecret 24
    $pwFile = Join-Path $env:TEMP ("ish-pw-{0}.txt" -f [guid]::NewGuid())
    try {
        Set-Content -LiteralPath $pwFile -Value $super -Encoding ASCII -NoNewline
        $rc = Invoke-Exe (Join-Path $PgBin 'initdb.exe') @('-D', $PgData, '-U', 'postgres', "--pwfile=$pwFile", '-E', 'UTF8', '--locale=C', '--auth-local=scram-sha-256', '--auth-host=scram-sha-256')
        if ($rc -ne 0) { Fail 'initdb 失败（见日志）' 21 }
    } finally { Remove-Item -LiteralPath $pwFile -Force -ErrorAction SilentlyContinue }
    @(
        "listen_addresses = '127.0.0.1'"
        "port = $DbPort"
        'max_connections = 60'
        "shared_buffers = '256MB'"
        "logging_collector = on"
        "log_directory = 'log'"
        "log_rotation_age = '7d'"
        "lc_messages = 'C'"
        "timezone = 'Asia/Shanghai'"
    ) | Add-Content -LiteralPath (Join-Path $PgData 'postgresql.conf') -Encoding ASCII
    @('# 仅本机 scram 认证', 'host all all 127.0.0.1/32 scram-sha-256', 'host all all ::1/128 scram-sha-256') | Set-Content -LiteralPath (Join-Path $PgData 'pg_hba.conf') -Encoding ASCII
    # 超级用户密码：写入仅管理员可读的 config\admin-only（应用服务账户不可读）
    New-Item -ItemType Directory -Force -Path $AdminOnly | Out-Null
    Set-Content -LiteralPath (Join-Path $AdminOnly 'postgres-superuser.txt') -Value "postgres 超级用户密码（仅运维紧急使用，勿外传）：$super" -Encoding UTF8
    Write-Ok '数据库目录已初始化'
} else { Write-Ok '数据库目录已存在，跳过 initdb' }

# 服务账户与目录权限（低权限：虚拟服务账户 NT SERVICE\...）
Write-Step '注册 Windows 服务（开机自启、故障自动重启）'
$winsw = Join-Path $Runtime 'winsw\WinSW-x64.exe'
function Install-Svc([string]$id, [string]$xml) {
    $dir = Join-Path $Services $id
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $dst = Join-Path $dir "$id.exe"
    # 服务运行中时 exe 被占用：哈希相同则不复制（重复安装/修复不失败）
    if (-not (Test-Path -LiteralPath $dst) -or (Get-FileHash -LiteralPath $dst).Hash -ne (Get-FileHash -LiteralPath $winsw).Hash) {
        if ((Get-ServiceState $id) -eq 'Running') { Stop-Service -Name $id }
        Copy-Item -LiteralPath $winsw -Destination $dst -Force
    }
    Set-Content -LiteralPath (Join-Path $dir "$id.xml") -Value $xml -Encoding UTF8
    if ((Get-ServiceState $id) -eq 'NotInstalled') {
        $rc = Invoke-Exe (Join-Path $dir "$id.exe") @('install')
        if ($rc -ne 0) { Fail "服务 $id 安装失败" 22 }
        Write-Ok "已注册服务 $id"
    } else {
        # WinSW 2.x 在每次启动服务时读取同名 XML，已更新的配置将在下次启动时生效（不重复创建服务）
        Write-Ok "服务 $id 已存在，已更新配置文件（不重复创建）"
    }
    # 以 Windows 虚拟服务账户（NT SERVICE\<服务名>，无密码、低权限）运行
    # 不用 sc.exe：PowerShell 5.1 会丢弃空字符串参数（password= ""），改用 WMI Win32_Service.Change
    $svc = Get-CimInstance Win32_Service -Filter "Name='$id'"
    if ($svc.StartName -ne "NT SERVICE\$id") {
        $r = Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{ StartName = "NT SERVICE\$id"; StartPassword = '' }
        if ($r.ReturnValue -ne 0) { Fail "无法将服务 $id 配置为虚拟服务账户（Win32_Service.Change 返回 $($r.ReturnValue)）" 22 }
    }
}
$pgExe = Join-Path $PgBin 'pg_ctl.exe'
$dbXml = @"
<service>
  <id>$SvcDb</id>
  <name>Industrial Sales Hub 数据库</name>
  <description>工业销售管理系统 PostgreSQL（仅本机 127.0.0.1:$DbPort）</description>
  <executable>$(Join-Path $PgBin 'postgres.exe')</executable>
  <arguments>-D "$PgData"</arguments>
  <stopexecutable>$pgExe</stopexecutable>
  <stoparguments>stop -D "$PgData" -m fast -w</stoparguments>
  <stoptimeout>60 sec</stoptimeout>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <resetfailure>1 hour</resetfailure>
  <logpath>$(Join-Path $Logs 'service')</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
$appXml = @"
<service>
  <id>$SvcApp</id>
  <name>Industrial Sales Hub 应用</name>
  <description>工业销售管理系统 Web 应用（Waitress，端口 $AppPort）</description>
  <executable>$Python</executable>
  <arguments>-X utf8 -B "$(Join-Path $Backend 'serve.py')"</arguments>
  <workingdirectory>$Backend</workingdirectory>
  <env name="ISH_CONFIG_FILE" value="$AppEnv"/>
  <depend>$SvcDb</depend>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>30 sec</stoptimeout>
  <logpath>$(Join-Path $Logs 'service')</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@
Install-Svc $SvcDb $dbXml
Install-Svc $SvcApp $appXml

Write-Step '设置目录权限（根目录普通用户只读；数据/配置/备份仅管理员与服务账户）'
Set-RootAcl
Grant-ServiceDirs
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
    $exists = & $psql -h 127.0.0.1 -p $DbPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='ish_app'"
    if ($exists -ne '1') {
        & $psql -h 127.0.0.1 -p $DbPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE ish_app LOGIN PASSWORD '$dbPass' NOSUPERUSER NOCREATEROLE CREATEDB" | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail '创建数据库账号失败' 24 }
    }
    $exists = & $psql -h 127.0.0.1 -p $DbPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='ish'"
    if ($exists -ne '1') {
        & $psql -h 127.0.0.1 -p $DbPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE ish OWNER ish_app ENCODING 'UTF8' TEMPLATE template0" | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail '创建数据库失败' 24 }
    }
} finally { Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Ok '应用账号 ish_app（非超级用户）与数据库 ish 就绪'

Write-Step '数据库迁移'
Invoke-Manage @('migrate', '--noinput') | Out-Null
Write-Ok '迁移完成'

Write-Step '导入三家报价出具主体配置（不导入任何客户/项目/账号）'
$ent = Join-Path $Root 'config-templates\entities.json'
$entLocal = Join-Path $Config 'entities.json'
if (Test-Path -LiteralPath $entLocal) { $ent = $entLocal }
Invoke-Manage @('seed_entities', $ent) | Out-Null

Write-Step '初始化管理员（仅首次）'
$hasAdmin = Invoke-Manage @('init_admin', '--check') -AllowFail
if ($hasAdmin -ne 0) {
    if (-not $AdminUser) {
        if ($Unattended) { Fail '无人值守安装必须指定 -AdminUser' 26 }
        $AdminUser = Read-Host '请输入管理员用户名（例如 admin）'
    }
    if ($Unattended -and -not $env:ISH_INIT_ADMIN_PASSWORD) { Fail '无人值守安装需要设置环境变量 ISH_INIT_ADMIN_PASSWORD' 26 }
    # 密码在 manage.py 中交互输入两次（不回显、不进日志）；-Unattended 时取 ISH_INIT_ADMIN_PASSWORD
    Invoke-Manage @('init_admin', '--username', $AdminUser) | Out-Null
} else { Write-Ok '已存在管理员，跳过' }

Write-Step '配置 Windows 防火墙（仅专用/域网络，仅应用端口）'
$ruleName = "Industrial Sales Hub (TCP $AppPort)"
Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $AppPort -Action Allow -Profile Private, Domain -RemoteAddress $AllowedRemote | Out-Null
Write-Ok "已放行 TCP $AppPort（远程范围：$AllowedRemote；数据库端口 $DbPort 不对外开放）"
$pub = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' }
if ($pub) { Write-Warn2 ("网络 {0} 当前为「公用」类型，防火墙规则不对公用网络生效。请在 Windows 设置中改为「专用网络」，否则其他电脑无法访问。" -f (($pub | Select-Object -ExpandProperty Name) -join '、')) }

Write-Step '启动应用'
try { Start-Service -Name $SvcApp -ErrorAction Stop } catch { Fail "应用服务启动失败：$($_.Exception.Message)（见 logs\service 与 logs\app.log）" 25 }
$health = Wait-Http "http://127.0.0.1:$AppPort/healthz" 120
if (-not $health) { Fail '应用未能在 120 秒内就绪（见 logs\service 与 logs\app.log）' 25 }
Write-Ok "应用就绪：$health"

Write-Step '生成客户端快捷方式'
$lan = Get-LanAddresses
$scheme = if ($cfg['ISH_HTTPS'] -eq '1') { 'https' } else { 'http' }
$urls = @($lan | ForEach-Object { "${scheme}://${_}:$AppPort/" })
$urls += "${scheme}://$($env:COMPUTERNAME):$AppPort/"
$sc = Join-Path $Root '客户端快捷方式'
New-Item -ItemType Directory -Force -Path $sc | Out-Null
$first = $urls[0]
Set-Content -LiteralPath (Join-Path $sc '工业销售管理系统.url') -Value "[InternetShortcut]`r`nURL=$first`r`n" -Encoding ASCII
Set-Content -LiteralPath (Join-Path $sc '访问地址.txt') -Value (@('局域网其他电脑在浏览器中打开以下任一地址（无需安装任何软件）：') + $urls + @('', '建议在路由器中为本机设置 DHCP 保留，保证 IP 不变。')) -Encoding UTF8
Write-Ok "快捷方式：$sc"

Write-Host "`n安装完成。" -ForegroundColor Green
Write-Host '局域网访问地址（以本机实际网卡为准）：' -ForegroundColor Green
$urls | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }
if ($scheme -eq 'http') { Write-Host '注意：当前为 HTTP 隔离内网试用，传输未加密。' -ForegroundColor Yellow }
Write-Host '下一步：用管理员登录 → 系统设置 → 创建负责人、商务、销售、财务账号；运行 04-立即备份 并配置计划任务（见手册）。'
Write-IshLog '==== 安装完成'
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$AppPort/" }
exit 0
