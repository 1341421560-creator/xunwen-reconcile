Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# 使用当前 Windows PowerShell 自带模块，不依赖父进程的模块搜索路径。
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [System.IO.Path]::PathSeparator + $env:PSModulePath

function Get-AppRoot { return (Split-Path -Parent $PSScriptRoot) }

function Get-AppConfig {
    return (Get-Content -LiteralPath (Join-Path (Get-AppRoot) 'config/defaults.json') -Encoding UTF8 | ConvertFrom-Json)
}

function Test-InstalledRuntime($Directory, $Fingerprint) {
    $marker = Join-Path $Directory 'installed.json'
    if (-not (Test-Path -LiteralPath $marker)) { return $false }
    try {
        $installed = Get-Content -LiteralPath $marker -Encoding UTF8 | ConvertFrom-Json
        if ($installed.fingerprint -ne $Fingerprint -or $installed.files.Count -lt 30) { return $false }
        foreach ($item in $installed.files) {
            $path = Join-Path $Directory $item.path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $item.sha256) { return $false }
        }
        return $true
    } catch { return $false }
}

function Expand-VerifiedArchive($Source, $Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Directory]::CreateDirectory($Destination) | Out-Null
    $boundary = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Source)
    try {
        foreach ($entry in $archive.Entries) {
            $target = [System.IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if (-not $target.StartsWith($boundary, [System.StringComparison]::OrdinalIgnoreCase)) { throw '运行包包含越界路径。' }
            if ([string]::IsNullOrEmpty($entry.Name)) { [System.IO.Directory]::CreateDirectory($target) | Out-Null; continue }
            [System.IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $false)
        }
    } finally { $archive.Dispose() }
}

function Initialize-AppRuntime {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $root = Get-AppRoot
    $config = Get-AppConfig
    $temp = Join-Path $root $config.temp_dir
    [System.IO.Directory]::CreateDirectory($temp) | Out-Null
    $env:TEMP = $temp
    $env:TMP = $temp
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUTF8 = '1'
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or -not [Environment]::Is64BitOperatingSystem) { throw '此离线版本需要 64 位 Windows。' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw '此版本面向 Windows x64；ARM64 请使用专门的运行包。' }
    $manifestPath = Join-Path $root 'config/runtime.json'
    $runtime = Get-Content -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
    $fingerprint = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    foreach ($archive in $runtime.archives) {
        $path = Join-Path $root $archive.file
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw ('离线包缺失：' + $archive.file + '。请完整 clone 仓库。') }
        if ((Get-Item -LiteralPath $path).Length -ne $archive.size -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $archive.sha256) { throw ('离线包校验失败：' + $archive.file) }
    }
    $mutex = [System.Threading.Mutex]::new($false, ('Local\XunwenRuntime-' + $fingerprint))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(300000) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw '另一窗口仍在准备环境，请稍后重新启动。' }
        $base = Join-Path $runtime.environment_parent $runtime.bundle_id
        $candidates = @($base)
        if (Test-Path -LiteralPath $runtime.environment_parent) {
            $candidates += @(Get-ChildItem -LiteralPath $runtime.environment_parent -Directory -Filter ($runtime.bundle_id + '-repair-*') | Sort-Object Name -Descending | ForEach-Object { $_.FullName })
        }
        foreach ($candidate in $candidates) {
            if (Test-InstalledRuntime $candidate $fingerprint) { return (Join-Path $candidate 'python.exe') }
        }
        $directory = $base
        if (Test-Path -LiteralPath $directory) { $directory += '-repair-' + [guid]::NewGuid().ToString('N') }
        Write-Host ('正在从离线包准备运行环境：' + $directory)
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        foreach ($archive in $runtime.archives) {
            Expand-VerifiedArchive (Join-Path $root $archive.file) (Join-Path $directory $archive.target)
        }
        # 内嵌解释器只加载本运行包，项目入口显式添加自身路径。
        $paths = $runtime.python_library + "`n.`nLib/site-packages`n"
        [System.IO.File]::WriteAllText((Join-Path $directory $runtime.python_path_file), $paths, [System.Text.UTF8Encoding]::new($false))
        $python = Join-Path $directory 'python.exe'
        $probe = & $python -B -X utf8 (Join-Path $root 'deployment/runtime_probe.py')
        if ($LASTEXITCODE -ne 0 -or $probe -ne $runtime.python_version) { throw '运行环境自检失败，已保留现场；再次启动将尝试独立修复。' }
        $files = @(Get-ChildItem -LiteralPath $directory -Recurse -File | ForEach-Object {
            @{path=$_.FullName.Substring($directory.Length + 1).Replace('\','/'); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
        })
        $record = @{fingerprint=$fingerprint; files=$files} | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText((Join-Path $directory 'installed.json'), $record, [System.Text.UTF8Encoding]::new($false))
        return $python
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

Export-ModuleMember -Function Get-AppRoot,Get-AppConfig,Initialize-AppRuntime
