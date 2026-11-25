param([string]$PgSource, [string]$WinSw, [string]$Work, [switch]$ProbeOnly)
$ErrorActionPreference = 'Stop'

# Probe opening the exact object for a requested access right, without reading bytes or changing it.
Add-Type @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class IshFileProbe {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security,
        uint disposition, uint flags, IntPtr template);
    public static int Open(string path, uint access) {
        using (var f = CreateFile(path, access, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            return f.IsInvalid ? Marshal.GetLastWin32Error() : 0;
        }
    }
}
'@
if ($ProbeOnly) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    $matrix = @(foreach ($case in @(
        @('config\app.env', 1), @('config\app.env', 2), @('config\app.env', 65536), @('config\app.env', 262144),
        @('config\app.env.qa.tmp', 1), @('config\app.env.qa.tmp', 2), @('config\app.env.qa.tmp', 65536),
        @('config\admin-only\test.opaque', 1), @('config\admin-only\test.opaque', 2), @('config\admin-only\test.opaque', 65536),
        @('config', 2), @('config', 64), @('data', 1), @('data', 2), @('data', 64), @('logs', 1), @('backups', 1),
        @('services\qa.xml', 2), @('services\qa.xml', 65536), @('scripts\probe.ps1', 2), @('scripts\probe.ps1', 65536),
        @('runtime\pgsql\bin\initdb.exe', 2), @('runtime\pgsql\bin\initdb.exe', 65536), @('runtime\pgsql\bin\initdb.exe', 262144),
        @('scripts', 2), @('scripts', 64), @('runtime', 2), @('runtime', 64), @('app\immutable.txt', 2), @('app\immutable.txt', 65536), @('app', 2), @('app', 64), @('.', 64)
    )) {
        @{ file = $case[0]; access = $case[1]; error = [IshFileProbe]::Open((Join-Path $Work $case[0]), $case[1]) }
    })
    $elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    @{ elevated = $elevated; tests = $matrix } | ConvertTo-Json -Depth 5 -Compress
    if ($elevated -or @($matrix | Where-Object error -ne 5).Count) { exit 1 }
    exit 0
}

if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion.Major -ne 5) { throw 'Native test requires Windows PowerShell 5.1' }
. (Join-Path $PSScriptRoot '..\scripts\common.ps1')
. (Join-Path $PSScriptRoot '..\scripts\install-security.ps1')
$script:LogFile = $null
if (-not (Test-Admin)) { throw 'Native test requires an administrator token' }
if (-not (Test-Path -LiteralPath (Join-Path $PgSource 'bin\initdb.exe') -PathType Leaf)) { throw 'Provide the verified PostgreSQL 16.16 binary directory' }
if ((Get-FileHash -LiteralPath $WinSw -Algorithm SHA256).Hash -ne '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da') { throw 'WinSW digest mismatch' }
$protectedParent = Join-Path $env:ProgramFiles ('ISH-NativeQA-' + [guid]::NewGuid().ToString('N'))
$fixture = Join-Path $protectedParent 'fixture'
New-InstallPrivateDirectory $protectedParent
New-InstallPrivateDirectory $fixture
# The tester receives read/execute only on the distribution tree, not a privileged starting directory.
foreach ($dir in $protectedParent, $fixture) {
    $acl = Get-Acl -LiteralPath $dir
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new((Get-Sid 'S-1-5-32-545'), 'ReadAndExecute', 'None', 'None', 'Allow'))
    Set-Acl -LiteralPath $dir -AclObject $acl
}
Set-IshRoot $fixture
foreach ($path in $Config, $AdminOnly, $Data, $Logs, $Backups, $Services) { New-InstallPrivateDirectory $path }
foreach ($path in $Runtime, $App, (Join-Path $Root 'scripts'), (Join-Path $Runtime 'winsw')) { New-Item -ItemType Directory -Path $path | Out-Null }
Start-Log 'native-install'
Copy-Item -LiteralPath $PgSource -Destination (Join-Path $Runtime 'pgsql') -Recurse
Copy-Item -LiteralPath $WinSw -Destination (Join-Path $Runtime 'winsw\WinSW-x64.exe')
$probe = Join-Path $Root 'scripts\probe.ps1'
Copy-Item -LiteralPath $PSCommandPath -Destination $probe
# Permit users to run the read-only test script and traverse to test objects.
foreach ($dir in (Join-Path $Root 'scripts'), $Runtime, (Join-Path $Runtime 'pgsql'), $PgBin) {
    $acl = Get-Acl -LiteralPath $dir
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new((Get-Sid 'S-1-5-32-545'), 'ReadAndExecute', 'None', 'None', 'Allow'))
    Set-Acl -LiteralPath $dir -AclObject $acl
}
$acl = Get-Acl -LiteralPath $probe
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new((Get-Sid 'S-1-5-32-545'), 'ReadAndExecute', 'Allow'))
Set-Acl -LiteralPath $probe -AclObject $acl
foreach ($path in $AppEnv, "$AppEnv.qa.tmp", (Join-Path $AdminOnly 'test.opaque'), (Join-Path $Services 'qa.xml'), (Join-Path $App 'immutable.txt')) {
    [IO.File]::WriteAllText($path, 'synthetic test marker, no real secret')
}
Assert-InstallTree
Write-Host "FIXTURE=$fixture"
if ([IshFileProbe]::Open($AppEnv, 1) -or [IshFileProbe]::Open($AppEnv, 2) -or [IshFileProbe]::Open($AppEnv, 65536)) { throw 'Administrator fixture access was denied' }
Write-Host 'PASS protected fresh tree and configuration/temporary files'

# Model an unsafe, pre-existing directory from an older installation. Only grants read on a test marker.
$acl = Get-Acl -LiteralPath $Config
$unsafe = [Security.AccessControl.FileSystemAccessRule]::new((Get-Sid 'S-1-5-32-545'), 'ReadAndExecute', 'Allow')
$acl.AddAccessRule($unsafe); Set-Acl -LiteralPath $Config -AclObject $acl
try {
    $rejected = $false
    try { Assert-InstallTree } catch { $rejected = $_.Exception.Message.Contains($Config) }
    if (-not $rejected) { throw 'Unsafe ACL was not rejected' }
} finally { $acl.RemoveAccessRuleSpecific($unsafe); Set-Acl -LiteralPath $Config -AclObject $acl }
Write-Host 'PASS unsafe ACL rejected before secret generation'

$token = [guid]::NewGuid().ToString('N') + 'Qa7'
$exe = Join-Path $PgBin 'initdb.exe'
if ((& $exe --version) -notmatch '16\.16$') { throw 'Incorrect PostgreSQL version' }
Write-Host 'PASS actual initdb PostgreSQL 16.16'
$failed = Join-Path $Data 'occupied'
New-InstallPrivateDirectory $failed
$protectedFile = Join-Path $failed 'sentinel.txt'
[IO.File]::WriteAllText($protectedFile, 'do not overwrite')
$before = (Get-FileHash -LiteralPath $protectedFile).Hash
$taskName = Get-InstallInitTaskName
$tempSecret = Join-Path $AdminOnly 'initdb-bootstrap.pw'
if ((Invoke-InstallInitDb $failed $token) -eq 0 -or (Get-FileHash -LiteralPath $protectedFile).Hash -ne $before -or (Test-Path -LiteralPath $tempSecret) -or (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) { throw 'The failing native initdb did not leave a safe state' }
if (Test-Path -LiteralPath $PgData) { throw 'The failure created the final data directory' }
Write-Host 'PASS non-empty staging rejected, no changed sentinel, no password or task residue'

# Model a stop after task registration and a previous stop before committing the final data directory.
[IO.File]::WriteAllText($tempSecret, 'test-only stale credential marker')
$action = New-ScheduledTaskAction -Execute $exe -Argument "-D `"$failed`"" -WorkingDirectory $Root
Register-ScheduledTask -TaskName $taskName -Action $action -User 'SYSTEM' -RunLevel Highest | Out-Null
$superFile = Join-Path $AdminOnly 'postgres-superuser.txt'
[IO.File]::WriteAllText($superFile, 'test-only stale saved credential')
[IO.File]::WriteAllText("$superFile.install.tmp", 'test-only stale temporary credential')
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
if (-not (Initialize-InstallDatabase $port) -or -not (Test-Path -LiteralPath (Join-Path $PgData 'PG_VERSION')) -or
    (Test-Path -LiteralPath $tempSecret) -or (Test-Path -LiteralPath "$superFile.install.tmp") -or
    (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) { throw 'Native initdb resume failed' }
Write-Host 'PASS actual SYSTEM initdb resumed safely with no temporary secret or task residue'
$credentialHash = (Get-FileHash -LiteralPath $superFile).Hash
$controlHash = (Get-FileHash -LiteralPath (Join-Path $PgData 'global\pg_control')).Hash
if ((Initialize-InstallDatabase $port) -or $credentialHash -ne (Get-FileHash -LiteralPath $superFile).Hash -or
    $controlHash -ne (Get-FileHash -LiteralPath (Join-Path $PgData 'global\pg_control')).Hash) {
    throw 'Repeated initialization changed the credential or database'
}
Write-Host 'PASS repeated initialization preserves credential and database fingerprints'
$cluster = $PgData
$hba = @(Get-Content -LiteralPath (Join-Path $cluster 'pg_hba.conf') | Where-Object { $_ -match '^(local|host)\s' })
if (-not $hba.Count -or @($hba | Where-Object { $_ -notmatch 'scram-sha-256\s*$' }).Count) { throw 'Unexpected PostgreSQL authentication' }

$suffix = [guid]::NewGuid().ToString('N')
$script:SvcDb = "IshQaDb$suffix"
$script:SvcApp = "IshQaApp$suffix"
$pgCtl = Join-Path $PgBin 'pg_ctl.exe'
$dbXml = @"
<service>
  <id>$SvcDb</id><name>$SvcDb</name><description>Native test database</description><startmode>Manual</startmode>
  <executable>$PgBin\postgres.exe</executable><arguments>-D &quot;$cluster&quot;</arguments>
  <stopexecutable>$pgCtl</stopexecutable><stoparguments>stop -D &quot;$cluster&quot; -m fast -w</stoparguments>
  <logpath>$Logs</logpath>
</service>
"@
$report = Join-Path $Logs 'app-access.txt'
$appCheck = Join-Path $Root 'scripts\app-access.ps1'
$appScript = @'
param($fixture)
$ErrorActionPreference = 'Stop'
$null = [IO.File]::ReadAllText((Join-Path $fixture 'config\app.env'))
$blocked = 0
foreach ($protected in 'config\admin-only\postgres-superuser.txt', 'data\pgdata\PG_VERSION') {
    try { $null = [IO.File]::ReadAllText((Join-Path $fixture $protected)) }
    catch [System.UnauthorizedAccessException] { $blocked++ }
}
try { $null = [IO.Directory]::GetFiles((Join-Path $fixture 'backups')) }
catch [System.UnauthorizedAccessException] { $blocked++ }
[IO.File]::WriteAllText((Join-Path $fixture 'logs\app-access.txt'), ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value + '|' + $blocked))
Start-Sleep -Seconds 90
'@
[IO.File]::WriteAllText($appCheck, $appScript)
$windowsPs = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$appXml = @"
<service>
  <id>$SvcApp</id><name>$SvcApp</name><description>Native application ACL probe</description><startmode>Manual</startmode>
  <executable>$windowsPs</executable><arguments>-NoProfile -ExecutionPolicy Bypass -File &quot;$appCheck&quot; &quot;$fixture&quot;</arguments>
  <logpath>$Logs</logpath>
</service>
"@
try {
    Install-Svc $SvcDb $dbXml
    # Simulate an older installer stopping after creating the local fixture service under LocalSystem.
    $partial = Join-Path $Services $SvcApp
    New-Item -ItemType Directory -Path $partial | Out-Null
    $partialExe = Join-Path $partial "$SvcApp.exe"
    Copy-Item -LiteralPath $WinSw -Destination $partialExe
    Set-Content -LiteralPath (Join-Path $partial "$SvcApp.xml") -Value $appXml -Encoding UTF8
    if ((Invoke-Exe $partialExe @('install')) -ne 0) { throw 'Cannot create the stopped service resume fixture' }
    Install-Svc $SvcApp $appXml
    Install-Svc $SvcDb $dbXml
    Write-Host 'PASS repeat registration and stopped partial service repair'
    Set-IshAcls
    Assert-InstallTree
    Start-IshService $SvcDb 20
    Start-IshService $SvcApp 20
    $deadline = (Get-Date).AddSeconds(20)
    $pgReady = Join-Path $PgBin 'pg_isready.exe'
    do {
        Start-Sleep -Milliseconds 300
        & $pgReady -q -h 127.0.0.1 -p $port
        $ready = $LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $report)
        if ($ready) { break }
    } while ((Get-Date) -lt $deadline)
    $appSid = (Get-ServiceAccount $SvcApp).Translate([Security.Principal.SecurityIdentifier]).Value
    $db = Get-CimInstance Win32_Service -Filter "Name='$SvcDb'"
    if (-not $ready -or [IO.File]::ReadAllText($report) -ne "$appSid|3" -or $db.StartName -ne "NT SERVICE\$SvcDb") {
        throw 'Virtual service account ACL proof failed'
    }
    Write-Host 'PASS WinSW virtual accounts: database process starts, app reads config and writes logs; app cannot read superuser/PGDATA/backups'
    $psql = Join-Path $PgBin 'psql.exe'
    $secretText = Get-Content -LiteralPath $superFile -Encoding UTF8 -Raw
    $superPassword = [regex]::Match($secretText, '[A-Za-z0-9]{32}$').Value
    if (-not $superPassword) { throw 'Saved PostgreSQL credential was not published' }
    $env:PGPASSWORD = $superPassword
    $one = & $psql -X -w -h 127.0.0.1 -p $port -U postgres -d postgres -Atc 'select 1' 2>$null
    if ($LASTEXITCODE -ne 0 -or $one -ne '1') { throw 'PostgreSQL SCRAM accepted-password assertion failed' }
    $listen = & $psql -X -w -h 127.0.0.1 -p $port -U postgres -d postgres -Atc 'show listen_addresses' 2>$null
    if ($LASTEXITCODE -ne 0 -or $listen -ne '127.0.0.1') { throw 'PostgreSQL is not restricted to loopback' }
    $rolePassword = [guid]::NewGuid().ToString('N')
    if ((Invoke-InstallRoleSql $psql $port $rolePassword) -ne 0) { throw 'Installer stdin role creation failed' }
    $env:PGPASSWORD = $rolePassword
    $role = & $psql -X -w -h 127.0.0.1 -p $port -U ish_app -d postgres -Atc 'select rolsuper from pg_roles where rolname=current_user' 2>$null
    if ($LASTEXITCODE -ne 0 -or $role -ne 'f') { throw 'Application role was not authenticated as non-superuser' }
    $env:PGPASSWORD = [guid]::NewGuid().ToString('N')
    $ErrorActionPreference = 'Continue'
    & $psql -X -w -h 127.0.0.1 -p $port -U postgres -d postgres -Atc 'select 1' 2>$null | Out-Null
    $badPasswordExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($badPasswordExit -eq 0) { throw 'Wrong password was accepted' }
    Write-Host 'PASS real SCRAM accepts correct password, rejects wrong password, app role is not superuser'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    foreach ($id in $SvcApp, $SvcDb) {
        if (Get-Service -Name $id -ErrorAction SilentlyContinue) {
            Stop-IshService $id 20
            $wrapper = Join-Path $Services "$id\$id.exe"
            & $wrapper uninstall | Out-Host
            if ($LASTEXITCODE) { throw "Failed to unregister dedicated test service $id" }
        }
    }
}
$serverLog = @(Get-ChildItem -LiteralPath (Join-Path $PgData 'log') -File)
if (-not $serverLog.Count) { throw 'No PostgreSQL log was available to check' }
foreach ($log in $serverLog) {
    $contents = [IO.File]::ReadAllText($log.FullName)
    if ($contents.Contains($rolePassword) -or $contents.Contains($superPassword)) { throw 'A PostgreSQL log contains credential material' }
}
Write-Host 'PASS actual PostgreSQL loopback and no credentials in server logs'

$name = 'ishqa' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$pass = ConvertTo-SecureString ([guid]::NewGuid().ToString('N') + 'Qa7!') -AsPlainText -Force
$user = New-LocalUser -Name $name -Password $pass -Description 'Disposable ISH native ACL test identity'
try {
    Add-LocalGroupMember -SID (Get-Sid 'S-1-5-32-545') -Member $user
    $scratch = Join-Path $protectedParent 'standard-user-scratch'
    New-InstallPrivateDirectory $scratch
    $scratchAcl = Get-Acl -LiteralPath $scratch
    $scratchAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user.SID, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $scratch -AclObject $scratchAcl
    $psi = [Diagnostics.ProcessStartInfo]::new((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'), "-NoProfile -ExecutionPolicy Bypass -File `"$probe`" -ProbeOnly -Work `"$fixture`"")
    $psi.UserName = $name; $psi.Domain = $env:COMPUTERNAME; $psi.Password = $pass
    $psi.WorkingDirectory = $env:SystemRoot
    $psi.EnvironmentVariables['TEMP'] = $scratch
    $psi.EnvironmentVariables['TMP'] = $scratch
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($psi)
    try {
        $output = $process.StandardOutput.ReadToEndAsync(); $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Standard-user probe timed out' }
        Write-Host $output.Result
        if ($process.ExitCode -ne 0) { throw "Standard-user ACL probe failed: $($errors.Result)" }
    } finally { $process.Dispose() }
} finally { Remove-LocalUser -SID $user.SID }
Write-Host 'PASS independent standard Windows user denied read/write/delete/replace and permission changes'
