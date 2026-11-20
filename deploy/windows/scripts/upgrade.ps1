# 07-升级程序（RC2 独立升级器）：**从新版本安装包目录运行**，升级指定的安装目录。
#
#   新包目录\07-升级程序.bat -InstallDir "D:\IndustrialSalesHub"            升级
#   新包目录\07-升级程序.bat -InstallDir "D:\IndustrialSalesHub" -Repair    同版本修复（重新部署发行文件）
#   安装目录\07-升级程序.bat -Rollback <升级ID>                              按日志回滚（中断/断电后，或升级后发现问题）
#   安装目录\07-升级程序.bat -Cleanup <升级ID>                               确认新版本运行正常后删除保留的旧版本与数据库快照
#   安装目录\07-升级程序.bat -Status                                         列出升级日志
#
# 顺序（日志 data\ops\upgrade-<ID>.json 逐步记录；任何一步失败都按日志逆序回滚）：
#   PRECHECK   管理员、路径安全、新包 SHA256 全量校验、版本（禁止降级）、PostgreSQL 大版本一致、磁盘空间
#   LOCK       全局运维锁 + 维护标记（token）；停止应用
#   BACKUP     **旧程序、旧运行时**生成 pre-upgrade 一致性备份 → 旧程序校验 + 恢复演练（临时库）
#   SNAPSHOT   数据库整库快照 CREATE DATABASE <db>_preupg_<ID> TEMPLATE <db>（回滚用，无需 pg_restore）
#   STAGE      新发行文件复制到 安装目录\_upgrade\<ID>\new 并再次全量校验（与安装目录同一磁盘卷）
#   SWITCH     停止数据库 → 旧发行文件整套移到 _upgrade\<ID>\old → 新发行文件移入 → 安装目录按新清单逐文件校验
#   MIGRATE    启动数据库 → **新程序** ish_upgrade --pre-backup <ID 对应备份>（迁移 + 完整性校验含历史 PDF 哈希）
#   VERIFY     启动应用 → /healthz 版本号 = 新版本
#   DONE       解除自己的维护标记；旧发行文件与数据库快照保留，待 -Cleanup
# data、config、backups、logs、services 永不移动。
param(
    [string]$InstallDir = '',
    [string]$NewPackageDir = '',      # 兼容旧调用方式：提示改为从新包运行
    [switch]$Repair,
    [string]$Rollback = '',
    [string]$Cleanup = '',
    [switch]$Status,
    [switch]$Yes                      # 无人值守（自动化验收）：跳过交互确认
)
. (Join-Path $PSScriptRoot 'common.ps1')
$PkgRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')

if ($NewPackageDir -and -not $InstallDir) {
    Write-Host "RC2 起升级器必须从新版本安装包目录运行（避免替换正在运行的脚本）。请执行：" -ForegroundColor Yellow
    Write-Host "  `"$NewPackageDir\07-升级程序.bat`" -InstallDir `"$PkgRoot`"" -ForegroundColor Yellow
    exit 2
}
if (-not $InstallDir) { $InstallDir = $PkgRoot }   # -Rollback/-Cleanup/-Status 在安装目录直接运行
# 在安装目录内执行 -Rollback/-Cleanup：回滚会移动本脚本所在的 scripts 目录，因此先把升级器复制到临时目录再运行
if (($Rollback -or $Cleanup) -and -not $env:ISH_UPGRADER_RELAUNCHED -and ((Resolve-Path -LiteralPath $InstallDir).Path.TrimEnd('\') -eq $PkgRoot)) {
    $tmpRun = Join-Path ([IO.Path]::GetTempPath()) ('ish-upgrader-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmpRun | Out-Null
    Copy-Item -LiteralPath $PSScriptRoot -Destination (Join-Path $tmpRun 'scripts') -Recurse
    $env:ISH_UPGRADER_RELAUNCHED = '1'
    $pass = @('-InstallDir', $PkgRoot)
    if ($Rollback) { $pass += @('-Rollback', $Rollback) }
    if ($Cleanup) { $pass += @('-Cleanup', $Cleanup) }
    if ($Yes) { $pass += '-Yes' }
    & (Get-Process -Id $PID).Path -NoProfile -ExecutionPolicy Bypass -File (Join-Path $tmpRun 'scripts\upgrade.ps1') @pass
    $rcRelaunch = $LASTEXITCODE
    Remove-Item -LiteralPath $tmpRun -Recurse -Force -ErrorAction SilentlyContinue
    exit $rcRelaunch
}
if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'config\app.env'))) { Write-Host "不是已安装的目录（缺少 config\app.env）：$InstallDir" -ForegroundColor Red; exit 50 }
Set-IshRoot $InstallDir
Start-Log 'upgrade'
if (-not (Test-SimPlatform)) { Assert-Admin '停止/启动服务、替换程序文件' }

function Get-VersionText([string]$base) {
    $f = Join-Path $base 'VERSION.txt'
    if (-not (Test-Path -LiteralPath $f)) { return '' }
    return ((Get-Content -LiteralPath $f -Raw -Encoding UTF8).Trim() -split '\s', 2)[0]
}
function Compare-IshVersion([string]$a, [string]$b) {
    # 2.1.0-rc1 < 2.1.0-rc2 < 2.1.0 < 2.1.1；返回 -1/0/1
    function P([string]$v) {
        $core, $pre = $v -split '-', 2
        $nums = @($core -split '\.' | ForEach-Object { [int]$_ })
        while ($nums.Count -lt 3) { $nums += 0 }
        $rank = if ($pre) { 0 } else { 1 }
        $preNum = 0
        if ($pre -match '(\d+)$') { $preNum = [int]$Matches[1] }
        return @($nums[0], $nums[1], $nums[2], $rank, $preNum)
    }
    $x = P $a; $y = P $b
    for ($i = 0; $i -lt 5; $i++) { if ($x[$i] -lt $y[$i]) { return -1 }; if ($x[$i] -gt $y[$i]) { return 1 } }
    return 0
}
function Get-PgMajorOfBin([string]$base) {
    $exe = Join-Path $base 'runtime\pgsql\bin\postgres.exe'
    $ErrorActionPreference = 'Continue'
    $v = & $exe --version 2>&1
    if ("$v" -match '(\d+)\.\d+') { return [int]$Matches[1] }
    return -1
}
function Get-FreeBytes([string]$path) {
    $full = (Resolve-Path -LiteralPath $path).Path
    if ([Environment]::OSVersion.Platform -eq 'Unix') {
        # 仅 Linux 模拟测试：按挂载点统计
        $line = (& df -Pk $full | Select-Object -Last 1) -split '\s+'
        return [int64]$line[3] * 1024
    }
    return ([IO.DriveInfo]::new([IO.Path]::GetPathRoot($full))).AvailableFreeSpace
}
function Get-DirBytes([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return 0 }
    $s = (Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    if ($s) { return [int64]$s } else { return 0 }
}
function Wait-AppVersion([string]$expect, [int]$seconds = 120) {
    if (Test-SimPlatform) { $seconds = [Math]::Min($seconds, 60) }
    $cfg = Read-AppEnv
    $port = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
    $body = Wait-Http "http://127.0.0.1:$port/healthz" $seconds
    if (-not $body) { return "应用未在 $seconds 秒内响应 /healthz" }
    $v = ($body | ConvertFrom-Json).version
    if ($v -ne $expect) { return "应用版本为 $v，期望 $expect" }
    return $null
}

# ---------------------------------------------------------------- 回滚（按日志逆序）
function Invoke-UpgradeRollback([string]$why) {
    $j = $script:Journal
    Write-Host "`n开始回滚（$why）……" -ForegroundColor Yellow
    Write-IshLog "ROLLBACK begin: $why"
    $errs = @()
    try { Stop-IshService $SvcApp } catch { Write-IshLog "stop app: $($_.Exception.Message)" }
    $steps = @($j.steps)
    [array]::Reverse($steps)
    $port = [int](Read-AppEnv)['ISH_DB_PORT']
    $dbStopped = $false
    foreach ($st in $steps) {
        if (Test-Prop $st 'undone') { continue }
        try {
            switch ($st.step) {
                # 按实际存在性撤销（意图先于动作记录；动作未发生则跳过；可重复执行）
                'file_new_in' { if (-not $dbStopped) { Stop-IshService $SvcDb; $dbStopped = $true }; if ((Test-Path -LiteralPath $st.dst) -and -not (Test-Path -LiteralPath $st.src)) { Move-Item -LiteralPath $st.dst -Destination $st.src } }
                'file_old_out' { if (-not $dbStopped) { Stop-IshService $SvcDb; $dbStopped = $true }; if ((Test-Path -LiteralPath $st.dst) -and -not (Test-Path -LiteralPath $st.src)) { Move-Item -LiteralPath $st.dst -Destination $st.src } }
                'db_migrate_started' {
                    # 用升级前整库快照替换（改名切换）；迁移过的库保留为 _failed_<ID> 供分析
                    Start-IshService $SvcDb; $dbStopped = $false
                    if (-not (Wait-Db $port 60)) { throw '数据库未就绪' }
                    $c = Get-DbConn
                    $failed = "$($c.Db)_failed_$($j.id)"
                    $has = { param($n) (Invoke-Psql "SELECT 1 FROM pg_database WHERE datname='$n'") -eq '1' }
                    # 两次改名分别按存在性执行：中途失败后再次 -Rollback 可以继续（P1 修复）
                    if ((& $has $c.Db) -and -not (& $has $failed) -and (& $has $st.snapshot)) { Invoke-Psql "ALTER DATABASE `"$($c.Db)`" RENAME TO `"$failed`"" | Out-Null }
                    if ((& $has $st.snapshot) -and -not (& $has $c.Db)) { Invoke-Psql "ALTER DATABASE `"$($st.snapshot)`" RENAME TO `"$($c.Db)`"" | Out-Null }
                    if (-not (& $has $c.Db)) { throw "正式库 $($c.Db) 不存在（快照 $($st.snapshot) 也不存在），需人工处理" }
                    $j['failed_db'] = $failed
                }
                'db_snapshot' {
                    # 快照未被用于回滚（失败发生在迁移之前）：删除快照；若已被改名为正式库则不存在，无操作
                    Start-IshService $SvcDb; $dbStopped = $false
                    if (-not (Wait-Db $port 60)) { throw '数据库未就绪' }
                    Invoke-Psql "DROP DATABASE IF EXISTS `"$($st.snapshot)`"" | Out-Null
                }
                default { }
            }
            $st | Add-Member -NotePropertyName undone -NotePropertyValue (Get-Date -Format s) -Force
            Save-OpsJournal
            Write-IshLog "undone $($st.step)"
        } catch {
            $errs += "$($st.step): $($_.Exception.Message)"
            Write-IshLog "UNDO FAILED $($st.step): $($_.Exception.Message)"
            break
        }
    }
    if (-not $errs.Count) {
        # 与升级前记录的不一致项相同即可（升级前已存在的差异不应阻止回滚）
        $known = @($j.old_release_mismatch)
        $bad = @(Test-PackageChecksums $Root | Where-Object { $known -notcontains $_ })
        if ($bad.Count) { $errs += "回滚后旧发行文件与旧清单不一致：$(($bad | Select-Object -First 5) -join '；')" }
    }
    if (-not $errs.Count) {
        try {
            Set-IshAcls
            Start-IshService $SvcDb
            if (-not (Wait-Db $port 60)) { throw '数据库未就绪' }
            $rc = Invoke-Manage @('verify_integrity') -AllowFail
            if ($rc -ne 0) { throw '旧程序完整性校验失败' }
            Start-IshService $SvcApp
            $e = Wait-AppVersion $j.old_version 120
            if ($e) { throw $e }
        } catch { $errs += "回滚后启动/校验：$($_.Exception.Message)" }
    }
    if ($errs.Count) {
        $j.state = 'ROLLBACK_FAILED'; $j['rollback_errors'] = $errs; Save-OpsJournal
        Write-Host "`n回滚未完成，系统保持维护（只读）状态。请勿手工移动文件；把以下日志交给实施人员：" -ForegroundColor Red
        Write-Host "  $script:JournalPath`n  $script:LogFile" -ForegroundColor Red
        Write-Host "  修复后可再次执行：07-升级程序.bat -Rollback $($j.id)" -ForegroundColor Red
        return $false
    }
    Clear-Maintenance | Out-Null
    $j.state = 'ROLLED_BACK'; $j['rolled_back_at'] = (Get-Date -Format s); Save-OpsJournal
    Write-Host "回滚完成：程序、运行时、脚本、版本与数据库均为升级前状态（$($j.old_version)）。" -ForegroundColor Green
    return $true
}

# ---------------------------------------------------------------- -Status / -Cleanup / -Rollback
if ($Status) {
    Get-ChildItem -LiteralPath $OpsDir -Filter 'upgrade-*.json' -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
        $o = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $ov = if (Test-Prop $o 'old_version') { $o.old_version } else { '?' }
        $nv = if (Test-Prop $o 'new_version') { $o.new_version } else { '?' }
        Write-Host ("{0}  {1}  阶段 {2}  {3} → {4}" -f $o.id, $o.state, $o.phase, $ov, $nv)
    }
    exit 0
}
$tok, $busy = Enter-OpsLock 'upgrade' 0
if (-not $tok) { Fail $busy 75 }
try {
    if ($Cleanup) {
        $j = Read-OpsJournal 'upgrade' $Cleanup
        if ($j.state -ne 'DONE') { Fail "只能清理已完成（DONE）的升级，当前 $($j.state)" 61 }
        $snap = @($j.steps | Where-Object { $_.step -eq 'db_migrate_started' } | Select-Object -First 1)
        if ($snap.Count) { Invoke-Psql "DROP DATABASE IF EXISTS `"$($snap[0].snapshot)`"" | Out-Null }
        $u = Join-Path $Root "_upgrade\$Cleanup"
        if (Test-Path -LiteralPath $u) { Remove-Item -LiteralPath $u -Recurse -Force }
        $j['cleaned_at'] = (Get-Date -Format s); Save-OpsJournal
        Write-Ok "已删除旧版本文件与数据库快照（升级 $Cleanup）"
        exit 0
    }
    if ($Rollback) {
        $j = Read-OpsJournal 'upgrade' $Rollback
        if ($j.state -eq 'ROLLED_BACK') { Write-Ok '该升级已回滚'; exit 0 }
        if ($j.state -eq 'DONE') {
            Write-Host "升级 $Rollback 已完成。回滚会把数据库换回升级前快照：升级之后录入的所有业务数据将丢失（仍保存在 _failed_ 库中供人工处理）。" -ForegroundColor Yellow
            if (-not $Yes) { if ((Read-Host '确认回滚请输入「确认回滚」') -ne '确认回滚') { Write-Host '已取消'; exit 0 } }
            if ($j['cleaned_at']) { Fail '该升级的旧版本与快照已清理，无法回滚；请用 05-恢复备份 恢复 pre-upgrade 备份' 62 }
        }
        $m = Set-Maintenance "upgrade-rollback $Rollback"
        if ($m -and -not ((Get-MaintenanceOwner).task -like "upgrade*")) { Fail $m 63 }
        # 失败的升级会保留它自己的维护标记（旧 token）：回滚由同一升级 ID 接管
        Write-AtomicText (Join-Path $Data 'MAINTENANCE') (([ordered]@{ token = $script:OpsToken; task = "upgrade-rollback $Rollback"; pid = $PID; at = (Get-Date -Format s) }) | ConvertTo-Json -Compress)
        if (Invoke-UpgradeRollback '管理员执行 -Rollback') { exit 0 } else { exit 64 }
    }

    # ============================================================ 升级
    $id = New-OpsJournal 'upgrade'
    $j = $script:Journal
    Set-OpsPhase 'PRECHECK'
    $j['install_dir'] = $Root; $j['package_dir'] = $PkgRoot
    if ($PkgRoot -eq $Root) { Fail '请从“新版本安装包目录”运行升级器，并用 -InstallDir 指定安装目录。' 50 }
    if ($PkgRoot.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase) -or $Root.StartsWith($PkgRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { Fail '新安装包目录不能位于安装目录之内（或相反）。' 50 }
    foreach ($p in @($PkgRoot, $Root)) { if ($p.StartsWith('\\')) { Fail "不支持网络路径：$p" 50 } }
    $bad = @(Test-PackageChecksums $PkgRoot)
    if ($bad.Count) { Fail ("新安装包校验失败：`n  " + (($bad | Select-Object -First 10) -join "`n  ")) 51 }
    Write-Ok '新安装包 SHA256 全量校验通过'
    $old = Get-VersionText $Root; $new = Get-VersionText $PkgRoot
    $j['old_version'] = $old; $j['new_version'] = $new
    $cmp = Compare-IshVersion $new $old
    if ($cmp -lt 0) { Fail "禁止降级：当前 $old，安装包 $new" 52 }
    if ($cmp -eq 0 -and -not $Repair) { Fail "安装包版本与当前相同（$old）。如需重新部署同版本，请加 -Repair" 52 }
    $oldBad = @(Test-PackageChecksums $Root)
    $j['old_release_mismatch'] = $oldBad
    if ($oldBad.Count) { Write-Warn2 ("当前安装的发行文件与其清单不一致（{0} 项，已记录到日志）：回滚将恢复为当前实际文件。" -f $oldBad.Count) }
    $pgNew = Get-PgMajorOfBin $PkgRoot
    $pgData = [int](Get-Content -LiteralPath (Join-Path $PgData 'PG_VERSION') -Raw).Trim()
    if ($pgNew -ne $pgData) { Fail "数据库大版本不一致：数据目录为 PostgreSQL $pgData，新安装包为 $pgNew。跨大版本升级需要专门的 pg_upgrade 流程，本升级器拒绝执行。" 53 }
    # 新包暂存 + 数据库快照（≈ pgdata）+ pre-upgrade 备份（≈ pgdata + private）+ 1 GB 余量（模拟测试机可用 ISH_SIM_MARGIN_MB 调小）
    $margin = 1GB
    if ((Test-SimPlatform) -and $env:ISH_SIM_MARGIN_MB) { $margin = [int64]$env:ISH_SIM_MARGIN_MB * 1MB }
    $need = (Get-DirBytes $PkgRoot) + 2 * ((Get-DirBytes $PgData) + (Get-DirBytes (Join-Path $Data 'private'))) + $margin
    $free = Get-FreeBytes $Root
    if ($free -lt $need) { Fail ("磁盘空间不足：需要约 {0:N0} MB，剩余 {1:N0} MB" -f ($need / 1MB), ($free / 1MB)) 54 }
    Write-Ok ("版本 {0} → {1}；PostgreSQL {2}；磁盘剩余 {3:N0} MB" -f $old, $new, $pgData, ($free / 1MB))
    Save-OpsJournal
    if (-not $Yes) {
        $ans = Read-Host "升级期间系统暂停使用（通常数分钟）。输入 Y 继续"
        if ($ans -notin @('Y', 'y')) { $j.state = 'CANCELLED'; Save-OpsJournal; exit 0 }
    }

    Set-OpsPhase 'LOCK'
    $m = Set-Maintenance "upgrade $id"
    if ($m) { Fail $m 55 }
    $j['maintenance'] = $true; Save-OpsJournal
    $failed = $false
    try {
        Stop-IshService $SvcApp
        Test-FaultInjection 'upgrade.after_stop'

        Set-OpsPhase 'BACKUP'
        $cfg = Read-AppEnv
        $bdest = if ($cfg['ISH_BACKUP_DIR']) { $cfg['ISH_BACKUP_DIR'] } else { $Backups }
        $label = "pre-upgrade-$id"
        # 旧程序可能不认识 --no-prune（RC1）：用很大的保留天数代替，避免清理掉旧备份
        $rc = Invoke-Manage @('ish_backup', '--dest', $bdest, '--pg-bin', $PgBin, '--label', $label, '--keep-days', '36500') -AllowFail
        # RC1 程序的备份命令会自行写入并删除维护标记：重新确认本次升级的维护标记仍在
        if (-not (Get-MaintenanceOwner)) { $null = Set-Maintenance "upgrade $id" }
        if ($rc -ne 0) { throw "旧程序 pre-upgrade 备份失败（退出码 $rc）" }
        $bpath = @(Get-ChildItem -LiteralPath $bdest -Directory -Filter "ish-backup-*-$label" | Sort-Object Name | Select-Object -Last 1)
        if (-not $bpath.Count) { throw "找不到 pre-upgrade 备份目录（$label）" }
        $bpath = $bpath[0].FullName
        $j['pre_backup'] = $bpath; Save-OpsJournal
        $drillDb = ("ish_drill_upg_" + $id)
        $drillDir = New-OpsTempDir "drill-$id"
        $rc = Invoke-Manage @('ish_verify_backup', $bpath, '--restore-to-db', $drillDb, '--restore-data-dir', $drillDir, '--pg-bin', $PgBin) -AllowFail
        if ($rc -ne 0) { throw "pre-upgrade 备份恢复演练失败（退出码 $rc）" }
        Write-Ok "pre-upgrade 备份已用旧程序校验并恢复演练通过：$bpath"
        Test-FaultInjection 'upgrade.after_backup'

        Set-OpsPhase 'SNAPSHOT'
        $c = Get-DbConn
        $snapDb = "$($c.Db)_preupg_$id"
        Add-OpsStep @{ step = 'db_snapshot'; snapshot = $snapDb }   # 先记意图，再创建（崩溃后回滚按存在性处理）
        Invoke-Psql "CREATE DATABASE `"$snapDb`" TEMPLATE `"$($c.Db)`"" | Out-Null
        Write-Ok "数据库快照：$snapDb"

        Set-OpsPhase 'STAGE'
        $items = Get-ReleaseItems $PkgRoot
        $oldItems = @()
        if (Test-Path -LiteralPath (Join-Path $Root 'SHA256SUMS.txt')) { $oldItems = Get-ReleaseItems $Root }
        $allItems = @($items + $oldItems | Sort-Object -Unique)
        $j['release_items'] = $allItems
        $ubase = Join-Path $Root "_upgrade\$id"
        $stage = Join-Path $ubase 'new'
        $keep = Join-Path $ubase 'old'
        New-Item -ItemType Directory -Force -Path $stage, $keep | Out-Null
        foreach ($it in $items) {
            $src = Join-Path $PkgRoot $it
            if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $stage $it) -Recurse -Force }
        }
        Test-FaultInjection 'upgrade.stage_copy'
        $bad = @(Test-PackageChecksums $stage)
        if ($bad.Count) { throw ("暂存目录校验失败：" + (($bad | Select-Object -First 5) -join '；')) }
        Write-Ok "新发行文件已暂存并校验：$stage"

        Set-OpsPhase 'SWITCH'
        Stop-IshService $SvcDb
        foreach ($it in $allItems) {
            $cur = Join-Path $Root $it
            if (Test-Path -LiteralPath $cur) {
                Add-OpsStep @{ step = 'file_old_out'; src = $cur; dst = (Join-Path $keep $it) }
                Move-Item -LiteralPath $cur -Destination (Join-Path $keep $it)
            }
        }
        Test-FaultInjection 'upgrade.switch_half'
        foreach ($it in $items) {
            $s = Join-Path $stage $it
            if (Test-Path -LiteralPath $s) {
                Add-OpsStep @{ step = 'file_new_in'; src = $s; dst = (Join-Path $Root $it) }
                Move-Item -LiteralPath $s -Destination (Join-Path $Root $it)
            }
        }
        $bad = @(Test-PackageChecksums $Root)
        if ($bad.Count) { throw ("切换后安装目录与新清单不一致：" + (($bad | Select-Object -First 5) -join '；')) }
        Write-Ok '安装目录发行文件已与新版本清单逐文件一致'
        Set-IshAcls

        Set-OpsPhase 'MIGRATE'
        Start-IshService $SvcDb
        if (-not (Wait-Db ([int]$cfg['ISH_DB_PORT']) 60)) { throw '数据库未能启动' }
        Add-OpsStep @{ step = 'db_migrate_started'; snapshot = $snapDb }
        Test-FaultInjection 'upgrade.migrate'
        $rc = Invoke-Manage @('ish_upgrade', '--pre-backup', $bpath, '--pg-bin', $PgBin) -AllowFail
        if ($rc -ne 0) { throw "新程序迁移/完整性校验失败（退出码 $rc）" }

        Set-OpsPhase 'VERIFY'
        Start-IshService $SvcApp
        Test-FaultInjection 'upgrade.verify'
        $e = Wait-AppVersion $new 120
        if ($e) { throw $e }
    } catch {
        $failed = $true
        $j['error'] = "$($_.Exception.Message)"; Save-OpsJournal
        Write-Host "`n升级失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-IshLog "UPGRADE FAILED: $($_.Exception.Message)"
    }
    if ($failed) {
        if (Invoke-UpgradeRollback '升级失败自动回滚') { exit 56 } else { exit 57 }
    }
    Clear-Maintenance | Out-Null
    $j.state = 'DONE'; $j['finished_at'] = (Get-Date -Format s)
    Set-OpsPhase 'DONE'
    Write-Host "`n升级完成：$old → $new（升级 ID $id）" -ForegroundColor Green
    Write-Host "旧版本发行文件保留在 _upgrade\$id\old，数据库快照 $snapDb；确认运行正常（建议一周）后执行：" -ForegroundColor Green
    Write-Host "  `"$Root\07-升级程序.bat`" -Cleanup $id"
    exit 0
} finally {
    Exit-OpsLock
}
