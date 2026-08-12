#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ReportPath = 'C:\ProgramData\EpicVM\clean-source-disk-preflight.json')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    Import-Module Hyper-V
    $vm = Get-VM -Name 'EpicVM-CleanTemplateSource' -ErrorAction Stop
    if ([string]$vm.State -ne 'Off') { throw 'Clean source must be Off before disk publication.' }
    if ([string]$vm.Notes -notmatch '(?i)EpicVM-CleanTemplateSource\s*:\s*true') { throw 'Clean-source ownership marker did not match.' }
    $root = 'E:\EpicVM\clean-template-source'
    if ([IO.Path]::GetFullPath([string]$vm.Path).TrimEnd('\') -ine 'E:\EpicVM\clean-template-source\EpicVM-CleanTemplateSource') { throw 'Clean-source VM path did not match the managed fallback root.' }
    $drives = @(Get-VMHardDiskDrive -VM $vm -ErrorAction Stop)
    if ($drives.Count -ne 1) { throw 'Clean source must have exactly one attached disk.' }
    $diskPath = [IO.Path]::GetFullPath([string]$drives[0].Path)
    if (-not $diskPath.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Clean-source disk is outside its managed root.' }
    $vhd = Get-VHD -Path $diskPath -ErrorAction Stop
    $result = [ordered]@{
        ok = $true; vmName = $vm.Name; vmState = [string]$vm.State; diskPath = $diskPath
        vhdType = [string]$vhd.VhdType; parentPath = [string]$vhd.ParentPath
        sizeBytes = [int64]$vhd.Size; fileSizeBytes = [int64]$vhd.FileSize
        standalone = [string]::IsNullOrWhiteSpace([string]$vhd.ParentPath)
    }
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -LiteralPath "$ReportPath.tmp" -Value ($result | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
}
catch {
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -LiteralPath "$ReportPath.tmp" -Value (@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
    exit 1
}
