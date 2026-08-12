#Requires -RunAsAdministrator

[CmdletBinding()]
param([Parameter(Mandatory)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$target = Join-Path $PSScriptRoot 'Invoke-EpicVMReadinessPreflight.ps1'
try {
    & $target -ReportPath $ReportPath
}
catch {
    $errorPath = $ReportPath + '.error.txt'
    ($_ | Format-List * -Force | Out-String) | Set-Content -LiteralPath $errorPath -Encoding UTF8 -NoNewline
    exit 1
}
