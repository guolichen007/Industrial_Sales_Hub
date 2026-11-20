# 测试专用：由当前安装包生成一个“下一版本”测试包（只改版本号并重算 SHA256SUMS），用于在测试机上验证 07-升级程序 的升级与回滚。
#   powershell -ExecutionPolicy Bypass -File tools\make-test-next-version.ps1 -Source D:\ISH-RC2-pkg -Dest D:\ISH-next -Version 2.1.0-rc3-test
# 生成的包**不是正式发布物**：VERSION.txt 带 TEST 标记；只能在测试机使用。
param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Dest, [string]$Version = '2.1.0-rc3-test')
$ErrorActionPreference = 'Stop'
$Source = (Resolve-Path -LiteralPath $Source).Path
if (Test-Path -LiteralPath $Dest) { throw "目标已存在：$Dest" }
if (-not (Test-Path -LiteralPath (Join-Path $Source 'SHA256SUMS.txt'))) { throw '源目录不是安装包（缺少 SHA256SUMS.txt）' }
foreach ($d in @('data', 'config', 'backups', 'logs')) { if (Test-Path -LiteralPath (Join-Path $Source $d)) { throw "源目录是已安装目录（含 $d），请用解压后的原始安装包" } }
Copy-Item -LiteralPath $Source -Destination $Dest -Recurse
$settings = Join-Path $Dest 'app\backend\config\settings.py'
$t = [IO.File]::ReadAllText($settings)
$t = [Text.RegularExpressions.Regex]::Replace($t, '(?m)^APP_VERSION = ".*"$', "APP_VERSION = `"$Version`"")
[IO.File]::WriteAllText($settings, $t, (New-Object Text.UTF8Encoding($false)))
Set-Content -LiteralPath (Join-Path $Dest 'VERSION.txt') -Value "$Version (TEST 升级演练包，非正式发布)" -Encoding UTF8
$lines = Get-ChildItem -LiteralPath $Dest -Recurse -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | ForEach-Object {
    $rel = $_.FullName.Substring($Dest.Length).TrimStart([char[]]@([char]92, [char]47)).Replace([string][char]92, '/')
    '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower(), $rel
} | Sort-Object { $_.Substring(66) }
[IO.File]::WriteAllLines((Join-Path $Dest 'SHA256SUMS.txt'), [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
Write-Host "已生成测试升级包：$Dest（版本 $Version，$($lines.Count) 个文件）" -ForegroundColor Green
