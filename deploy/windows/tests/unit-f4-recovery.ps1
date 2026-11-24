# RC3 定向整改 F-4 PowerShell 单元测试：恢复失败后的可用性判定（Windows PowerShell 5.1 与 PowerShell 7 均可运行；
#   不需要管理员、不修改系统、不需要数据库）。
#   验证 Get-RestoreRecoveryDecision 的状态映射：前置拒绝/无日志/已安全回退 → 可安全恢复应用服务(RESTART)；
#   正在恢复/回退失败/状态不确定 → 保持保护(PROTECT)。仅抽取该纯函数，避免 dot-source 整个 common.ps1 的初始化副作用。
$ErrorActionPreference = 'Stop'
$fail = 0
function Check([string]$name, [bool]$ok) { if ($ok) { Write-Host "PASS $name" } else { Write-Host "FAIL $name"; $script:fail++ } }

$src = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot '..\scripts\common.ps1')
$ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($fn.Name -eq 'Get-RestoreRecoveryDecision') { . ([scriptblock]::Create($fn.Extent.Text)) }
}

# 可安全恢复应用服务：数据可证明未改动或已完整回退
Check 'F-4 前置拒绝(FAILED_PRECHECK)→RESTART' ((Get-RestoreRecoveryDecision -State 'FAILED_PRECHECK' -HasJournal $true) -eq 'RESTART')
Check 'F-4 已安全回退(ROLLED_BACK)→RESTART' ((Get-RestoreRecoveryDecision -State 'ROLLED_BACK' -HasJournal $true) -eq 'RESTART')
Check 'F-4 无日志(未开始)→RESTART' ((Get-RestoreRecoveryDecision -State '' -HasJournal $false) -eq 'RESTART')

# 保持保护状态：状态不确定或回退失败
Check 'F-4 正在恢复(RUNNING)→PROTECT' ((Get-RestoreRecoveryDecision -State 'RUNNING' -HasJournal $true) -eq 'PROTECT')
Check 'F-4 回退失败(ROLLBACK_FAILED)→PROTECT' ((Get-RestoreRecoveryDecision -State 'ROLLBACK_FAILED' -HasJournal $true) -eq 'PROTECT')
Check 'F-4 异常 DONE 路径→PROTECT' ((Get-RestoreRecoveryDecision -State 'DONE' -HasJournal $true) -eq 'PROTECT')
Check 'F-4 空状态但有日志→PROTECT' ((Get-RestoreRecoveryDecision -State '' -HasJournal $true) -eq 'PROTECT')

Write-Host "unit-f4-recovery fail=$fail"
if ($fail -ne 0) { exit 1 }
exit 0
