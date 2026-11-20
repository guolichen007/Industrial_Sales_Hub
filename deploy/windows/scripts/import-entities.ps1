# 09-导入主体配置：把仓库外保存的真实主体配置（全称/税号/银行/条款）导入系统。需要部门负责人账号授权，写审计。
#   09-导入主体配置.bat -ConfigFile "D:\IndustrialSalesHub\config\entities.real.json" -AuthorizedBy <负责人用户名> [-DryRun]
# 永不修改模板签收状态（签收只能由负责人在系统内按模板版本完成）。
param([Parameter(Mandatory = $true)][string]$ConfigFile, [Parameter(Mandatory = $true)][string]$AuthorizedBy, [switch]$DryRun)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'import-entities'
Assert-CanReadConfig
if (-not (Test-Path -LiteralPath $AppEnv)) { Fail '尚未安装' 30 }
$ConfigFile = (Resolve-Path -LiteralPath $ConfigFile).Path
$tok, $busy = Enter-OpsLock 'import-entities' 0
if (-not $tok) { Fail $busy 75 }
try {
    $a = @('import_entity_config', $ConfigFile, '--authorized-by', $AuthorizedBy)
    if ($DryRun) { $a += '--dry-run' }
    else {
        Write-Step '先预览差异'
        Invoke-Manage @('import_entity_config', $ConfigFile, '--authorized-by', $AuthorizedBy, '--dry-run') | Out-Null
        if ((Read-Host '确认导入以上变更？输入 Y 继续') -notin @('Y', 'y')) { Write-Host '已取消'; exit 0 }
    }
    Invoke-Manage $a | Out-Null
    Write-Ok '完成（已写审计）。历史报价 PDF 不受影响；新报价使用新信息。'
} finally { Exit-OpsLock }
