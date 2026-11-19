# 03-停止系统：仅停止本系统的服务（按服务名，不会结束其他程序的同名进程）
param([switch]$IncludeDatabase)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'stop'
Assert-Admin '停止 Windows 服务'
$ans = Read-Host '停止后局域网内所有用户将无法使用系统。确认停止？输入 Y 继续'
if ($ans -notin @('Y', 'y')) { Write-Host '已取消'; exit 0 }
if ((Get-ServiceState $SvcApp) -eq 'Running') { Stop-Service -Name $SvcApp; Write-Ok '应用已停止' } else { Write-Ok '应用未运行' }
if ($IncludeDatabase) {
    if ((Get-ServiceState $SvcDb) -eq 'Running') { Stop-Service -Name $SvcDb; Write-Ok '数据库已停止' }
} else { Write-Host '数据库保持运行（如需同时停止：03-停止系统.bat -IncludeDatabase）' }
