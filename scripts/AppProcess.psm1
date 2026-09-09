Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-OwnedServer($Root, $Port) {
    $path = Join-Path $Root ('temp/server-' + $Port + '.json')
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $record = Get-Content -LiteralPath $path -Encoding UTF8 | ConvertFrom-Json
        $process = Get-Process -Id $record.pid -ErrorAction Stop
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $record.start_ticks) { return $null }
        $info = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $record.pid)
        $entry = Join-Path $Root 'scripts\entry.py'
        if (-not $info.CommandLine.Contains($entry) -or -not $info.CommandLine.Contains('server')) { return $null }
        return $process
    } catch { return $null }
}

function Wait-AppReady($Port, $Process, $Name) {
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($Process.HasExited) { throw '服务启动后退出，请查看 temp 中的服务错误日志。' }
        try {
            $response = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $Port + '/api/bootstrap') -TimeoutSec 2
            if ($response.app_name -eq $Name) { return }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    throw '启动等待超时，请查看 temp 中的服务日志；账本错误或端口占用均会停止启动。'
}

function Enter-LauncherLock($Root, $Port) {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { $key = [BitConverter]::ToString($hasher.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant() + ':' + $Port))).Replace('-', '') } finally { $hasher.Dispose() }
    $mutex = [System.Threading.Mutex]::new($false, ('Local\XunwenLauncher-' + $key))
    try { $locked = $mutex.WaitOne(300000) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { $mutex.Dispose(); throw '另一启动或停止操作仍在进行，请稍后重试。' }
    return $mutex
}

Export-ModuleMember -Function Get-OwnedServer,Wait-AppReady,Enter-LauncherLock
