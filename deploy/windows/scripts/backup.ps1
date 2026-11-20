# 04-立即备份 / 计划任务备份：一致性备份（维护窗口通常数秒到数分钟，期间业务只读）
#   -Scheduled：计划任务调用。其他运维任务（恢复/升级/另一个备份）进行中时最多等待 30 分钟，仍忙则安全退出（退出码 75，
#               不覆盖上一次备份状态，不删除别人的维护标记）。
param([string]$Dest = '', [string]$CopyTo = '', [switch]$Scheduled)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'backup'
Assert-CanReadConfig
if (-not (Test-Path -LiteralPath $AppEnv)) { Fail '尚未安装' 30 }
$cfg = Read-AppEnv
if (-not $Dest) { $Dest = if ($cfg['ISH_BACKUP_DIR']) { $cfg['ISH_BACKUP_DIR'] } else { $Backups } }
if (-not $CopyTo) { $CopyTo = $cfg['ISH_BACKUP_COPY_TO'] }
$wait = if ($Scheduled) { 1800 } else { 0 }
$args2 = @('ish_backup', '--dest', $Dest, '--keep-days', $(if ($cfg['ISH_BACKUP_KEEP_DAYS']) { $cfg['ISH_BACKUP_KEEP_DAYS'] } else { '30' }), '--pg-bin', $PgBin, '--lock-wait', "$wait")
if ($CopyTo) { $args2 += @('--copy-to', $CopyTo) } else { Write-Warn2 '未配置异盘/异机副本（config\app.env 的 ISH_BACKUP_COPY_TO）。仅本机备份无法抵御硬盘损坏。' }
$rc = Invoke-Manage $args2 -AllowFail
if ($rc -eq 75) { Write-Warn2 '其他运维任务正在进行，本次备份已安全跳过（上一次备份状态不变）。'; exit 75 }
if ($rc -ne 0) { Fail "备份失败（退出码 $rc），上一个成功备份保留不变" 31 }
Write-Ok "备份完成，目录：$Dest"
