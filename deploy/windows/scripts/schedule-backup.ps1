# 配置每日自动备份计划任务（以 SYSTEM 运行，不依赖任何人登录）
param([string]$At = '02:00')
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'schedule'
Assert-Admin '创建计划任务'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$act = New-ScheduledTaskAction -Execute $ps -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Scheduled' -f (Join-Path $PSScriptRoot 'backup.ps1'))
$trg = New-ScheduledTaskTrigger -Daily -At $At
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)
$set.MultipleInstances = 'IgnoreNew'   # 上一次仍在运行时不再并行启动
Register-ScheduledTask -TaskName $BackupTask -Action $act -Trigger $trg -Settings $set -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Write-Ok '其他运维任务进行中时，计划备份最多等待 30 分钟；仍忙则安全跳过（退出码 75，不覆盖上次备份状态）'
Write-Ok "已配置每日 $At 自动备份（错过时开机后补做）"
