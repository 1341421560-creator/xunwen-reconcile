$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Environment.psm1') -Force
$python = Initialize-AppRuntime
& $python -B -X utf8 (Join-Path $PSScriptRoot 'entry.py') check
exit $LASTEXITCODE
