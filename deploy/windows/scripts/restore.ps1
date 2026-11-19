# 05-恢复备份
#   默认（演练）：校验备份并恢复到临时数据库与临时目录，检查完整性后自动清理 —— 不影响正式数据。
#   -Production：用备份覆盖正式数据（停止应用 → 先备份当前状态 → 恢复 → 校验 → 启动）。需输入确认短语。
param([Parameter(Mandatory = $true)][string]$BackupPath, [switch]$Production)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'restore'
Assert-CanReadConfig
if (-not (Test-Path -LiteralPath $AppEnv)) { Fail '尚未安装' 30 }
$cfg = Read-AppEnv
$BackupPath = (Resolve-Path -LiteralPath $BackupPath).Path
Write-Step '校验备份'
Invoke-Manage @('ish_verify_backup', $BackupPath) | Out-Null
if (-not $Production) {
    Write-Step '恢复演练（临时库 + 临时目录）'
    $tmpDir = Join-Path $env:TEMP ("ish-restore-" + (Get-Date -Format 'yyyyMMddHHmmss'))
    Invoke-Manage @('ish_verify_backup', $BackupPath, '--restore-to-db', ('ish_drill_' + (Get-Date -Format 'yyyyMMddHHmmss')), '--restore-data-dir', $tmpDir, '--pg-bin', $PgBin) | Out-Null
    Write-Ok '恢复演练通过；正式数据未被修改。'
    exit 0
}
Assert-Admin '停止/启动服务并覆盖正式数据'
Write-Host "将用以下备份覆盖正式数据：`n  $BackupPath" -ForegroundColor Yellow
$phrase = Read-Host '此操作会替换当前全部业务数据。请输入「确认恢复」四个字继续'
if ($phrase -ne '确认恢复') { Write-Host '已取消'; exit 0 }
Write-Step '先备份当前状态（以便撤销本次恢复）'
Invoke-Manage @('ish_backup', '--dest', $cfg['ISH_BACKUP_DIR'], '--pg-bin', $PgBin, '--label', 'before-restore') | Out-Null
Write-Step '停止应用'
Stop-Service -Name $SvcApp
$db = [Uri]($cfg['DATABASE_URL'] -replace '^postgres:', 'http:')
$env:PGPASSWORD = [Uri]::UnescapeDataString($db.UserInfo.Split(':', 2)[1])
try {
    $port = $db.Port
    Write-Step '恢复数据库（单事务，失败则不改变任何数据）'
    $rc = Invoke-Exe (Join-Path $PgBin 'pg_restore.exe') @('-h', '127.0.0.1', '-p', "$port", '-U', 'ish_app', '-d', 'ish', '--clean', '--if-exists', '--no-owner', '--no-privileges', '--single-transaction', '--exit-on-error', (Join-Path $BackupPath 'db.dump'))
    if ($rc -ne 0) { Start-Service -Name $SvcApp; Fail 'pg_restore 失败，数据库未改变；应用已重新启动。' 40 }
} finally { Remove-Item env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Step '恢复私有文件（报价 PDF、案例、附件）'
$priv = Join-Path $Data 'private'
$old = "$priv.old-" + (Get-Date -Format 'yyyyMMddHHmmss')
Rename-Item -LiteralPath $priv -NewName (Split-Path $old -Leaf)
Copy-Item -LiteralPath (Join-Path $BackupPath 'private') -Destination $priv -Recurse
Protect-Directory $priv ('NT SERVICE\' + $SvcApp) 'Modify'
Write-Step '完整性校验'
$rc = Invoke-Manage @('verify_integrity', '--expect-manifest', (Join-Path $BackupPath 'manifest.json')) -AllowFail
if ($rc -ne 0) { Fail "恢复后完整性校验失败。原私有文件保存在 $old；可用 before-restore 备份再次恢复。" 41 }
Start-Service -Name $SvcApp
Write-Ok "恢复完成。原私有文件已保留在：$old（确认无误后可手工删除）"
