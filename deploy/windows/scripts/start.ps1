# 02-启动系统：检查并启动服务（重复执行安全：不新建实例、不 seed、不改密钥）
param([switch]$NoBrowser)
. (Join-Path $PSScriptRoot 'common.ps1')
Start-Log 'start'
if (-not (Test-Path -LiteralPath $AppEnv)) { Fail '尚未安装，请先运行 01-安装部署.bat' 30 }
$cfg = Read-AppEnv
$port = [int]($cfg['ISH_LISTEN'] -split ':')[-1]
foreach ($svc in @($SvcDb, $SvcApp)) {
    $st = Get-ServiceState $svc
    if ($st -eq 'NotInstalled') { Fail "服务 $svc 未安装，请重新运行 01-安装部署.bat" 31 }
    if ($st -ne 'Running') {
        if (-not (Test-Admin)) { Fail "服务 $svc 当前为 $st，启动服务需要管理员权限（右键以管理员身份运行）" 10 }
        Write-Step "启动 $svc"
        Start-Service -Name $svc
    } else { Write-Ok "$svc 运行中" }
}
if (-not (Wait-Db ([int]$cfg['ISH_DB_PORT']) 60)) { Fail '数据库未就绪（见 logs\postgresql）' 23 }
$h = Wait-Http "http://127.0.0.1:$port/healthz" 90
if (-not $h) { Fail '应用未就绪（见 logs\service、logs\app.log）' 25 }
Write-Ok "系统就绪：$h"
$scheme = if ($cfg['ISH_HTTPS'] -eq '1') { 'https' } else { 'http' }
Write-Host '局域网访问地址：' -ForegroundColor Green
Get-LanAddresses | ForEach-Object { Write-Host "  ${scheme}://${_}:$port/" -ForegroundColor Green }
Write-Host '关闭本窗口或浏览器不会停止系统；系统随开机自动启动。'
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$port/" }
