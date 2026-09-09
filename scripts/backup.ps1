param([string]$SourceRoot = '')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Environment.psm1') -Force
$python = Initialize-AppRuntime
$arguments = @('-B','-X','utf8',(Join-Path $PSScriptRoot 'entry.py'),'data','backup')
if ($SourceRoot) { $arguments += @('--source-root', (Resolve-Path -LiteralPath $SourceRoot).Path) }
& $python @arguments
exit $LASTEXITCODE
