# 05-恢复备份（RC2）
#   默认（演练）：校验备份 → 恢复到临时数据库与临时目录 → 完整性检查 → 自动清理。不影响正式数据。
#   -Production：正式恢复。停止应用 → manage.py ish_restore（staging 库 + staging 文件验证 → 改名切换 → 切换后校验，
#                任何阶段失败自动回退）→ 目录权限 → 启动并探活；权限或启动失败时按恢复日志撤销切换。
#   -Rollback <恢复ID>：撤销一次已完成的正式恢复（回到恢复前的数据库与文件）。
#   -Cleanup <恢复ID>：确认恢复结果无误后，删除保留的旧数据库与旧文件。
param([string]$BackupPath = '', [switch]$Production, [string]$Rollback = '', [string]$Cleanup = '', [switch]$Yes)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'restore'
Assert-CanReadConfig
if (-not (Test-Path -LiteralPath $AppEnv)) { Fail '尚未安装' 30 }
$cfg = Read-AppEnv
$bdest = if ($cfg['ISH_BACKUP_DIR']) { $cfg['ISH_BACKUP_DIR'] } else { $Backups }

if ($Cleanup) {
    $rc = Invoke-Manage @('ish_restore', '--cleanup', $Cleanup, '--pg-bin', $PgBin) -AllowFail
    exit $rc
}
if ($Rollback) {
    if (-not (Test-SimPlatform)) { Assert-Admin '停止/启动服务并撤销恢复' }
    if (-not $Yes) { if ((Read-Host "将撤销恢复 $Rollback，回到恢复前的数据。请输入「确认撤销」") -ne '确认撤销') { Write-Host '已取消'; exit 0 } }
    Stop-IshService $SvcApp
    $rc = Invoke-Manage @('ish_restore', '--rollback', $Rollback, '--pg-bin', $PgBin) -AllowFail
    if ($rc -eq 0) { Set-IshAcls; Start-IshService $SvcApp; Write-Ok '已撤销恢复' }
    exit $rc
}
if (-not $BackupPath) { Fail '请用 -BackupPath 指定备份目录' 30 }
$BackupPath = (Resolve-Path -LiteralPath $BackupPath).Path
Write-Step '校验备份'
Invoke-Manage @('ish_verify_backup', $BackupPath) | Out-Null

if (-not $Production) {
    Write-Step '恢复演练（临时库 + 临时目录，不影响正式数据）'
    $stamp = Get-Date -Format 'yyyyMMddHHmmss'
    $tmpDir = New-OpsTempDir "drill-$stamp"
    Invoke-Manage @('ish_verify_backup', $BackupPath, '--restore-to-db', ('ish_drill_' + $stamp), '--restore-data-dir', $tmpDir, '--pg-bin', $PgBin) | Out-Null
    Write-Ok '恢复演练通过；正式数据未被修改。'
    exit 0
}

if (-not (Test-SimPlatform)) { Assert-Admin '停止/启动服务并覆盖正式数据' }
Write-Host "将用以下备份替换当前全部业务数据：`n  $BackupPath" -ForegroundColor Yellow
Write-Host '恢复前会自动备份当前状态；旧数据库与旧文件在确认前保留，可用 -Rollback 撤销。' -ForegroundColor Yellow
if (-not $Yes) {
    if ((Read-Host '请输入「确认恢复」四个字继续') -ne '确认恢复') { Write-Host '已取消'; exit 0 }
}
$tok, $busy = Enter-OpsLock 'restore' 0
if (-not $tok) { Fail $busy 75 }
$exitCode = 0
try {
    $maintenance = Join-Path $Data 'MAINTENANCE'
    if (Test-Path -LiteralPath $maintenance) {
        Fail '检测到遗留维护标记，已拒绝发起新恢复并保留原应用状态。请运行 06-检查系统 并按《备份恢复与升级手册》第 6 节处理。' 42
    }
    $before = @(Get-ChildItem -LiteralPath $OpsDir -Filter 'restore-*.json' -ErrorAction SilentlyContinue | ForEach-Object Name)
    Write-Step '停止应用（数据库保持运行）'
    Stop-IshService $SvcApp
    Write-Step '正式恢复：staging 验证 → 原子切换 → 切换后校验'
    $rc = Invoke-Manage @('ish_restore', $BackupPath, '--confirm-production', '--pg-bin', $PgBin, '--backup-dest', $bdest) -AllowFail
    $new = @(Get-ChildItem -LiteralPath $OpsDir -Filter 'restore-*.json' -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Name } | Sort-Object Name | Select-Object -Last 1)
    $rid = if ($new.Count) { (Get-Content -LiteralPath $new[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json).id } else { '' }
    if ($rc -ne 0) {
        # F-4 修复：由 Get-RestoreRecoveryDecision 统一判定“可否安全恢复应用服务”。
        # 前置拒绝（FAILED_PRECHECK，数据未改动）与已安全回退（ROLLED_BACK，数据回到恢复前）→ 安全，恢复应用服务；
        # 正在恢复（RUNNING）/回退失败（ROLLBACK_FAILED）/状态不确定 → 继续保持停止与保护。
        # 绝不无条件自动启动，也不手工删除 MAINTENANCE 标记（维护标记只能由同一 token 的恢复进程自行解除）。
        $state = ''
        if ($new.Count) { $state = (Get-Content -LiteralPath $new[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json).state }
        $decision = Get-RestoreRecoveryDecision -State $state -HasJournal ([bool]$new.Count)
        if ($decision -eq 'RESTART') {
            $why = switch ($state) {
                'FAILED_PRECHECK' { '预检查被拒绝，正式数据未改动' }
                'ROLLED_BACK' { '已自动回退到恢复前状态' }
                default { '恢复未执行（无恢复日志）' }
            }
            $exitCode = 42
            if (Test-Path -LiteralPath $maintenance) {
                Write-Warn2 "恢复未成功（$why；退出码 $rc，恢复 ID $rid）：检测到维护标记。应用保持停止，请按手册第 6 节处理。"
            } else {
                try {
                    Start-IshService $SvcApp
                    if ((Get-ServiceState $SvcApp) -ne 'Running') { throw '应用服务未处于 Running 状态' }
                    $port = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
                    $body = Wait-Http "http://127.0.0.1:$port/healthz" 120
                    if (-not $body) { throw 'healthz 未返回 HTTP 200' }
                    $health = $body | ConvertFrom-Json
                    if ($health.status -cne 'ok' -or $health.maintenance -isnot [bool] -or $health.maintenance) {
                        throw 'healthz 未确认应用处于正常且非维护状态'
                    }
                    if ((Test-Path -LiteralPath $maintenance) -or (Get-ServiceState $SvcApp) -ne 'Running') {
                        throw '应用服务或维护状态在探活期间发生变化'
                    }
                    Write-Warn2 "恢复未成功（$why；退出码 $rc，恢复 ID $rid）：正式数据保持恢复前状态，已恢复应用服务。原因见上方日志。"
                    $exitCode = 40
                } catch {
                    Write-Warn2 "恢复未成功（$why；退出码 $rc，恢复 ID $rid）：应用启动或探活失败：$($_.Exception.Message)"
                    try { Stop-IshService $SvcApp } catch { Write-Warn2 "无法确认应用已停止：$($_.Exception.Message)" }
                }
            }
        } else {
            Write-Warn2 "恢复失败且状态不确定或自动回退未完成（日志状态 $state，恢复 ID $rid）。应用保持停止；请勿改动 data 目录，按《备份恢复与升级手册》第 6 节处理：05-恢复备份.bat -Rollback $rid"
            $exitCode = 42
        }
    } else {
        $ok = $true
        try {
            Test-FaultInjection 'restore.ps1.acl'
            Set-IshAcls
            Start-IshService $SvcApp
            Test-FaultInjection 'restore.ps1.start'
            $port = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
            if (-not (Wait-Http "http://127.0.0.1:$port/healthz" 120)) { throw '应用未能在 120 秒内就绪' }
        } catch {
            $ok = $false
            Write-Warn2 "恢复后设置权限/启动失败：$($_.Exception.Message)；按恢复日志撤销切换"
            Stop-IshService $SvcApp
            $rc2 = Invoke-Manage @('ish_restore', '--rollback', $rid, '--pg-bin', $PgBin) -AllowFail
            if ($rc2 -eq 0) { Set-IshAcls; Start-IshService $SvcApp; Write-Warn2 '已撤销本次恢复，系统回到恢复前状态。' } else { Write-Warn2 "撤销失败，请勿手工改动数据目录，联系实施人员（恢复 ID $rid）。" }
            $exitCode = 41
        }
        if ($ok) {
            Write-Ok "恢复完成（恢复 ID $rid）。确认业务数据无误后，可运行：05-恢复备份.bat -Cleanup $rid 删除保留的旧数据"
        }
    }
} finally {
    Exit-OpsLock
}
exit $exitCode
