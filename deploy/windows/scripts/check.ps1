# 06-检查系统：版本、服务、端口、数据库、健康、备份、磁盘、日志、局域网地址（只读，不修改任何东西）
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'check'
Assert-CanReadConfig
$ok = $true
$ver = if (Test-Path -LiteralPath (Join-Path $Root 'VERSION.txt')) { Get-Content -LiteralPath (Join-Path $Root 'VERSION.txt') -Raw } else { '未知' }
Write-Host "版本：$ver"
Write-Host "安装目录：$Root"
if (-not (Test-Path -LiteralPath $AppEnv)) { Write-Warn2 '尚未安装（无 config\app.env）'; exit 2 }
$cfg = Read-AppEnv
$port = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
foreach ($svc in @($SvcDb, $SvcApp)) {
    $st = Get-ServiceState $svc
    $s = Get-CimInstance Win32_Service -Filter "Name='$svc'" -ErrorAction SilentlyContinue
    $acct = if ($s) { $s.StartName } else { '-' }
    $mode = if ($s) { $s.StartMode } else { '-' }
    if ($st -eq 'Running') { Write-Ok "$svc：$st（启动类型 $mode，账户 $acct）" } else { Write-Warn2 "$svc：$st"; $ok = $false }
}
$dbPort = [int]$cfg['ISH_DB_PORT']
$l = Get-NetTCPConnection -State Listen -LocalPort $dbPort -ErrorAction SilentlyContinue
if ($l) {
    $addrs = ($l | Select-Object -ExpandProperty LocalAddress -Unique) -join ','
    if ($addrs -match '0\.0\.0\.0|::$') { Write-Warn2 "数据库监听在 $addrs（应仅 127.0.0.1）"; $ok = $false } else { Write-Ok "数据库仅本机监听：$addrs`:$dbPort" }
}
$h = Wait-Http "http://127.0.0.1:$port/healthz" 10
if ($h) { Write-Ok "健康检查：$h" } else { Write-Warn2 '健康检查失败'; $ok = $false }
if ($cfg['ISH_HTTPS'] -ne '1') { Write-Warn2 'HTTP 隔离内网试用模式（未加密传输）' }
$bs = Join-Path $Data 'backup_status.json'
if (Test-Path -LiteralPath $bs) {
    $b = Get-Content -LiteralPath $bs -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($b.ok) {
        $age = (New-TimeSpan -Start ([datetime]$b.at) -End (Get-Date)).TotalHours
        if ($age -gt 26) { Write-Warn2 ("最近一次成功备份在 {0:N0} 小时前：{1}" -f $age, $b.path); $ok = $false } else { Write-Ok ("最近备份：{0}（{1} MB）" -f $b.at, $b.size_mb) }
        if (-not $b.copy) { Write-Warn2 '未配置异盘/异机备份副本' }
    } else { Write-Warn2 "最近一次备份失败：$($b.error)"; $ok = $false }
} else { Write-Warn2 '尚无备份记录'; $ok = $false }
$task = Get-ScheduledTask -TaskName 'IndustrialSalesHub-DailyBackup' -ErrorAction SilentlyContinue
if ($task) { Write-Ok "每日备份计划任务：$($task.State)" } else { Write-Warn2 '未配置每日备份计划任务（以管理员身份运行 08-配置每日备份.bat）'; $ok = $false }
$drive = (Get-Item -LiteralPath $Root).PSDrive
Write-Host ("磁盘 {0}: 剩余 {1:N1} GB" -f $drive.Name, ($drive.Free / 1GB))
$mo = Get-MaintenanceOwner
if ($mo) {
    Write-Warn2 ("系统处于维护（只读）状态：{0}（{1}）" -f $mo.task, $mo.at); $ok = $false
    $owner = $null; try { $owner = Get-Content -LiteralPath (Join-Path $Data 'ops.owner.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $owner = $null }
    if ($owner -and $owner.token) { Write-Host "    当前运维任务：$($owner.task)，开始于 $($owner.started_at)，进程 $($owner.pid)（任务结束后自动解除）" }
    else {
        Write-Host '    没有正在运行的运维任务：可能是上次升级/恢复失败后保持的只读状态。按以下顺序处理，不要直接删除 data\MAINTENANCE：' -ForegroundColor Yellow
        Get-ChildItem -LiteralPath $OpsDir -Filter '*.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 3 | ForEach-Object {
            $j = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Host ("      {0}：状态 {1}，阶段 {2}" -f $_.Name, $j.state, $j.phase)
        }
        Write-Host '      升级未完成 → 07-升级程序.bat -Rollback <ID>；恢复未完成 → 05-恢复备份.bat -Rollback <ID>（见备份恢复与升级手册第 6 节）'
    }
}
$st = Join-Path $Data 'install-state.json'
if (Test-Path -LiteralPath $st) { $is = Get-Content -LiteralPath $st -Raw -Encoding UTF8 | ConvertFrom-Json; if ($is.phase -ne 'done') { Write-Warn2 "安装未完成（阶段 $($is.phase)）：请重新运行 01-安装部署.bat"; $ok = $false } }
$fw = Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' -ErrorAction SilentlyContinue
if ($fw) { Write-Ok ("防火墙规则：{0}（{1}）" -f $fw.DisplayName, ($fw.Profile -join ',')) } else { Write-Warn2 '未找到防火墙规则，局域网其他电脑可能无法访问' }
$scheme = if ($cfg['ISH_HTTPS'] -eq '1') { 'https' } else { 'http' }
Write-Host '局域网地址：'; Get-LanAddresses | ForEach-Object { Write-Host "  ${scheme}://${_}:$port/" }
$errs = Get-ChildItem -LiteralPath $Logs -Filter 'app.log' -ErrorAction SilentlyContinue | ForEach-Object { Select-String -LiteralPath $_.FullName -Pattern ' ERROR ' | Select-Object -Last 5 }
if ($errs) { Write-Warn2 '应用日志最近的错误：'; $errs | ForEach-Object { Write-Host "    $($_.Line)" } }
if ($ok) { Write-Host "`n检查结论：正常" -ForegroundColor Green; exit 0 } else { Write-Host "`n检查结论：存在需要处理的项目（见上）" -ForegroundColor Yellow; exit 1 }
