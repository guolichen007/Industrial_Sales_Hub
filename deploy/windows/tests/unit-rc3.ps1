# RC3-G5 PowerShell 单元测试（可在 Windows PowerShell 5.1 与 PowerShell 7 运行；不需要管理员、不修改系统）
#   T17 防火墙清理只匹配本程序应用规则；T18 多网卡选择默认网关所在的实体网卡
$ErrorActionPreference = 'Stop'
$fail = 0
function Check([string]$name, [bool]$ok) { if ($ok) { Write-Host "PASS $name" } else { Write-Host "FAIL $name"; $script:fail++ } }

# 只加载需要的函数（不 dot-source 整个 common.ps1，避免其初始化路径）
$src = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot '..\scripts\common.ps1')
$ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($fn.Name -in @('Get-AppFirewallRules', 'Select-LanAddress')) { . ([scriptblock]::Create($fn.Extent.Text)) }
}
$script:AppFwRulePrefix = 'Industrial Sales Hub App (TCP '

# ---- T17：模拟 Get-NetFirewallRule
function Get-NetFirewallRule { [Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSReviewUnusedParameter", "")] param($DisplayName, $ErrorAction)
    $all = @('Industrial Sales Hub HTTPS (443)', 'Industrial Sales Hub App (TCP 8000)', 'Industrial Sales Hub (TCP 8000)', 'Industrial Sales Hub Admin Note', 'Other App (TCP 80)')
    return @($all | Where-Object { $_ -like $DisplayName } | ForEach-Object { [pscustomobject]@{ DisplayName = $_ } })
}
$r = @(Get-AppFirewallRules | ForEach-Object DisplayName)
Check 'T17 应用规则（新名称）被匹配' ($r -contains 'Industrial Sales Hub App (TCP 8000)')
Check 'T17 RC1/RC2 旧名称应用规则被匹配（升级时清理）' ($r -contains 'Industrial Sales Hub (TCP 8000)')
Check 'T17 HTTPS 443 手工规则不被匹配' (-not ($r -contains 'Industrial Sales Hub HTTPS (443)'))
Check 'T17 其他同前缀规则不被匹配' (-not ($r -contains 'Industrial Sales Hub Admin Note'))
Check 'T17 只匹配 2 条' ($r.Count -eq 2)

# ---- T18：多网卡
$cands = @(
    [pscustomobject]@{ IP = '172.25.32.1'; Alias = 'vEthernet (WSL)'; Gateway = $false; Virtual = $true },
    [pscustomobject]@{ IP = '10.8.0.6'; Alias = 'OpenVPN TAP'; Gateway = $true; Virtual = $true },
    [pscustomobject]@{ IP = '192.168.56.1'; Alias = 'VirtualBox Host-Only'; Gateway = $false; Virtual = $true },
    [pscustomobject]@{ IP = '192.168.1.20'; Alias = 'Ethernet'; Gateway = $true; Virtual = $false },
    [pscustomobject]@{ IP = '10.0.5.7'; Alias = 'Ethernet 2'; Gateway = $false; Virtual = $false }
)
$sel = @(Select-LanAddress $cands)
Check 'T18 首选默认网关所在的实体网卡' ($sel[0] -eq '192.168.1.20')
Check 'T18 其次为无网关实体网卡' ($sel[1] -eq '10.0.5.7')
Check 'T18 虚拟/VPN 网卡排在最后' (@($sel[2..4]) -notcontains '192.168.1.20' -and $sel.Count -eq 5)
$sel1 = @(Select-LanAddress @([pscustomobject]@{ IP = '192.168.0.9'; Alias = 'Wi-Fi'; Gateway = $true; Virtual = $false }))
Check 'T18 单网卡' ($sel1.Count -eq 1 -and $sel1[0] -eq '192.168.0.9')

Write-Host "unit-rc3 fail=$fail"
if ($fail) { exit 1 }
