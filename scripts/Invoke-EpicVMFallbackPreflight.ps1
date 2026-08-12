#Requires -RunAsAdministrator

[CmdletBinding()]
param([Parameter(Mandatory)][string]$ReportPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$names = @('testre','EpicVM-CleanTemplateSource','EpicVM-TemplateBuilder')
$vms = @()
foreach ($name in $names) {
    $vm = @(Get-VM -Name $name -ErrorAction SilentlyContinue) | Select-Object -First 1
    if ($null -eq $vm) { continue }
    $disks = @(Get-VMHardDiskDrive -VMName $name -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ path = [string]$_.Path; exists = Test-Path -LiteralPath ([string]$_.Path) -PathType Leaf }
    })
    $vms += [ordered]@{
        name = [string]$vm.Name
        state = [string]$vm.State
        path = [string]$vm.Path
        notes = [string]$vm.Notes
        disks = $disks
    }
}
$work = @(Get-ChildItem -LiteralPath 'E:\EpicVM\template-work' -Force -ErrorAction SilentlyContinue | ForEach-Object {
    [ordered]@{ name = $_.Name; path = $_.FullName; isDirectory = $_.PSIsContainer; length = if ($_.PSIsContainer) { [int64]0 } else { [int64]$_.Length } }
})
$templateRoot = 'E:\EpicVM\templates\win11-25h2'
$manifestPath = Join-Path $templateRoot 'manifest.json'
$manifest = $null
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) { try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }
$report = [ordered]@{
    vms = $vms
    templateWork = $work
    existingTemplate = [ordered]@{
        path = $templateRoot
        manifestPresent = Test-Path -LiteralPath $manifestPath -PathType Leaf
        build = if ($null -ne $manifest -and $manifest.PSObject.Properties['build']) { [string]$manifest.build } else { '' }
        sunshineInstalled = $null -ne $manifest -and $manifest.PSObject.Properties['sunshine'] -and [string]$manifest.sunshine -ieq 'installed'
        imagePath = if ($null -ne $manifest -and $manifest.PSObject.Properties['imagePath']) { [string]$manifest.imagePath } else { '' }
    }
}
$parent = Split-Path -Parent $ReportPath
if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
$tmp = $ReportPath + '.tmp'
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding UTF8 -NoNewline
Move-Item -LiteralPath $tmp -Destination $ReportPath -Force
$report | ConvertTo-Json -Depth 8
