param([int]$Port = 0)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Import-Module (Join-Path $PSScriptRoot 'Environment.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'AppProcess.psm1') -Force
if ($Port -eq 0) { $Port = (Get-AppConfig).port }
$launcherLock = Enter-LauncherLock (Get-AppRoot) $Port
try {
$process = Get-OwnedServer (Get-AppRoot) $Port
if ($null -eq $process) { Write-Host '没有找到由当前目录启动的服务。'; exit 0 }
Stop-Process -Id $process.Id -ErrorAction Stop
Write-Host '当前目录的服务已停止，账本与导出文件保留。'
} finally { $launcherLock.ReleaseMutex(); $launcherLock.Dispose() }
