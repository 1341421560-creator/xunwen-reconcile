param([int]$Port = 0, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Environment.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'AppProcess.psm1') -Force
$python = Initialize-AppRuntime
$root = Get-AppRoot
$config = Get-AppConfig
if ($Port -eq 0) { $Port = $config.port }
if ($Port -lt 1 -or $Port -gt 65535) { throw '端口必须在 1 至 65535 之间。' }
$url = 'http://127.0.0.1:' + $Port
$launcherLock = Enter-LauncherLock $root $Port
try {
$process = Get-OwnedServer $root $Port
if ($null -eq $process) {
    # 启动前只探测端口，不接管其他目录启动的软件。
    $probe = [System.Net.Sockets.TcpClient]::new()
    try { $occupied = $probe.ConnectAsync('127.0.0.1', $Port).Wait(1000) } catch { $occupied = $false } finally { $probe.Dispose() }
    if ($occupied) { throw ('端口 ' + $Port + ' 已被其他实例占用。请停止该实例，或用 -Port 指定其他端口。') }
    $entry = Join-Path $root 'scripts\entry.py'
    $launched = & $python -B -X utf8 $entry launch --port $Port
    if ($LASTEXITCODE -ne 0) { throw '后台服务创建失败，请检查目录访问权限。' }
    $process = Get-Process -Id ($launched | ConvertFrom-Json).pid -ErrorAction Stop
    $record = @{pid=$process.Id; start_ticks=$process.StartTime.ToUniversalTime().Ticks.ToString(); port=$Port} | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $root ('temp/server-' + $Port + '.json')), $record, [System.Text.UTF8Encoding]::new($false))
}
Wait-AppReady $Port $process $config.app_name
Write-Host ('已启动：' + $url)
} finally { $launcherLock.ReleaseMutex(); $launcherLock.Dispose() }
if (-not $NoBrowser) { Start-Process $url }
