# Industrial Sales Hub — Windows 局域网部署公共函数（PowerShell 5.1+ / 7）
# 被 01~07 各 .ps1 脚本 dot-source。所有路径均支持中文与空格。
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

$script:SvcDb = 'IndustrialSalesHub-DB'
$script:SvcApp = 'IndustrialSalesHub-App'
$script:BackupTask = 'IndustrialSalesHub-DailyBackup'

function Set-IshRoot([string]$dir) {
    # 所有路径由安装根目录推导；升级器从新安装包运行时用它指向“被升级的安装目录”
    $script:Root = (Resolve-Path -LiteralPath $dir).Path.TrimEnd('\')
    $script:App = Join-Path $script:Root 'app'
    $script:Backend = Join-Path $script:App 'backend'
    $script:Runtime = Join-Path $script:Root 'runtime'
    $script:Python = Join-Path $script:Runtime 'python\python.exe'
    $script:PgBin = Join-Path $script:Runtime 'pgsql\bin'
    $script:Config = Join-Path $script:Root 'config'
    $script:AppEnv = Join-Path $script:Config 'app.env'
    $script:Data = Join-Path $script:Root 'data'
    $script:PgData = Join-Path $script:Data 'pgdata'
    $script:Logs = Join-Path $script:Root 'logs'
    $script:Backups = Join-Path $script:Root 'backups'
    $script:Services = Join-Path $script:Root 'services'
    $script:AdminOnly = Join-Path $script:Config 'admin-only'
    $script:OpsDir = Join-Path $script:Data 'ops'
}
Set-IshRoot (Join-Path $PSScriptRoot '..')

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan; Write-IshLog "STEP $msg" }
function Write-Ok([string]$msg) { Write-Host "    [OK] $msg" -ForegroundColor Green; Write-IshLog "OK $msg" }
function Write-Warn2([string]$msg) { Write-Host "    [警告] $msg" -ForegroundColor Yellow; Write-IshLog "WARN $msg" }
function Fail([string]$msg, [int]$code = 1) {
    Write-Host "`n[失败] $msg" -ForegroundColor Red
    Write-Host "详细日志：$script:LogFile" -ForegroundColor Red
    Write-IshLog "FAIL($code) $msg"
    exit $code
}

function Start-Log([string]$name) {
    New-Item -ItemType Directory -Force -Path $script:Logs | Out-Null
    $script:LogFile = Join-Path $script:Logs ("{0}-{1}.log" -f $name, (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Write-IshLog "==== $name 开始 $(Get-Date -Format s) 用户=$env:USERNAME 计算机=$env:COMPUTERNAME 目录=$script:Root"
}
function Write-IshLog([string]$line) { if ($script:LogFile) { Add-Content -LiteralPath $script:LogFile -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $line) -Encoding UTF8 } }
# 控制台与原生程序输出统一按 UTF-8（Python 以 -X utf8 运行）
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { Write-Verbose 'console encoding unchanged' }
$OutputEncoding = [Text.Encoding]::UTF8

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Assert-Admin([string]$why) {
    if (-not (Test-Admin)) { Fail "需要以管理员身份运行（原因：$why）。请右键 .bat → 以管理员身份运行。" 10 }
}

function Assert-CanReadConfig {
    if ((Test-Path -LiteralPath $script:Config) -and -not (Test-Path -LiteralPath $script:AppEnv -ErrorAction SilentlyContinue)) {
        try { Get-ChildItem -LiteralPath $script:Config -ErrorAction Stop | Out-Null } catch { Fail '无权读取 config 目录：请右键以管理员身份运行。' 10 }
    }
}

function Read-AppEnv {
    $h = @{}
    if (Test-Path -LiteralPath $script:AppEnv) {
        foreach ($l in Get-Content -LiteralPath $script:AppEnv -Encoding UTF8) {
            $t = $l.Trim()
            if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) { $k, $v = $t.Split('=', 2); $h[$k.Trim()] = $v.Trim() }
        }
    }
    return $h
}

function Invoke-Exe([string]$Exe, [string[]]$ExeArgs) {
    # Windows PowerShell 5.1：在 ErrorAction=Stop 下把原生程序 stderr 重定向会抛出终止错误，此处局部改为 Continue
    $ErrorActionPreference = 'Continue'
    Write-IshLog ("exec " + (Split-Path $Exe -Leaf) + ' ' + ($ExeArgs -join ' '))
    & $Exe @ExeArgs 2>&1 | ForEach-Object { $l = "$_"; Add-Content -LiteralPath $script:LogFile -Value $l -Encoding UTF8; $l } | Out-Host
    return $LASTEXITCODE
}

function Invoke-Manage([string[]]$ManageArgs, [switch]$AllowFail, [hashtable]$ExtraEnv) {
    $ErrorActionPreference = 'Continue'
    $env:ISH_CONFIG_FILE = $script:AppEnv
    $env:PATH = $script:PgBin + [IO.Path]::PathSeparator + $env:PATH
    if ($ExtraEnv) { foreach ($k in $ExtraEnv.Keys) { Set-Item -Path "env:$k" -Value $ExtraEnv[$k] } }
    Push-Location -LiteralPath $script:Backend
    try {
        Write-IshLog ("manage.py " + ($ManageArgs -join ' '))
        # 嵌入式 Python 存在 ._pth 时会忽略 PYTHON* 环境变量，因此用命令行 -X utf8 -B
        & $script:Python '-X' 'utf8' '-B' 'manage.py' @ManageArgs 2>&1 | ForEach-Object { $l = "$_"; Add-Content -LiteralPath $script:LogFile -Value $l -Encoding UTF8; $l } | Out-Host
        $rc = $LASTEXITCODE
    } finally { Pop-Location; if ($ExtraEnv) { foreach ($k in $ExtraEnv.Keys) { Remove-Item -Path "env:$k" -ErrorAction SilentlyContinue } } }
    if ($rc -ne 0 -and -not $AllowFail) { Fail ("命令失败（退出码 $rc）：manage.py " + ($ManageArgs -join ' ')) 20 }
    return $rc
}

function Get-ServiceState([string]$name) {
    $s = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($null -eq $s) { return 'NotInstalled' }
    return [string]$s.Status
}

function Wait-Http([string]$url, [int]$seconds = 90) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $r.Content }
        } catch { Start-Sleep -Seconds 2 }
    }
    return $null
}

function Wait-Db([int]$port, [int]$seconds = 60) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        & (Join-Path $script:PgBin 'pg_isready.exe') -h 127.0.0.1 -p $port -q
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

# RC3-G5：应用防火墙规则只用“确切名称”，安装/卸载只删除本程序创建的规则；手工建立的 HTTPS 443 等规则不受影响
$script:AppFwRulePrefix = 'Industrial Sales Hub App (TCP '
function Get-AppFirewallRules {
    # 只匹配本程序创建的应用端口规则（新名称 + RC1/RC2 旧名称“Industrial Sales Hub (TCP <端口>)”），不匹配 HTTPS 代理规则
    return @(Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' -ErrorAction SilentlyContinue | Where-Object {
            $_.DisplayName.StartsWith($script:AppFwRulePrefix) -or $_.DisplayName -match '^Industrial Sales Hub \(TCP \d+\)$' })
}

function Select-LanAddress([object[]]$cands) {
    # 纯函数（便于测试）：$cands 为 @{ IP; Alias; Gateway; Virtual } 列表。优先：有默认网关的实体网卡 → 无网关的实体网卡 → 其余
    $ranked = @($cands | Sort-Object @{ Expression = { if ($_.Gateway -and -not $_.Virtual) { 0 } elseif (-not $_.Virtual) { 1 } else { 2 } } }, @{ Expression = { $_.Alias } })
    return @($ranked | ForEach-Object { $_.IP })
}

function Get-LanCandidates {
    $out = @()
    try {
        $cfgs = Get-NetIPConfiguration -ErrorAction Stop
        foreach ($c in $cfgs) {
            $virtual = $false
            $desc = "$($c.InterfaceDescription) $($c.InterfaceAlias)"
            if ($desc -match 'Hyper-V|vEthernet|VirtualBox|VMware|TAP|Tunnel|VPN|WireGuard|ZeroTier|Tailscale|Loopback|Bluetooth|WSL') { $virtual = $true }
            foreach ($a in @($c.IPv4Address)) {
                if (-not $a) { continue }
                $ip = $a.IPAddress
                if ($ip -like '127.*' -or $ip -like '169.254.*') { continue }
                $out += [pscustomobject]@{ IP = $ip; Alias = $c.InterfaceAlias; Gateway = [bool]$c.IPv4DefaultGateway; Virtual = $virtual }
            }
        }
    } catch {
        foreach ($ip in [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) | Where-Object { $_.AddressFamily -eq 'InterNetwork' -and -not $_.ToString().StartsWith('127.') }) {
            $out += [pscustomobject]@{ IP = $ip.ToString(); Alias = '?'; Gateway = $false; Virtual = $false }
        }
    }
    return @($out)
}

function Get-LanAddresses {
    # 首个地址 = 推荐给员工使用的地址（默认路由所在的实体网卡）；config\app.env 的 ISH_PREFERRED_IP 可人工指定
    $pref = (Read-AppEnv)['ISH_PREFERRED_IP']
    $ips = Select-LanAddress (Get-LanCandidates)
    if ($pref) { $ips = @($pref) + @($ips | Where-Object { $_ -ne $pref }) }
    return @($ips)
}

function New-RandomSecret([int]$bytes = 48) {
    $b = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return ([Convert]::ToBase64String($b) -replace '[+/=]', 'x')
}

function Test-PortFree([int]$port) {
    $l = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    return ($null -eq $l)
}

function Test-SyncedFolder([string]$path) {
    foreach ($k in @('OneDrive', 'Dropbox', 'Google Drive', 'iCloudDrive', '坚果云', 'BaiduNetdisk', '百度网盘')) {
        if ($path -like "*$k*") { return $k }
    }
    if ($env:OneDrive -and $path.StartsWith($env:OneDrive, [StringComparison]::OrdinalIgnoreCase)) { return 'OneDrive' }
    return $null
}

# 内置账户一律使用 SID（中文/德文等本地化系统中组名不同）：SYSTEM S-1-5-18，Administrators S-1-5-32-544，Users S-1-5-32-545
function Get-Sid([string]$sid) { return New-Object System.Security.Principal.SecurityIdentifier($sid) }
function Get-ServiceAccount([string]$svc) { return New-Object System.Security.Principal.NTAccount('NT SERVICE', $svc) }

function Set-RootAcl {
    # 安装根目录：关闭继承；SYSTEM/Administrators 完全控制；普通用户只读（防止本地用户篡改 SYSTEM 计划任务执行的脚本与运行时）
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inh = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $none = [System.Security.AccessControl.PropagationFlags]::None
    foreach ($id in @((Get-Sid 'S-1-5-18'), (Get-Sid 'S-1-5-32-544'))) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($id, 'FullControl', $inh, $none, 'Allow')))
    }
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule((Get-Sid 'S-1-5-32-545'), 'ReadAndExecute', $inh, $none, 'Allow')))
    foreach ($svc in @($script:SvcApp, $script:SvcDb)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule((Get-ServiceAccount $svc), 'ReadAndExecute', $inh, $none, 'Allow')))
    }
    Set-Acl -LiteralPath $script:Root -AclObject $acl
}

function Grant-ServiceDirs {
    # 敏感目录关闭继承，普通用户（Users）不可读：
    #   data（含 private 报价 PDF/附件、运维日志、恢复/升级暂存）、logs（含账号与 IP）、config（含数据库密码与签名密钥）、backups
    $app = Get-ServiceAccount $script:SvcApp
    $db = Get-ServiceAccount $script:SvcDb
    Protect-Directory $script:Data @(@($app, 'Modify'), @($db, 'Modify'))
    Protect-Directory $script:PgData @(, @($db, 'Modify'))
    Protect-Directory (Join-Path $script:Data 'private') @(, @($app, 'Modify'))
    Protect-Directory $script:Logs @(@($app, 'Modify'), @($db, 'Modify'))
    Protect-Directory $script:Config @(, @($app, 'ReadAndExecute'))
    Protect-Directory (Join-Path $script:Config 'admin-only') @()
    Protect-Directory $script:Backups @()
    $cfgBk = (Read-AppEnv)['ISH_BACKUP_DIR']
    if ($cfgBk -and (Test-Path -LiteralPath $cfgBk) -and ($cfgBk.TrimEnd('\') -ne $script:Backups)) { Protect-Directory $cfgBk @() }
}

function Protect-Directory([string]$path, [object[]]$grants = @()) {
    # 仅 SYSTEM、Administrators 与指定服务账户可访问（去除继承的 Users/Everyone）；grants = @(@(账户, 权限), ...)
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inh = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $prop = [System.Security.AccessControl.PropagationFlags]::None
    foreach ($id in @((Get-Sid 'S-1-5-18'), (Get-Sid 'S-1-5-32-544'))) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($id, 'FullControl', $inh, $prop, 'Allow')))
    }
    foreach ($g in $grants) {
        if ($g -and $g.Count -eq 2 -and $g[1] -ne 'None') {
            $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($g[0], $g[1], $inh, $prop, 'Allow')))
        }
    }
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    Set-Acl -LiteralPath $path -AclObject $acl
}

function Test-AsciiPath([string]$path) { return ($path -cmatch '^[\x20-\x7E]+$') }

function Verify-PackageChecksums([string]$Base = $script:Root) {
    if (-not (Test-Path -LiteralPath (Join-Path $Base 'SHA256SUMS.txt'))) { Fail '安装包缺少 SHA256SUMS.txt，可能不完整。' 11 }
    $bad = @(Test-PackageChecksums $Base)
    if ($bad.Count) { Fail ("安装包校验失败（{0} 项）：`n  {1}" -f $bad.Count, (($bad | Select-Object -First 10) -join "`n  ")) 12 }
    $n = @(Get-Content -LiteralPath (Join-Path $Base 'SHA256SUMS.txt') -Encoding UTF8 | Where-Object { $_.Trim() }).Count
    Write-Ok "安装包校验通过（$n 个文件）"
}

# ======================================================================================
# RC2：运维互斥锁、维护标记、日志状态机、平台层（服务/ACL）、故障注入
# ======================================================================================

# ---- 平台层：正式环境调用 Windows 服务；测试机模拟（仅当 data\ALLOW_SIMULATION 存在且设置 ISH_SIM_HOOK）----
function Test-SimPlatform {
    return ($env:ISH_SIM_HOOK -and (Test-Path -LiteralPath (Join-Path $script:Data 'ALLOW_SIMULATION')))
}
function Stop-IshService([string]$name, [int]$timeoutSec = 90) {
    if (Test-SimPlatform) { & $env:ISH_SIM_HOOK 'stop' $name $script:Root | Out-Host; return }
    $s = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($null -eq $s -or $s.Status -eq 'Stopped') { return }
    Stop-Service -Name $name -ErrorAction Stop
    $s.WaitForStatus('Stopped', [TimeSpan]::FromSeconds($timeoutSec))
    Write-IshLog "service stopped $name"
}
function Start-IshService([string]$name, [int]$timeoutSec = 90) {
    if (Test-SimPlatform) { & $env:ISH_SIM_HOOK 'start' $name $script:Root | Out-Host; return }
    $s = Get-Service -Name $name -ErrorAction Stop
    if ($s.Status -ne 'Running') { Start-Service -Name $name -ErrorAction Stop; $s.WaitForStatus('Running', [TimeSpan]::FromSeconds($timeoutSec)) }
    Write-IshLog "service started $name"
}
function Set-IshAcls {
    if (Test-SimPlatform) { Write-IshLog 'ACL skipped (simulation)'; return }
    Set-RootAcl
    Grant-ServiceDirs
}

function Test-FaultInjection([string]$stage) {
    # 仅测试机：ISH_FAULT_INJECT 含该阶段名 且 data\ALLOW_FAULT_INJECTION 存在
    if (-not $env:ISH_FAULT_INJECT -and -not $env:ISH_FAULT_CRASH) { return }
    if (-not (Test-Path -LiteralPath (Join-Path $script:Data 'ALLOW_FAULT_INJECTION'))) { return }
    if (($env:ISH_FAULT_INJECT -split ',') -contains $stage) { throw "故障注入：$stage" }
    if ($env:ISH_FAULT_CRASH -and (($env:ISH_FAULT_CRASH -split ',') -contains $stage)) {
        Write-IshLog "SIMULATED CRASH at $stage"
        [Environment]::Exit(99)   # 模拟断电/进程被杀：不执行 finally、不回滚，锁由操作系统释放
    }
}

# ---- 全局运维锁：与 Python apps.common.opslock 共用 data\ops.lock / ops.owner.json ----
function Enter-OpsLock([string]$task, [int]$waitSec = 0) {
    New-Item -ItemType Directory -Force -Path $script:Data | Out-Null
    $lockPath = Join-Path $script:Data 'ops.lock'
    $deadline = (Get-Date).AddSeconds($waitSec)
    while ($true) {
        try {
            $script:OpsHandle = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            break
        } catch [System.UnauthorizedAccessException] {
            return $null, "无权访问 $lockPath：请右键以管理员身份运行。"
        } catch {
            if ((Get-Date) -ge $deadline) {
                $o = $null
                try { $o = Get-Content -LiteralPath (Join-Path $script:Data 'ops.owner.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $o = $null }
                $who = if ($o) { "$($o.task)（开始于 $($o.started_at)，进程 $($o.pid)）" } else { '未知任务' }
                return $null, "另一个运维任务正在进行：$who"
            }
            Start-Sleep -Milliseconds 500
        }
    }
    # 上一个任务的父窗口被强制关闭时，操作系统已释放锁，但它启动的 manage.py 子进程可能仍在运行：此时视为忙
    $prev = $null; try { $prev = Get-Content -LiteralPath (Join-Path $script:Data 'ops.owner.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $prev = $null }
    if ($prev -and $prev.token -and $prev.PSObject.Properties['children']) {
        $alive = @($prev.children | Where-Object { $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
        if ($alive.Count) {
            $script:OpsHandle.Close(); $script:OpsHandle = $null
            return $null, ("上一个运维任务（{0}）的子进程仍在运行（PID {1}），可能是窗口被强制关闭。请等待其结束后重试。" -f $prev.task, ($alive -join ','))
        }
    }
    $script:OpsToken = (New-RandomSecret 16) -replace '[^A-Za-z0-9]', 'x'
    $info = [ordered]@{ task = $task; token = $script:OpsToken; pid = $PID; host = $env:COMPUTERNAME; started_at = (Get-Date -Format s); children = @() }
    Write-AtomicText (Join-Path $script:Data 'ops.owner.json') ($info | ConvertTo-Json -Compress)
    Add-Content -LiteralPath (Join-Path $script:Data 'ops.history.log') -Value (([ordered]@{ event = 'acquire'; task = $task; pid = $PID; at = (Get-Date -Format s) }) | ConvertTo-Json -Compress) -Encoding UTF8
    $env:ISH_OPS_TOKEN = $script:OpsToken   # 子进程 manage.py 继承同一运维任务
    Write-IshLog "ops lock acquired task=$task"
    return $script:OpsToken, $null
}
function Exit-OpsLock {
    if ($script:OpsHandle) {
        try {
            $owner = Join-Path $script:Data 'ops.owner.json'
            $o = Get-Content -LiteralPath $owner -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($o.token -eq $script:OpsToken) { $o.token = ''; Write-AtomicText $owner ($o | ConvertTo-Json -Compress -Depth 4) }
            Add-Content -LiteralPath (Join-Path $script:Data 'ops.history.log') -Value (([ordered]@{ event = 'release'; pid = $PID; at = (Get-Date -Format s) }) | ConvertTo-Json -Compress) -Encoding UTF8
        } catch { Write-IshLog "ops owner update failed: $($_.Exception.Message)" }
        $script:OpsHandle.Close(); $script:OpsHandle = $null
        Remove-Item env:ISH_OPS_TOKEN -ErrorAction SilentlyContinue
        Write-IshLog 'ops lock released'
    }
}
function Write-AtomicText([string]$path, [string]$text) {
    $tmp = "$path.$PID.$([guid]::NewGuid().ToString('N').Substring(0,8)).tmp"
    [System.IO.File]::WriteAllText($tmp, $text, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $path -Force
}

# ---- 维护标记：内容 JSON {token, task, pid, at}；只能删除自己的 ----
function Get-MaintenanceOwner {
    $f = Join-Path $script:Data 'MAINTENANCE'
    if (-not (Test-Path -LiteralPath $f)) { return $null }
    try { return (Get-Content -LiteralPath $f -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return [pscustomobject]@{ token = ''; task = 'legacy'; at = '' } }
}
function Set-Maintenance([string]$task) {
    $cur = Get-MaintenanceOwner
    if ($cur -and $cur.token -ne $script:OpsToken) { return "系统处于维护状态（$($cur.task) $($cur.at)），可能是上次升级/恢复失败后保持的只读状态。请先运行 06-检查系统 并按手册处理。" }
    Write-AtomicText (Join-Path $script:Data 'MAINTENANCE') (([ordered]@{ token = $script:OpsToken; task = $task; pid = $PID; at = (Get-Date -Format s) }) | ConvertTo-Json -Compress)
    return $null
}
function Clear-Maintenance {
    $cur = Get-MaintenanceOwner
    if ($cur -and $cur.token -eq $script:OpsToken) { Remove-Item -LiteralPath (Join-Path $script:Data 'MAINTENANCE') -Force; return $true }
    return ($null -eq $cur)
}

# ---- 运维日志（状态机）：data\ops\<kind>-<id>.json，逐步记录，崩溃后可诊断与回退 ----
function New-OpsJournal([string]$kind) {
    New-Item -ItemType Directory -Force -Path $script:OpsDir | Out-Null
    $id = (Get-Date -Format 'yyyyMMddHHmmss') + ('{0:D3}' -f ($PID % 1000))
    $j = [ordered]@{ kind = $kind; id = $id; state = 'RUNNING'; phase = 'NEW'; steps = @(); created_at = (Get-Date -Format s); host = $env:COMPUTERNAME }
    $script:Journal = $j
    $script:JournalPath = Join-Path $script:OpsDir "$kind-$id.json"
    Save-OpsJournal
    return $id
}
function Read-OpsJournal([string]$kind, [string]$id) {
    $p = Join-Path $script:OpsDir "$kind-$id.json"
    if (-not (Test-Path -LiteralPath $p)) { Fail "找不到运维日志：$p" 60 }
    $o = Get-Content -LiteralPath $p -Raw -Encoding UTF8 | ConvertFrom-Json
    $h = [ordered]@{}
    foreach ($pr in $o.PSObject.Properties) { $h[$pr.Name] = $pr.Value }
    $h['steps'] = @($o.steps)
    $script:Journal = $h; $script:JournalPath = $p
    return $h
}
function Save-OpsJournal { Write-AtomicText $script:JournalPath ($script:Journal | ConvertTo-Json -Depth 6) }
function Set-OpsPhase([string]$phase) { $script:Journal.phase = $phase; Save-OpsJournal; Write-Step $phase }
function Add-OpsStep([hashtable]$step) {
    $step['at'] = (Get-Date -Format s)
    $script:Journal.steps = @($script:Journal.steps) + @([pscustomobject]$step)
    Save-OpsJournal
    Write-IshLog ("journal step " + ($step | ConvertTo-Json -Compress))
}

function New-OpsTempDir([string]$name) {
    # 恢复演练等需要临时存放私有文件的目录：位于安装根目录下 _ops-tmp（在 data 之外，满足“不得恢复到正式数据目录”），
    # 目录仅 SYSTEM/Administrators 可访问。返回的子目录本身不创建（演练命令要求目录不存在或为空）。
    $base = Join-Path $script:Root '_ops-tmp'
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    if (-not (Test-SimPlatform)) { Protect-Directory $base @() }
    return (Join-Path $base $name)
}
function Test-Prop($obj, [string]$name) { return ($null -ne $obj -and $null -ne $obj.PSObject.Properties[$name] -and $obj.$name) }

function Get-DbConn {
    # 解析 app.env 的 DATABASE_URL（应用账号），供 psql 直接操作数据库（快照/改名）
    $cfg = Read-AppEnv
    $u = [Uri]($cfg['DATABASE_URL'] -replace '^postgres(ql)?:', 'http:')
    $ui = $u.UserInfo.Split(':', 2)
    return [pscustomobject]@{ User = [Uri]::UnescapeDataString($ui[0]); Password = [Uri]::UnescapeDataString($ui[1]); Port = $u.Port; Db = $u.AbsolutePath.TrimStart('/') }
}
function Invoke-Psql([string]$sql, [string]$db = 'postgres', [switch]$AllowFail) {
    $c = Get-DbConn
    $env:PGPASSWORD = $c.Password
    try {
        $ErrorActionPreference = 'Continue'
        $out = & (Join-Path $script:PgBin 'psql.exe') -X -q -h 127.0.0.1 -p $c.Port -U $c.User -d $db -v ON_ERROR_STOP=1 -tAc $sql 2>&1
        $rc = $LASTEXITCODE
    } finally { Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue }
    Write-IshLog "psql rc=$rc $sql"
    if ($rc -ne 0 -and -not $AllowFail) { throw "psql 失败（$rc）：$sql`n$out" }
    return (($out | ForEach-Object { "$_" }) -join "`n").Trim()
}

function Get-ReleaseItems([string]$base) {
    # “发行文件”= 安装包中除 data/config/backups/logs/services 外的全部顶层项目（由 SHA256SUMS.txt 推导，加上清单本身）
    $sums = Join-Path $base 'SHA256SUMS.txt'
    $top = @{}
    foreach ($line in Get-Content -LiteralPath $sums -Encoding UTF8) {
        if (-not $line.Trim()) { continue }
        $rel = ($line -split '  ', 2)[1]
        $first = ($rel -split '/')[0]
        $top[$first] = $true
    }
    $top['SHA256SUMS.txt'] = $true
    foreach ($forbidden in @('data', 'config', 'backups', 'logs', 'services')) {
        if ($top.ContainsKey($forbidden)) { throw "安装包不应包含 $forbidden（可能被篡改）" }
    }
    return @($top.Keys | Sort-Object)
}

function Test-PackageChecksums([string]$Base) {
    # 与 Verify-PackageChecksums 相同，但返回错误列表而不是退出（升级/回滚中需要先回退再报错）
    $sums = Join-Path $Base 'SHA256SUMS.txt'
    if (-not (Test-Path -LiteralPath $sums)) { return @('缺少 SHA256SUMS.txt') }
    $bad = @()
    foreach ($line in Get-Content -LiteralPath $sums -Encoding UTF8) {
        if (-not $line.Trim()) { continue }
        $hash, $rel = $line -split '  ', 2
        if ($rel -match '(^|/)\.\.(/|$)' -or $rel.StartsWith('/') -or $rel -match '^[A-Za-z]:') { $bad += "非法路径 $rel"; continue }
        $p = Join-Path $Base ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $p)) { $bad += "缺失 $rel"; continue }
        if ((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() -ne $hash) { $bad += "不一致 $rel" }
    }
    return $bad
}

function Test-SensitiveAcl {
    # 返回问题列表：普通用户（Users/Authenticated Users/Everyone）不得对敏感目录有任何允许的访问；
    # 不得对安装根目录、scripts、runtime、app 有写入/修改权限（SYSTEM 计划任务执行这些脚本）
    $problems = @()
    $lowSids = @('S-1-5-32-545', 'S-1-5-11', 'S-1-1-0')
    $write = [System.Security.AccessControl.FileSystemRights]'Write, Modify, FullControl, CreateFiles, AppendData, Delete, ChangePermissions, TakeOwnership'
    foreach ($d in @($script:Config, $script:Data, $script:Logs, $script:Backups, (Join-Path $script:Config 'admin-only'))) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        foreach ($r in (Get-Acl -LiteralPath $d).Access) {
            try { $sid = $r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { $sid = "$($r.IdentityReference)" }
            if ($r.AccessControlType -eq 'Allow' -and $lowSids -contains $sid) { $problems += "$d 允许 $($r.IdentityReference) $($r.FileSystemRights)" }
        }
    }
    foreach ($d in @($script:Root, (Join-Path $script:Root 'scripts'), $script:Runtime, $script:App)) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        foreach ($r in (Get-Acl -LiteralPath $d).Access) {
            try { $sid = $r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { $sid = "$($r.IdentityReference)" }
            if ($r.AccessControlType -eq 'Allow' -and $lowSids -contains $sid -and ($r.FileSystemRights -band $write)) { $problems += "$d 允许 $($r.IdentityReference) 写入（$($r.FileSystemRights)）" }
        }
    }
    return $problems
}
