#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ReportPath = 'C:\ProgramData\EpicVM\clean-source-start.json')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$result = $null
try {
    Import-Module Hyper-V
    $vm = Get-VM -Name 'EpicVM-CleanTemplateSource' -ErrorAction Stop
    if ($vm.State -ne 'Running') { Start-VM -Name $vm.Name -ErrorAction Stop }
    $result = [ordered]@{ name = $vm.Name; state = [string](Get-VM -Name $vm.Name).State }
    $json = $result | ConvertTo-Json -Compress
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $tmp = "$ReportPath.tmp"
    Set-Content -LiteralPath $tmp -Value $json -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath $tmp -Destination $ReportPath -Force
}
catch {
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $tmp = "$ReportPath.tmp"
    Set-Content -LiteralPath $tmp -Value (@{ error = $_.Exception.Message } | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath $tmp -Destination $ReportPath -Force
    exit 1
}
