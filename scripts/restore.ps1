param([string]$Archive = '')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Environment.psm1') -Force
$python = Initialize-AppRuntime
if (-not $Archive) { $Archive = (Read-Host '请粘贴数据迁移包 ZIP 的路径').Trim('"') }
& $python -B -X utf8 (Join-Path $PSScriptRoot 'entry.py') data restore --archive (Resolve-Path -LiteralPath $Archive).Path
exit $LASTEXITCODE
