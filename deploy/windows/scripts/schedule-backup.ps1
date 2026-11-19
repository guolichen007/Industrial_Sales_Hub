# 配置每日自动备份计划任务（以 SYSTEM 运行，不依赖任何人登录）
param([string]$At = '02:00')
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'schedule'
Assert-Admin '创建计划任务'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$act = New-ScheduledTaskAction -Execute $ps -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $PSScriptRoot 'backup.ps1'))
$trg = New-ScheduledTaskTrigger -Daily -At $At
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName 'IndustrialSalesHub-DailyBackup' -Action $act -Trigger $trg -Settings $set -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Write-Ok "已配置每日 $At 自动备份（错过时开机后补做）"
