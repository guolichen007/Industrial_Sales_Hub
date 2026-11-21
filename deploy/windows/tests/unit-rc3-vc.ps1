# RC3-G5 T19：从 install.ps1 截取“运行库”一节原文，用桩函数执行，验证 3010 → 保存续装状态并退出 3010；未重启再运行 → 仍退出 3010；重启后 → 继续
$ErrorActionPreference = 'Stop'
$fail = 0
function Check([string]$name, [bool]$ok) { if ($ok) { Write-Host "PASS $name" } else { Write-Host "FAIL $name"; $script:fail++ } }
$src = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot '..\scripts\install.ps1')
$start = $src.IndexOf('# ---------- 2. 运行库 ----------'); $end = $src.IndexOf('# ---------- 3. 目录与配置 ----------')
if ($start -lt 0 -or $end -lt 0) { throw '找不到运行库一节' }
$block = $src.Substring($start, $end - $start)
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('ishvc-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $tmp | Out-Null

function Run([int]$vcExit, [string]$bootTime) {
    $script:Data = Join-Path $tmp 'data'; New-Item -ItemType Directory -Force $script:Data | Out-Null
    $script:Runtime = Join-Path $tmp 'runtime'; New-Item -ItemType Directory -Force $script:Runtime | Out-Null
    Set-Content (Join-Path $script:Runtime 'vc_redist.x64.exe') 'x'
    $env:ISH_SIM_VC_EXIT = "$vcExit"; $env:ISH_SIM_BOOT_TIME = $bootTime
    $code = @'
function Write-Step($m) {} ; function Write-Ok($m) { "OK $m" } ; function Write-Warn2($m) { "WARN $m" } ; function Fail($m, $c) { "FAIL $m"; exit $c }
function Test-SimPlatform { $true } ; function Exit-OpsLock {}
function Test-Path { param([Parameter(Position=0)]$Path, $LiteralPath) if ($LiteralPath) { Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath } elseif ("$Path" -like 'HKLM:*') { $false } else { Microsoft.PowerShell.Management\Test-Path $Path } }
function Set-InstallState([string]$phase) { Set-Content -LiteralPath (Join-Path $Data 'install-state.json') -Value (@{ phase = $phase } | ConvertTo-Json -Compress) }
function Get-InstallState { $f = Join-Path $Data 'install-state.json'; if (Microsoft.PowerShell.Management\Test-Path -LiteralPath $f) { (Get-Content -Raw $f | ConvertFrom-Json).phase } }
'@ + "`n`$Data = '$($script:Data)'; `$Runtime = '$($script:Runtime)'`n" + $block + "`n'CONTINUED'"
    $f = Join-Path $tmp 'run.ps1'; Set-Content -LiteralPath $f -Value $code -Encoding UTF8
    $out = & (Get-Process -Id $PID).Path -NoProfile -File $f 2>&1 | Out-String
    return @($LASTEXITCODE, $out)
}
# Linux 进程退出码只有 8 位（3010 → 194）；Windows 保留完整 3010。按平台比较
$RebootCode = if ([Environment]::OSVersion.Platform -eq 'Unix') { 3010 % 256 } else { 3010 }
$r = Run 3010 "2000-01-01"
Check 'T19 VC++ 返回 3010 → 退出码 3010、不继续初始化数据库' ($r[0] -eq $RebootCode -and $r[1] -notmatch 'CONTINUED')
Check 'T19 保存续装状态 vc-reboot-required' ((Get-Content -Raw (Join-Path $tmp 'data\install-state.json')) -match 'vc-reboot-required')
$r = Run 0 '2000-01-01'
Check 'T19 未重启再次运行 → 仍提示重启（3010）' ($r[0] -eq $RebootCode -and $r[1] -match '尚未重启')
$r = Run 0 (Get-Date).AddMinutes(5).ToString('s')
Check 'T19 重启后运行 → 继续安装' ($r[1] -match 'CONTINUED' -and $r[1] -match '重启后继续')
Remove-Item env:ISH_SIM_VC_EXIT, env:ISH_SIM_BOOT_TIME -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $tmp
Write-Host "unit-rc3-vc fail=$fail"
if ($fail) { exit 1 }
