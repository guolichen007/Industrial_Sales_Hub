# Run the actual restore.ps1 and common.ps1 in a disposable install tree.
# Stub the Windows service, manage.py and HTTP boundaries only.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$scripts = Join-Path $PSScriptRoot '../scripts'
$root = Join-Path ([IO.Path]::GetTempPath()) ('ish-f4-' + [guid]::NewGuid().ToString('N'))
$fakePlatform = @'
function Test-SimPlatform { return $true }
function Get-ServiceState([string]$name) {
    Get-Content -LiteralPath (Join-Path $script:Data 'service.state')
}
function Stop-IshService([string]$name) {
    Add-Content -LiteralPath (Join-Path $script:Data 'calls') 'stop'
    Set-Content -LiteralPath (Join-Path $script:Data 'service.state') 'Stopped'
}
function Start-IshService([string]$name) {
    Add-Content -LiteralPath (Join-Path $script:Data 'calls') 'start'
    if ($env:ISH_F4_CASE -eq 'start-error') { throw 'injected Start-IshService failure' }
    if ($env:ISH_F4_CASE -ne 'service-stopped') {
        Set-Content -LiteralPath (Join-Path $script:Data 'service.state') 'Running'
    }
}
function Invoke-Manage([string[]]$ManageArgs, [switch]$AllowFail) {
    if ($ManageArgs[0] -eq 'ish_verify_backup') { return 0 }
    if (-not $env:ISH_OPS_TOKEN) { throw 'Missing operations lock token' }
    Add-Content -LiteralPath (Join-Path $script:Data 'calls') "manage $($ManageArgs[0])"
    New-Item -ItemType Directory -Path $script:OpsDir -Force | Out-Null
    Write-AtomicText (Join-Path $script:OpsDir 'restore-f4.json') '{"id":"f4","state":"FAILED_PRECHECK"}'
    return 1
}
function Wait-Http([string]$url, [int]$seconds) {
    if ($url -ne 'http://127.0.0.1:18100/healthz') { throw "Unexpected health URL: $url" }
    Add-Content -LiteralPath (Join-Path $script:Data 'calls') 'healthz'
    switch ($env:ISH_F4_CASE) {
        'http-503' { return $null }
        'maintenance-true' { return '{"status":"ok","maintenance":true}' }
        default { return '{"status":"ok","maintenance":false}' }
    }
}
'@
$fail = 0
try {
    foreach ($scenario in 'old-stopped', 'old-running', 'start-error', 'service-stopped', 'http-503', 'maintenance-true', 'healthy') {
        $env:ISH_F4_CASE = $scenario
        $install = Join-Path $root $scenario
        foreach ($name in 'scripts', 'data', 'config', 'backup') {
            New-Item -ItemType Directory -Path (Join-Path $install $name) -Force | Out-Null
        }
        $data = Join-Path $install 'data'
        $appEnv = Join-Path $install 'config/app.env'
        $marker = Join-Path $data 'MAINTENANCE'
        $payload = Join-Path $data 'payload.bin'
        $stateFile = Join-Path $data 'service.state'
        $callFile = Join-Path $data 'calls'
        Set-Content -LiteralPath $appEnv 'ISH_LISTEN=127.0.0.1:18100'
        Set-Content -LiteralPath $callFile -Value '' -NoNewline
        [IO.File]::WriteAllBytes($payload, [byte[]](0, 17, 129, 255))
        $before = (Get-FileHash -LiteralPath $payload).Hash
        $initial = if ($scenario -eq 'old-stopped') { 'Stopped' } else { 'Running' }
        Set-Content -LiteralPath $stateFile $initial
        $old = $scenario -like 'old-*'
        if ($old) {
            [IO.File]::WriteAllText($marker, "{`"token`":`"old-token`",`"task`":`"upgrade`"}`r`n", [Text.Encoding]::ASCII)
        }
        $markerBefore = if ($old) { (Get-FileHash -LiteralPath $marker).Hash } else { '-' }
        $common = Get-Content -LiteralPath (Join-Path $scripts 'common.ps1') -Raw -Encoding UTF8
        [IO.File]::WriteAllText((Join-Path $install 'scripts/common.ps1'), "$common`r`n$fakePlatform", [Text.Encoding]::UTF8)
        Copy-Item -LiteralPath (Join-Path $scripts 'restore.ps1') -Destination (Join-Path $install 'scripts/restore.ps1')
        $exe = (Get-Process -Id $PID).Path
        $output = & $exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $install 'scripts/restore.ps1') -BackupPath (Join-Path $install 'backup') -Production -Yes | Out-String
        $rc = $LASTEXITCODE
        $actual = Get-Content -LiteralPath $stateFile
        $calls = @(Get-Content -LiteralPath $callFile) -join ','
        $after = (Get-FileHash -LiteralPath $payload).Hash
        $markerAfter = if (Test-Path -LiteralPath $marker) { (Get-FileHash -LiteralPath $marker).Hash } else { '-' }
        $owner = Get-Content -LiteralPath (Join-Path $data 'ops.owner.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $ok = $before -eq $after -and $markerBefore -eq $markerAfter -and -not $owner.token
        $reportedRecovered = $output.Contains('正式数据保持恢复前状态，已恢复应用服务。')
        if ($old) {
            $ok = $ok -and $rc -eq 42 -and $actual -eq $initial -and $calls -eq '' -and -not $reportedRecovered
        } elseif ($scenario -eq 'healthy') {
            $ok = $ok -and $rc -eq 40 -and $actual -eq 'Running' -and $calls -eq 'stop,manage ish_restore,start,healthz' -and $reportedRecovered
        } else {
            $expectedCalls = if ($scenario -in 'start-error', 'service-stopped') { 'stop,manage ish_restore,start,stop' } else { 'stop,manage ish_restore,start,healthz,stop' }
            $ok = $ok -and $rc -eq 42 -and $actual -eq 'Stopped' -and $calls -eq $expectedCalls -and -not $reportedRecovered
        }
        if (-not $ok) { $fail++ }
        $result = if ($ok) { 'PASS' } else { 'FAIL' }
        Write-Host "$result $scenario rc=$rc service=$initial->$actual calls=[$calls] reportedRecovered=$reportedRecovered data=$before/$after marker=$markerBefore/$markerAfter lockReleased=$(-not $owner.token)"
        Write-Host $output
    }
} finally {
    Remove-Item Env:ISH_F4_CASE -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $root) {
        $resolved = (Resolve-Path -LiteralPath $root).ProviderPath
        if ($resolved -ne $root) { throw "Unexpected test cleanup path: $resolved" }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
Write-Host "unit-f4-restore-wrapper fail=$fail"
if ($fail) { exit 1 }
