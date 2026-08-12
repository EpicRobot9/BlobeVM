#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ReportPath = 'C:\Users\Epic\.codex\visualizations\2026\08\02\019fc3f3-fb3f-7361-b1b6-8eed5c67abe2\epicvm-provisioning-integration\.epicvm-builder-cleanup.json')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$vmName = 'EpicVM-TemplateBuilder'
$workRoot = 'E:\EpicVM\template-work'
try {
    Import-Module Hyper-V
    $vm = Get-VM -Name $vmName -ErrorAction Stop
    if ([string]$vm.Notes -notmatch '(?i)EpicVM-TemplateBuilder\s*:\s*true') { throw 'Builder ownership marker did not match.' }
    $vmPath = [IO.Path]::GetFullPath([string]$vm.Path).TrimEnd('\')
    $workFull = [IO.Path]::GetFullPath($workRoot).TrimEnd('\')
    if (-not ($vmPath.Equals($workFull,[StringComparison]::OrdinalIgnoreCase) -or $vmPath.StartsWith($workFull + '\',[StringComparison]::OrdinalIgnoreCase))) { throw 'Builder path is outside the managed staging root.' }
    if ([string]$vm.State -eq 'Running') { Stop-VM -Name $vmName -Force -ErrorAction Stop }
    Remove-VM -Name $vmName -Force -ErrorAction Stop
    $builderDirectory = Split-Path -Parent $vmPath
    if (Test-Path -LiteralPath $builderDirectory -PathType Container) { Remove-Item -LiteralPath $builderDirectory -Recurse -Force -ErrorAction Stop }
    $result = [ordered]@{ok=$true;name=$vmName;removedPath=$builderDirectory}
    $parent=Split-Path -Parent $ReportPath
    if($parent){New-Item -ItemType Directory -Path $parent -Force|Out-Null}
    Set-Content -LiteralPath "$ReportPath.tmp" -Value ($result|ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
}
catch {
    $parent=Split-Path -Parent $ReportPath
    if($parent){New-Item -ItemType Directory -Path $parent -Force|Out-Null}
    Set-Content -LiteralPath "$ReportPath.tmp" -Value (@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
    exit 1
}
