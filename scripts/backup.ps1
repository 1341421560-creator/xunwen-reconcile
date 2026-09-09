param([string]$SourceRoot = '', [string]$CompanyKey = '', [switch]$AllCompanies)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Environment.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'CompanyScope.psm1') -Force
$python = Initialize-AppRuntime
$arguments = @('-B','-X','utf8',(Join-Path $PSScriptRoot 'entry.py'),'data','backup')
if ($SourceRoot) { $arguments += @('--source-root', (Resolve-Path -LiteralPath $SourceRoot).Path) }
$arguments += @(Select-CompanyScope -CompanyKey $CompanyKey -AllCompanies:$AllCompanies)
& $python @arguments
exit $LASTEXITCODE
