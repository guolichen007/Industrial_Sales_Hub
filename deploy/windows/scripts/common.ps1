# Industrial Sales Hub — Windows 局域网部署公共函数（PowerShell 5.1+ / 7）
# 被 01~07 各 .ps1 脚本 dot-source。所有路径均支持中文与空格。
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:App = Join-Path $Root 'app'
$script:Backend = Join-Path $App 'backend'
$script:Runtime = Join-Path $Root 'runtime'
$script:Python = Join-Path $Runtime 'python\python.exe'
$script:PgBin = Join-Path $Runtime 'pgsql\bin'
$script:Config = Join-Path $Root 'config'
$script:AppEnv = Join-Path $Config 'app.env'
$script:Data = Join-Path $Root 'data'
$script:PgData = Join-Path $Data 'pgdata'
$script:Logs = Join-Path $Root 'logs'
$script:Backups = Join-Path $Root 'backups'
$script:Services = Join-Path $Root 'services'
$script:AdminOnly = Join-Path $Config 'admin-only'
$script:SvcDb = 'IndustrialSalesHub-DB'
$script:SvcApp = 'IndustrialSalesHub-App'

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
    $env:PATH = "$script:PgBin;$env:PATH"
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

function Get-LanAddresses {
    $ips = @()
    try {
        $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
            Select-Object -ExpandProperty IPAddress
    } catch {
        $ips = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) | Where-Object { $_.AddressFamily -eq 'InterNetwork' -and -not $_.ToString().StartsWith('127.') } | ForEach-Object { $_.ToString() }
    }
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

function Set-RootAcl {
    # 安装根目录：关闭继承；SYSTEM/Administrators 完全控制；普通用户只读（防止本地用户篡改 SYSTEM 计划任务执行的脚本与运行时）
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inh = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $none = [System.Security.AccessControl.PropagationFlags]::None
    foreach ($id in @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($id, 'FullControl', $inh, $none, 'Allow')))
    }
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Users', 'ReadAndExecute', $inh, $none, 'Allow')))
    foreach ($sid in @('NT SERVICE\' + $script:SvcApp, 'NT SERVICE\' + $script:SvcDb)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'ReadAndExecute', $inh, $none, 'Allow')))
    }
    Set-Acl -LiteralPath $script:Root -AclObject $acl
}

function Grant-ServiceDirs {
    # 在根目录 ACL 基础上，为服务账户授予需要写入的目录；敏感目录关闭继承只允许指定账户
    $appSid = 'NT SERVICE\' + $script:SvcApp
    $dbSid = 'NT SERVICE\' + $script:SvcDb
    Protect-Directory $script:PgData $dbSid 'Modify'
    Protect-Directory $script:Config $appSid 'ReadAndExecute'
    Protect-Directory (Join-Path $script:Config 'admin-only') $null 'None'
    Protect-Directory (Join-Path $script:Data 'private') $appSid 'Modify'
    Protect-Directory $script:Backups $null 'None'
    foreach ($d in @($script:Data, $script:Logs)) {
        $acl = Get-Acl -LiteralPath $d
        foreach ($sid in @($appSid, $dbSid)) { $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))) }
        Set-Acl -LiteralPath $d -AclObject $acl
    }
}

function Protect-Directory([string]$path, [string]$serviceSid, [string]$right = 'Modify') {
    # 仅 SYSTEM、Administrators 与服务账户可访问（去除继承的 Users/Everyone）
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inh = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $prop = [System.Security.AccessControl.PropagationFlags]::None
    foreach ($id in @('NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($id, 'FullControl', $inh, $prop, 'Allow')))
    }
    if ($serviceSid -and $right -ne 'None') {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($serviceSid, $right, $inh, $prop, 'Allow')))
    }
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    Set-Acl -LiteralPath $path -AclObject $acl
}

function Test-AsciiPath([string]$path) { return ($path -cmatch '^[\x20-\x7E]+$') }

function Verify-PackageChecksums([string]$Base = $script:Root) {
    $sums = Join-Path $Base 'SHA256SUMS.txt'
    if (-not (Test-Path -LiteralPath $sums)) { Fail '安装包缺少 SHA256SUMS.txt，可能不完整。' 11 }
    $bad = @(); $n = 0
    foreach ($line in Get-Content -LiteralPath $sums -Encoding UTF8) {
        if (-not $line.Trim()) { continue }
        $hash, $rel = $line -split '  ', 2
        $p = Join-Path $Base ($rel -replace '/', '\')
        $n++
        if (-not (Test-Path -LiteralPath $p)) { $bad += "缺失 $rel"; continue }
        if ((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() -ne $hash) { $bad += "不一致 $rel" }
    }
    if ($bad.Count) { Fail ("安装包校验失败（{0} 项）：`n  {1}" -f $bad.Count, (($bad | Select-Object -First 10) -join "`n  ")) 12 }
    Write-Ok "安装包校验通过（$n 个文件）"
}
