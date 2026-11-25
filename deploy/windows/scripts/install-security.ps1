# 安装专用权限检查与 Windows PostgreSQL 初始化。由 install.ps1 在已保护的解包目录中加载。

function Assert-InstallAcl([string]$path, [string[]]$allowed, [long]$mask, [switch]$DirectOnly) {
    $item = Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "不允许重解析点：$path" }
    $acl = Get-Acl -LiteralPath $path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $allowed) { throw "安装路径的所有者不受信任：$path" }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($DirectOnly -and ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)) { continue }
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $allowed -and ([long]$rule.FileSystemRights -band $mask)) {
            throw "安装路径允许非授权账户访问或改动：$path"
        }
    }
}

function Assert-InstallTree {
    $admin = @('S-1-5-18', 'S-1-5-32-544')
    $systemOwners = $admin + 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464' # TrustedInstaller
    $writeMask = 0x500D0156 # GENERIC_ALL/WRITE、删除、改 ACL/所有者、目录新增/改写
    # 父目录的 DELETE_CHILD 也可绕过子目录的删除权限。卷根目录自身无法被重命名或删除。
    $parent = [IO.Directory]::GetParent($Root)
    while ($parent) {
        $mask = if ($parent.Parent) { $writeMask } else { 0x500C0040 }
        Assert-InstallAcl $parent.FullName $systemOwners $mask -DirectOnly
        $parent = $parent.Parent
    }
    Assert-InstallAcl $Root $admin $writeMask
    foreach ($entry in Get-ChildItem -LiteralPath $Root -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "不允许重解析点：$($entry.FullName)" }
        if (-not $entry.PSIsContainer -or $entry.Name -in 'app','runtime','scripts','tools','services','config-templates') {
            Assert-InstallAcl $entry.FullName $admin $writeMask
            if ($entry.PSIsContainer) {
                foreach ($file in Get-ChildItem -LiteralPath $entry.FullName -Force -Recurse) {
                    Assert-InstallAcl $file.FullName $admin $writeMask
                }
            }
        }
    }
    $appSids = @(); $dbSids = @()
    if (Get-Service -Name $SvcApp -ErrorAction SilentlyContinue) { $appSids += (Get-ServiceAccount $SvcApp).Translate([Security.Principal.SecurityIdentifier]).Value }
    if (Get-Service -Name $SvcDb -ErrorAction SilentlyContinue) { $dbSids += (Get-ServiceAccount $SvcDb).Translate([Security.Principal.SecurityIdentifier]).Value }
    foreach ($path in $Config, $Data, $Logs, $Backups, $AdminOnly, $PgData, (Join-Path $Data 'private')) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $allowed = $admin
        if ($path -in $Data, $Logs) { $allowed += $appSids + $dbSids }
        if ($path -in $Config, (Join-Path $Data 'private')) { $allowed += $appSids }
        if ($path -eq $PgData) { $allowed += $dbSids }
        Assert-InstallAcl $path $allowed -1
    }
    if (Test-Path -LiteralPath $Config) {
        foreach ($item in Get-ChildItem -LiteralPath $Config -Force -Recurse) {
            $allowed = if ($item.FullName -eq $AdminOnly -or $item.FullName.StartsWith("$AdminOnly\", [StringComparison]::OrdinalIgnoreCase)) { $admin } else { $admin + $appSids }
            Assert-InstallAcl $item.FullName $allowed -1
        }
    }
}

function New-InstallPrivateDirectory([string]$path) {
    if (Test-Path -LiteralPath $path) { return }
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner((Get-Sid 'S-1-5-32-544'))
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new((Get-Sid $sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    if ($PSVersionTable.PSEdition -eq 'Desktop') { [IO.Directory]::CreateDirectory($path, $acl) | Out-Null }
    else { [IO.FileSystemAclExtensions]::CreateDirectory($acl, $path) | Out-Null }
    Assert-InstallAcl $path @('S-1-5-18','S-1-5-32-544') -1
}

function Get-InstallInitTaskName {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $suffix = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToUpperInvariant()))).Replace('-', '').Substring(0, 24) }
    finally { $hash.Dispose() }
    return "IndustrialSalesHub-InitDb-$suffix"
}

function Clear-InstallInitTask([string]$taskName, [string]$exe, [string]$pwFile) {
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task) {
        $action = @($task.Actions)
        if ($action.Count -ne 1 -or $action[0].Execute -ne $exe -or $action[0].WorkingDirectory -ne $Root -or $task.Principal.UserId -notin 'SYSTEM','NT AUTHORITY\SYSTEM','S-1-5-18') {
            throw '初始化任务不属于当前安装目录，保留现场。'
        }
        if ($task.State -in 'Running','Queued') { throw '当前安装目录的初始化任务仍在运行，请待其结束后重试。' }
        Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false
    }
    if (Test-Path -LiteralPath $pwFile) {
        Assert-InstallAcl $pwFile @('S-1-5-18','S-1-5-32-544') -1
        Remove-Item -LiteralPath $pwFile
    }
}

function Invoke-InstallInitDb([string]$staging, [string]$password) {
    if ($env:PG_RESTRICT_EXEC -or [Environment]::GetEnvironmentVariable('PG_RESTRICT_EXEC', 'Machine')) { throw '检测到非空 PG_RESTRICT_EXEC，停止初始化。' }
    $exe = Join-Path $PgBin 'initdb.exe'
    $pwFile = Join-Path $AdminOnly 'initdb-bootstrap.pw'
    $taskName = Get-InstallInitTaskName
    Clear-InstallInitTask $taskName $exe $pwFile
    New-InstallPrivateDirectory $staging
    Assert-InstallAcl $staging @('S-1-5-18','S-1-5-32-544') -1
    try {
        [IO.File]::WriteAllText($pwFile, $password, [Text.Encoding]::ASCII)
        Assert-InstallAcl $pwFile @('S-1-5-18','S-1-5-32-544') -1
        $arguments = "-D `"$staging`" -U postgres --pwfile=`"$pwFile`" -E UTF8 --locale=C --auth-local=scram-sha-256 --auth-host=scram-sha-256"
        $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $Root
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Settings $settings -User 'SYSTEM' -RunLevel Highest | Out-Null
        $before = (Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\').LastRunTime
        Start-ScheduledTask -TaskName $taskName -TaskPath '\'
        $deadline = (Get-Date).AddMinutes(4)
        do {
            Start-Sleep -Milliseconds 500
            $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\'
            $state = (Get-ScheduledTask -TaskName $taskName -TaskPath '\').State
            if ($info.LastRunTime -gt $before -and $state -notin 'Running','Queued') {
                Write-IshLog "PostgreSQL initdb SYSTEM task rc=$($info.LastTaskResult)"
                return $info.LastTaskResult
            }
        } while ((Get-Date) -lt $deadline)
        throw 'PostgreSQL 初始化超时。'
    } finally {
        Clear-InstallInitTask $taskName $exe $pwFile
    }
}

function Initialize-InstallDatabase([int]$port) {
    $superFile = Join-Path $AdminOnly 'postgres-superuser.txt'
    $superTmp = "$superFile.install.tmp"
    Clear-InstallInitTask (Get-InstallInitTaskName) (Join-Path $PgBin 'initdb.exe') (Join-Path $AdminOnly 'initdb-bootstrap.pw')
    if (Test-Path -LiteralPath $superTmp) { Remove-Item -LiteralPath $superTmp }
    if (Test-Path -LiteralPath $PgData) {
        $version = Join-Path $PgData 'PG_VERSION'
        if (-not (Test-Path -LiteralPath $version -PathType Leaf) -or (Get-Content -LiteralPath $version -Raw).Trim() -ne '16' -or
            -not (Test-Path -LiteralPath $superFile -PathType Leaf) -or
            -not (Test-Path -LiteralPath (Join-Path $PgData 'global\pg_control') -PathType Leaf)) {
            throw '数据库目录存在，但无法证明初始化完整。保留该目录和凭据，请联系实施人员。'
        }
        return $false
    }

    # 独立暂存目录初始化成功后才发布。中断或失败时保留原路径，不删除未知数据。
    $staging = Join-Path $Data ('pgdata-init-' + [guid]::NewGuid().ToString('N'))
    $super = New-RandomSecret 24
    $rc = Invoke-InstallInitDb $staging $super
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $staging 'PG_VERSION') -PathType Leaf)) { throw "initdb 失败（退出码 $rc）" }
    @(
        "listen_addresses = '127.0.0.1'"
        "port = $port"
        'max_connections = 60'
        "shared_buffers = '256MB'"
        "password_encryption = 'scram-sha-256'"
        "logging_collector = on"
        "log_directory = 'log'"
        "log_filename = 'postgresql-%a.log'"
        "log_rotation_age = '7d'"
        "lc_messages = 'C'"
        "timezone = 'Asia/Shanghai'"
    ) | Add-Content -LiteralPath (Join-Path $staging 'postgresql.conf') -Encoding ASCII
    @('# 仅本机 scram 认证', 'host all all 127.0.0.1/32 scram-sha-256', 'host all all ::1/128 scram-sha-256') | Set-Content -LiteralPath (Join-Path $staging 'pg_hba.conf') -Encoding ASCII
    try {
        [IO.File]::WriteAllText($superTmp, "postgres 超级用户密码（仅运维紧急使用，勿外传）：$super", [Text.UTF8Encoding]::new($false))
        # 配置提交早于数据目录；仅在正式数据目录不存在时替换上一次中断留下的密码。
        if (Test-Path -LiteralPath $superFile) { [IO.File]::Replace($superTmp, $superFile, [NullString]::Value) }
        else { [IO.File]::Move($superTmp, $superFile) }
    } finally {
        if (Test-Path -LiteralPath $superTmp) { Remove-Item -LiteralPath $superTmp }
    }
    [IO.Directory]::Move($staging, $PgData)
    return $true
}

function Invoke-InstallRoleSql([string]$psql, [int]$port, [string]$password) {
    # psql 通过继承的 stdin 接收 SQL；服务器的错误诊断不记录含密码的语句。
    if ($password -cnotmatch '^[A-Za-z0-9]+$') { throw '数据库应用账号密码格式无效。' }
    $psi = [Diagnostics.ProcessStartInfo]::new($psql, "-X -w -h 127.0.0.1 -p $port -U postgres -d postgres -v ON_ERROR_STOP=1 -f -")
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.EnvironmentVariables['PGCONNECT_TIMEOUT'] = '10'
    $process = [Diagnostics.Process]::Start($psi)
    try {
        $out = $process.StandardOutput.ReadToEndAsync()
        $err = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.WriteLine("SET log_statement = 'none'; SET log_min_error_statement = 'panic'; SET statement_timeout = '30s';")
        $process.StandardInput.WriteLine("CREATE ROLE ish_app LOGIN PASSWORD '$password' NOSUPERUSER NOCREATEROLE CREATEDB;")
        $process.StandardInput.Close()
        $process.WaitForExit()
        $null = $out.GetAwaiter().GetResult()
        $null = $err.GetAwaiter().GetResult()
        return $process.ExitCode
    } finally { $process.Dispose() }
}

function Install-Svc([string]$id, [string]$xml) {
    $winsw = Join-Path $Runtime 'winsw\WinSW-x64.exe'
    $dir = Join-Path $Services $id
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $dst = Join-Path $dir "$id.exe"
    # 服务运行中时 exe 被占用：哈希相同则不复制（重复安装/修复不失败）
    if (-not (Test-Path -LiteralPath $dst) -or (Get-FileHash -LiteralPath $dst).Hash -ne (Get-FileHash -LiteralPath $winsw).Hash) {
        if ((Get-ServiceState $id) -eq 'Running') { Stop-Service -Name $id }
        Copy-Item -LiteralPath $winsw -Destination $dst -Force
    }
    # WinSW 在创建服务时直接把虚拟账户传给 SCM，省略密码元素（SCM 需要 NULL）。
    $account = "NT SERVICE\$id"
    $definition = [xml]$xml
    $serviceAccount = $definition.CreateElement('serviceaccount')
    $serviceAccount.InnerXml = "<domain>NT SERVICE</domain><user>$id</user>"
    $null = $definition.DocumentElement.AppendChild($serviceAccount)
    Set-Content -LiteralPath (Join-Path $dir "$id.xml") -Value $definition.OuterXml -Encoding UTF8
    if ((Get-ServiceState $id) -eq 'NotInstalled') {
        $rc = Invoke-Exe (Join-Path $dir "$id.exe") @('install')
        if ($rc -ne 0) { Fail "服务 $id 安装失败" 22 }
        Write-Ok "已注册服务 $id"
    } else {
        # WinSW 2.x 在每次启动服务时读取同名 XML，已更新的配置将在下次启动时生效（不重复创建服务）
        Write-Ok "服务 $id 已存在，已更新配置文件（不重复创建）"
    }
    $svc = Get-CimInstance Win32_Service -Filter "Name='$id'"
    if ($svc.StartName -ne $account) {
        # 旧安装中断后可能留有尚未配置账户的服务。只修复已停止的本目录服务。
        if ($svc.State -ne 'Stopped') { Fail "服务 $id 正在用非预期账户运行，请检查并停止该服务后重试。" 22 }
        $rc = Invoke-Exe (Join-Path $env:SystemRoot 'System32\sc.exe') @('config', $id, 'obj=', $account)
        if ($rc -ne 0 -or (Get-CimInstance Win32_Service -Filter "Name='$id'").StartName -ne $account) {
            Fail "无法将服务 $id 配置为虚拟服务账户（退出码 $rc）" 22
        }
    }
}
