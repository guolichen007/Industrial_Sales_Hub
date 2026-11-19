# 04-立即备份：一致性备份（维护窗口约数秒到数分钟，期间只读）
param([string]$Dest = '', [string]$CopyTo = '')
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'backup'
if (-not (Test-Path -LiteralPath $AppEnv)) { Fail '尚未安装' 30 }
$cfg = Read-AppEnv
if (-not $Dest) { $Dest = $cfg['ISH_BACKUP_DIR'] }
if (-not $CopyTo) { $CopyTo = $cfg['ISH_BACKUP_COPY_TO'] }
$args2 = @('ish_backup', '--dest', $Dest, '--keep-days', $cfg['ISH_BACKUP_KEEP_DAYS'], '--pg-bin', $PgBin)
if ($CopyTo) { $args2 += @('--copy-to', $CopyTo) } else { Write-Warn2 '未配置异盘/异机副本（config\app.env 的 ISH_BACKUP_COPY_TO）。仅本机备份无法抵御硬盘损坏。' }
Invoke-Manage $args2 | Out-Null
Write-Ok "备份完成，目录：$Dest"
