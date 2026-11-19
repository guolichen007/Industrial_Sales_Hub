# 卸载服务与防火墙规则。默认**不删除** data、config、backups；删除数据需单独显式确认。
param([switch]$DeleteData)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'uninstall'
Assert-Admin '删除 Windows 服务'
$ans = Read-Host '将停止并删除系统服务（数据保留）。输入 Y 继续'
if ($ans -notin @('Y', 'y')) { exit 0 }
foreach ($svc in @($SvcApp, $SvcDb)) {
    $exe = Join-Path $Services "$svc\$svc.exe"
    if ((Get-ServiceState $svc) -ne 'NotInstalled') {
        if ((Get-ServiceState $svc) -eq 'Running') { Stop-Service -Name $svc }
        $null = Invoke-Exe $exe @('uninstall')
        Write-Ok "已删除服务 $svc"
    }
}
Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Unregister-ScheduledTask -TaskName 'IndustrialSalesHub-DailyBackup' -Confirm:$false -ErrorAction SilentlyContinue
Write-Ok '防火墙规则与计划任务已删除'
if ($DeleteData) {
    $p = Read-Host "确定永久删除全部业务数据与备份？请输入 DELETE-ALL-DATA"
    if ($p -eq 'DELETE-ALL-DATA') { foreach ($d in @($Data, $Config, $Backups)) { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }; Write-Ok '数据已删除' }
    else { Write-Host '未删除数据' }
} else { Write-Host "数据保留在：$Data、$Config、$Backups" }
