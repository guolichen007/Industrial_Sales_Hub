# 本地复刻 CI windows job 的两项静态检查（PowerShell 7 解析 + PSScriptAnalyzer 1.22.0，规则与 CI 相同）
param([string]$Dir)
$errs = 0
Get-ChildItem (Join-Path $Dir '*.ps1') | ForEach-Object {
    $t = $null; $e = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$t, [ref]$e) | Out-Null
    if ($e.Count) { $errs++; $e | ForEach-Object { Write-Host "PARSE $($_.Extent.File):$($_.Extent.StartLineNumber) $($_.Message)" } }
}
Import-Module PSScriptAnalyzer
$r = Invoke-ScriptAnalyzer -Path $Dir -Severity Error,Warning -ExcludeRule PSAvoidUsingWriteHost,PSUseApprovedVerbs,PSUseShouldProcessForStateChangingFunctions,PSAvoidUsingPlainTextForPassword,PSUseSingularNouns,PSUseBOMForUnicodeEncodedFile
$r | ForEach-Object { Write-Host ("PSSA {0}:{1} [{2}] {3}" -f $_.ScriptName, $_.Line, $_.RuleName, $_.Message) }
Write-Host "parse_errors=$errs analyzer=$($r.Count)"
if ($errs -or $r.Count) { exit 1 }
