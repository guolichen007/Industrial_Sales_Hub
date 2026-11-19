# 07-升级程序：用新版本安装包中的 app\ 替换当前 app\（data、config、backups 不动）
#   步骤：校验新包 → 停止应用 → 一致性预备份 → 保留旧 app → 复制新 app 与依赖 → 迁移 → 完整性校验 → 启动
#   迁移失败：系统保持只读维护模式，按提示用 05-恢复备份 回滚数据并恢复旧 app。
param([Parameter(Mandatory = $true)][string]$NewPackageDir)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'upgrade'
Assert-Admin '停止/启动服务、替换程序文件'
$NewPackageDir = (Resolve-Path -LiteralPath $NewPackageDir).Path
if ($NewPackageDir -eq $Root) { Fail '请把新版本安装包解压到另一个目录，再从那里指定路径。' 50 }
foreach ($p in @('app\backend\manage.py', 'runtime\python\python.exe', 'SHA256SUMS.txt', 'VERSION.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $NewPackageDir $p))) { Fail "新安装包不完整：缺少 $p" 51 }
}
$newVer = (Get-Content -LiteralPath (Join-Path $NewPackageDir 'VERSION.txt') -Raw).Trim()
$curVer = (Get-Content -LiteralPath (Join-Path $Root 'VERSION.txt') -Raw).Trim()
Write-Host "当前版本：$curVer`n新版本：$newVer"
$ans = Read-Host '升级期间系统暂停使用（通常数分钟）。输入 Y 继续'
if ($ans -notin @('Y', 'y')) { exit 0 }
$cfg = Read-AppEnv
Write-Step '停止应用'
Stop-Service -Name $SvcApp
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$oldApp = Join-Path $Root "app.old-$curVer-$stamp"
$oldRt = Join-Path $Root "runtime.old-$curVer-$stamp"
Write-Step '替换程序文件（保留旧版本以便回滚）'
Rename-Item -LiteralPath $App -NewName (Split-Path $oldApp -Leaf)
Copy-Item -LiteralPath (Join-Path $NewPackageDir 'app') -Destination $App -Recurse
Copy-Item -LiteralPath $Runtime -Destination $oldRt -Recurse
Copy-Item -LiteralPath (Join-Path $NewPackageDir 'runtime\python') -Destination (Join-Path $Runtime 'python.new') -Recurse
Remove-Item -LiteralPath (Join-Path $Runtime 'python') -Recurse -Force
Rename-Item -LiteralPath (Join-Path $Runtime 'python.new') -NewName 'python'
foreach ($f in @('VERSION.txt', 'SHA256SUMS.txt', 'licenses', 'scripts', '*.bat', '*.md')) {
    Get-ChildItem -Path (Join-Path $NewPackageDir $f) -ErrorAction SilentlyContinue | Copy-Item -Destination $Root -Recurse -Force
}
Write-Step '预备份 + 迁移 + 完整性校验'
$rc = Invoke-Manage @('ish_upgrade', '--backup-dest', $cfg['ISH_BACKUP_DIR'], '--pg-bin', $PgBin) -AllowFail
if ($rc -ne 0) {
    Write-Host "`n升级失败。系统保持只读维护模式。回滚步骤：" -ForegroundColor Red
    Write-Host "  1. 删除 $App，把 $oldApp 改名为 app；把 $oldRt 改名为 runtime"
    Write-Host "  2. 运行：05-恢复备份.bat -BackupPath <backups 中最新的 *-pre-upgrade 目录> -Production"
    Write-Host "  3. 删除 data\MAINTENANCE 后运行 02-启动系统.bat"
    Fail '升级失败（见日志）' 52
}
Start-Service -Name $SvcApp
$port = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
if (-not (Wait-Http "http://127.0.0.1:$port/healthz" 120)) { Fail '新版本启动失败（见 logs），请按上方回滚步骤处理' 53 }
Remove-Item -LiteralPath $oldRt -Recurse -Force -ErrorAction SilentlyContinue
Write-Ok "升级完成：$curVer → $newVer。旧程序保留在 $oldApp（确认运行正常一周后可删除）"
