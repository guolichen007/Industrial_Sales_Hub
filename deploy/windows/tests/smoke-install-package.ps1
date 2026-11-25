param(
    [Parameter(Mandatory)][string]$Zip,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ZipSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{40}$')][string]$SourceSha,
    [Parameter(Mandatory)][string]$Evidence,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles ('ISH-InstallQA-' + [guid]::NewGuid().ToString('N'))),
    [string]$CredentialFile
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = [Text.Encoding]::UTF8
$checks = [ordered]@{ sha=$SourceSha; archive_sha256=$ZipSha256; root=$InstallRoot; status='IN_PROGRESS'; steps=[ordered]@{} }
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
function Save-Proof { $checks | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Evidence 'installer-smoke.json') -Encoding UTF8 }
function Assert-Proof([string]$name, [bool]$ok, $detail) {
    $checks.steps[$name] = [ordered]@{pass=$ok; detail=$detail}
    Save-Proof
    if (-not $ok) { throw "Installer smoke check failed: $name" }
    Write-Host "PASS $name"
}
function Invoke-ReleaseBatch([string]$number, [string]$log, [string[]]$options = @()) {
    $entry = @(Get-ChildItem -LiteralPath $InstallRoot -File -Filter "$number-*.bat")
    if ($entry.Count -ne 1) { throw "Expected one official $number batch entry" }
    $ErrorActionPreference = 'Continue'
    '' | & $entry[0].FullName @options 2>&1 | ForEach-Object {
        "$_" -replace '(?i)postgres(?:ql)?://\S+','[db-url]' -replace [regex]::Escape($env:COMPUTERNAME),'[host]' -replace '(?<![\d.])(?!(?:127\.0\.0\.1|0\.0\.0\.0)(?![\d.]))(?:\d{1,3}\.){3}\d{1,3}(?![\d.])','[IPv4]'
    } | Out-File -LiteralPath (Join-Path $Evidence $log) -Encoding UTF8
    return $LASTEXITCODE
}
function Get-SmokeFingerprint {
    $code = @'
import hashlib, json
from django.db import connection
from django.contrib.auth import get_user_model

tables = {}
with connection.cursor() as cursor:
    for name in sorted(connection.introspection.table_names(cursor)):
        cursor.execute('SELECT row_to_json(t)::text FROM ' + connection.ops.quote_name(name) + ' AS t ORDER BY 1')
        payload = json.dumps(cursor.fetchall(), ensure_ascii=True, separators=(',', ':')).encode()
        tables[name] = hashlib.sha256(payload).hexdigest()
print('QAJSON=' + json.dumps({'rows': tables, 'users': list(get_user_model().objects.order_by('username').values_list('username', flat=True))}, sort_keys=True))
'@
    $env:ISH_CONFIG_FILE = $AppEnv
    try {
        $raw = @(& $Python -X utf8 -B (Join-Path $Backend 'manage.py') shell -c $code)
        if ($LASTEXITCODE) { throw 'Django database fingerprint failed' }
    } finally { Remove-Item Env:ISH_CONFIG_FILE -ErrorAction SilentlyContinue }
    $marker = @($raw | Where-Object { $_ -like 'QAJSON=*' })
    if ($marker.Count -ne 1) { throw 'Django database fingerprint is missing' }
    $private = Join-Path $Data 'private'
    [ordered]@{
        database = $marker[0].Substring(7) | ConvertFrom-Json
        config_sha256 = (Get-FileHash -LiteralPath $AppEnv).Hash
        superuser_file_sha256 = (Get-FileHash -LiteralPath (Join-Path $AdminOnly 'postgres-superuser.txt')).Hash
        files = @(Get-ChildItem -LiteralPath $private -File -Recurse | Sort-Object FullName | ForEach-Object {
            [ordered]@{path=$_.FullName.Substring($private.Length + 1); sha256=(Get-FileHash -LiteralPath $_.FullName).Hash}
        })
    }
}

try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion.Major -ne 5 -or -not [Environment]::Is64BitProcess -or
        -not ([Security.Principal.WindowsPrincipal]::new($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Full install requires elevated x64 Windows PowerShell 5.1'
    }
    if (@(Get-ChildItem Env: | Where-Object Name -match '^ISH_(SIM_|FAULT_)').Count) { throw 'Simulation or fault-injection environment present' }
    if ((Split-Path -Parent $InstallRoot) -ne $env:ProgramFiles -or (Split-Path -Leaf $InstallRoot) -notmatch '^ISH-InstallQA-[A-Za-z0-9-]+$' -or (Test-Path -LiteralPath $InstallRoot)) {
        throw 'Use a new QA installation directory directly under Program Files'
    }
    if (@(Get-Service -Name IndustrialSalesHub-DB,IndustrialSalesHub-App -ErrorAction SilentlyContinue).Count -or
        @(Get-ScheduledTask -TaskName IndustrialSalesHub-DailyBackup -ErrorAction SilentlyContinue).Count -or
        @(Get-NetFirewallRule -DisplayName 'Industrial Sales Hub App (TCP *' -ErrorAction SilentlyContinue).Count -or
        @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 8000,55432).Count) {
        throw 'Existing product service, rule, task or required port; preserve it'
    }
    $stage = Join-Path $env:ProgramFiles ('ISH-InstallQA-Stage-' + [guid]::NewGuid().ToString('N'))
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in 'S-1-5-18','S-1-5-32-544') {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
    }
    [IO.Directory]::CreateDirectory($stage,$acl) | Out-Null
    $trustedZip = Join-Path $stage 'verified.zip'
    Copy-Item -LiteralPath $Zip -Destination $trustedZip
    Assert-Proof 'installer ZIP digest' ((Get-FileHash -LiteralPath $trustedZip).Hash -eq $ZipSha256) $ZipSha256
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($trustedZip,$stage)
    $payload = Join-Path $stage 'Industrial_Sales_Hub_2.1.0-rc3_LAN_Windows_x64'
    $version = [IO.File]::ReadAllText((Join-Path $payload 'VERSION.txt')).Trim()
    Assert-Proof 'package Git version' ($version -eq "2.1.0-rc3 (git $SourceSha)") $version
    Set-Acl -LiteralPath $payload -AclObject $acl
    [IO.Directory]::Move($payload,$InstallRoot)
    Remove-Item -LiteralPath $trustedZip
    [IO.Directory]::Delete($stage,$false)
    $admin = 'ish_qa_admin'
    $password = [guid]::NewGuid().ToString('N') + 'Qa9!'
    if ($CredentialFile) {
        $c = [pscredential]::new($admin,(ConvertTo-SecureString $password -AsPlainText -Force))
        $c | Export-Clixml -LiteralPath $CredentialFile -NoClobber
    }
    $options=@('-AppPort','8000','-DbPort','55432','-AllowedRemote','LocalSubnet','-AdminUser',$admin,'-Unattended','-NoBrowser')
    $env:ISH_INIT_ADMIN_PASSWORD=$password
    try { $rc=Invoke-ReleaseBatch '01' 'first-install.log' $options }
    finally { Remove-Item Env:ISH_INIT_ADMIN_PASSWORD -ErrorAction SilentlyContinue; $password=$null }
    Assert-Proof 'official first installation exit' ($rc -eq 0) $rc
    . (Join-Path $InstallRoot 'scripts\common.ps1')
    $before=Get-SmokeFingerprint
    Assert-Proof 'fresh administrator, production database initialized' (@($before.database.users).Count -eq 1 -and $before.database.users[0] -eq $admin -and $before.database.rows.PSObject.Properties.Name -contains 'django_migrations') $before.database.users
    $env:ISH_CONFIG_FILE=$AppEnv
    try {
        & $Python -X utf8 -B (Join-Path $Backend 'manage.py') migrate --check 2>&1 | Out-File -LiteralPath (Join-Path $Evidence 'django-migrations.log') -Encoding UTF8
        Assert-Proof 'Django has no unapplied migrations' ($LASTEXITCODE -eq 0) $LASTEXITCODE
    } finally { Remove-Item Env:ISH_CONFIG_FILE -ErrorAction SilentlyContinue }
    $rc=Invoke-ReleaseBatch '01' 'repeat-install.log' $options
    Assert-Proof 'official repeat installation exit' ($rc -eq 0) $rc
    $after=Get-SmokeFingerprint
    Assert-Proof 'repeat preserves database rows, accounts, QA files and credentials' (($before | ConvertTo-Json -Depth 9 -Compress) -ceq ($after | ConvertTo-Json -Depth 9 -Compress)) @{before=$before;after=$after}
    $phase=(Get-Content -LiteralPath (Join-Path $Data 'install-state.json') -Raw | ConvertFrom-Json).phase
    Assert-Proof 'install-state done' ($phase -eq 'done') $phase
    $svc=@(foreach($name in $SvcDb,$SvcApp) { Get-CimInstance Win32_Service -Filter "Name='$name'" | Select-Object Name,State,StartMode,StartName,PathName,ProcessId })
    $invalid=@($svc | Where-Object { $_.State -ne 'Running' -or $_.StartMode -ne 'Auto' -or $_.ProcessId -le 0 -or $_.StartName -ne "NT SERVICE\$($_.Name)" -or $_.PathName.Trim('"') -ne (Join-Path $Services "$($_.Name)\$($_.Name).exe") })
    Assert-Proof 'both real WinSW virtual services running' ($svc.Count -eq 2 -and -not $invalid.Count) $svc
    $health=Invoke-WebRequest http://127.0.0.1:8000/healthz -UseBasicParsing -TimeoutSec 12
    $body=$health.Content | ConvertFrom-Json
    Assert-Proof 'healthz HTTP 200 ok and non-maintenance' ($health.StatusCode -eq 200 -and $body.status -eq 'ok' -and $body.maintenance -eq $false) $body
    $aclIssues=@(Test-SensitiveAcl)
    Assert-Proof 'real installed directory ACL self-check' (-not $aclIssues.Count) $aclIssues
    $uri=[uri](Read-AppEnv)['DATABASE_URL']
    if($uri.Host -ne '127.0.0.1' -or $uri.Port -ne 55432 -or $uri.AbsolutePath -ne '/ish' -or $uri.UserInfo -notlike 'ish_app:*'){throw 'Expected the local QA database'}
    $psql=Join-Path $PgBin 'psql.exe'
    $conn=@('-X','-w','-h','127.0.0.1','-p','55432','-U','ish_app','-d','ish','-Atc')
    try {
        $env:PGPASSWORD=[uri]::UnescapeDataString($uri.UserInfo.Split(':',2)[1])
        $settings=& $psql @conn "select current_setting('server_version'),current_setting('max_connections'),current_setting('shared_buffers'),current_setting('password_encryption'),current_setting('listen_addresses')" 2>$null
        $right=$LASTEXITCODE -eq 0
        $env:PGPASSWORD=[guid]::NewGuid().ToString('N')
        $ErrorActionPreference='Continue'
        $rejection=@(& $psql @conn 'select 1' 2>&1)
        $badExit=$LASTEXITCODE
        $ErrorActionPreference='Stop'
    } finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    Assert-Proof 'PostgreSQL 16.16 resource settings with loopback and SCRAM' ($right -and $settings -match '^16\.16[^|]*\|60\|256MB\|scram-sha-256\|127\.0\.0\.1$' -and $badExit -ne 0 -and "$rejection" -match 'password authentication failed') @{settings=$settings; wrong_password_exit=$badExit}
    $rc=Invoke-ReleaseBatch '08' 'schedule-backup.log'
    Assert-Proof 'official scheduled backup registration' ($rc -eq 0) $rc
    $task=Get-ScheduledTask -TaskName $BackupTask -TaskPath '\'
    $actions=@($task.Actions)
    $triggers=@($task.Triggers)
    Assert-Proof 'daily backup runs as SYSTEM against the QA installation' ($task.Principal.UserId -in 'SYSTEM','NT AUTHORITY\SYSTEM','S-1-5-18' -and $actions.Count -eq 1 -and $actions[0].Arguments.Contains((Join-Path $Root 'scripts\backup.ps1')) -and $triggers.Count -eq 1 -and $triggers[0].CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger') @{user=$task.Principal.UserId; daily=$triggers.Count}
    $previous=(Get-ScheduledTaskInfo -InputObject $task).LastRunTime
    Start-ScheduledTask -InputObject $task
    $until=(Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 2
        $task=Get-ScheduledTask -TaskName $BackupTask -TaskPath '\'
        $info=Get-ScheduledTaskInfo -InputObject $task
    } while((Get-Date) -lt $until -and ($info.LastRunTime -le $previous -or $task.State -in 'Running','Queued'))
    $backup=Get-Content -LiteralPath (Join-Path $Data 'backup_status.json') -Raw | ConvertFrom-Json
    Assert-Proof 'actual SYSTEM scheduled backup completed' ($info.LastRunTime -gt $previous -and $info.LastTaskResult -eq 0 -and $backup.ok -eq $true) @{result=$info.LastTaskResult; ok=$backup.ok}
    $checks.status='PASS'
} catch {
    $checks.status='FAIL'
    $checks.error=$_.Exception.Message
    Write-Error $checks.error -ErrorAction Continue
} finally { Save-Proof }
if($checks.status -ne 'PASS'){exit 1}
