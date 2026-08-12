#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string] $VmName = 'testre',
    [string] $VmRoot = 'E:\EpicVM\vms',
    [string] $TemplateManifestPath = 'E:\EpicVM\templates\win11-25h2\manifest.json',
    [string] $AgentConfigPath = 'C:\ProgramData\EpicVM\agent\config.json',
    [string] $BootstrapCredentialPath = 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi',
    [string] $TailscaleOAuthSecretPath = 'C:\ProgramData\EpicVM\agent\tailscale-oauth.dpapi',
    [string] $ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-PathUnderRoot {
    param([string] $Path, [string] $Root)
    try {
        $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
        $base = [IO.Path]::GetFullPath($Root).TrimEnd('\')
        return $full.Equals($base, [StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)
    }
    catch { return $false }
}

function Get-ServiceExecutablePath {
    param([AllowNull()][object] $Service)
    if ($null -eq $Service) { return '' }
    $value = [string]$Service.PathName
    if ($value -match '^"([^"]+)"') { return $Matches[1] }
    if ($value -match '^([^ ]+)') { return $Matches[1] }
    return $value
}

$vm = @(Get-VM -Name $VmName -ErrorAction Stop) | Select-Object -First 1
$notes = [string]$vm.Notes
$vmPath = [string]$vm.Path
$expectedVmPath = Join-Path $VmRoot $VmName
$adapters = @(Get-VMNetworkAdapter -VMName $VmName -ErrorAction Stop)
$switchNames = @($adapters | ForEach-Object { [string]$_.SwitchName } | Where-Object { $_ } | Select-Object -Unique)
$disks = @(Get-VMHardDiskDrive -VMName $VmName -ErrorAction Stop)
$diskRecords = @()
foreach ($disk in $disks) {
    $diskPath = [IO.Path]::GetFullPath([string]$disk.Path)
    $vhd = $null
    try { $vhd = Get-VHD -Path $diskPath -ErrorAction Stop } catch { }
    $virtualSize = [int64]0
    $fileSize = [int64]0
    if ($null -ne $vhd) {
        [void][int64]::TryParse([string]$vhd.Size, [ref]$virtualSize)
        [void][int64]::TryParse([string]$vhd.FileSize, [ref]$fileSize)
    }
    $diskRecords += [ordered]@{
        leaf = [IO.Path]::GetFileName($diskPath)
        underManagedRoot = Test-PathUnderRoot -Path $diskPath -Root $VmRoot
        exists = Test-Path -LiteralPath $diskPath -PathType Leaf
        parentPresent = $null -ne $vhd -and -not [string]::IsNullOrWhiteSpace([string]$vhd.ParentPath)
        virtualSizeBytes = $virtualSize
        fileSizeBytes = $fileSize
    }
}
$sourceAllocation = [int64]0
if ($diskRecords.Count -gt 0) {
    $maximum = ($diskRecords | ForEach-Object { [int64]$_.virtualSizeBytes } | Measure-Object -Maximum).Maximum
    if ($null -ne $maximum) { [void][int64]::TryParse([string]$maximum, [ref]$sourceAllocation) }
}
if ($sourceAllocation -lt 0) { $sourceAllocation = 0 }
$eDrive = Get-PSDrive -Name E -ErrorAction Stop
$requiredBytes = ($sourceAllocation * 2) + (20GB)
$config = $null
if (Test-Path -LiteralPath $AgentConfigPath -PathType Leaf) {
    try { $config = Get-Content -LiteralPath $AgentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $config = $null }
}
$sunshineService = Get-CimInstance Win32_Service -Filter "Name='SunshineService'" -ErrorAction SilentlyContinue
$sunshineExe = Get-ServiceExecutablePath -Service $sunshineService
$sunshineInstalled = $null -ne $sunshineService -and (Test-Path -LiteralPath $sunshineExe -PathType Leaf)
$templateManifest = $null
if (Test-Path -LiteralPath $TemplateManifestPath -PathType Leaf) {
    try { $templateManifest = Get-Content -LiteralPath $TemplateManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $templateManifest = $null }
}
$templateImage = if ($null -ne $templateManifest -and $templateManifest.PSObject.Properties['imagePath']) { [string]$templateManifest.imagePath } else { '' }
$staleVm = @(Get-VM -Name 'EpicVM-TemplateBuilder' -ErrorAction SilentlyContinue).Count -gt 0
$staleWork = Test-Path -LiteralPath 'E:\EpicVM\template-work'
$gpuPartitionable = $false
try { $gpuPartitionable = @(Get-VMHostPartitionableGpu -ErrorAction Stop).Count -gt 0 } catch { $gpuPartitionable = $false }
$ownership = $notes -match '(?i)EpicVM-Managed\s*:\s*true'
$managedPath = [IO.Path]::GetFullPath($vmPath).TrimEnd('\') -eq [IO.Path]::GetFullPath($expectedVmPath).TrimEnd('\')
$diskGate = $disks.Count -eq 1 -and @($diskRecords | Where-Object { -not $_.underManagedRoot -or -not $_.exists }).Count -eq 0
$templateSunshine = $null -ne $templateManifest -and $templateManifest.PSObject.Properties['sunshine'] -and [string]$templateManifest.sunshine -ieq 'installed'
$templateImmutable = $null -ne $templateManifest -and $templateManifest.PSObject.Properties['immutable'] -and [bool]$templateManifest.immutable
$templateFullCopy = $null -ne $templateManifest -and $templateManifest.PSObject.Properties['fullCopy'] -and [bool]$templateManifest.fullCopy
$templateReady = $templateSunshine -and $templateImmutable -and $templateFullCopy -and (Test-Path -LiteralPath $templateImage -PathType Leaf)
$staleArtifacts = $staleVm -or $staleWork
$report = [ordered]@{
    vmName = $VmName
    ownership = [bool]$ownership
    running = ([string]$vm.State -ieq 'Running')
    managedPath = [bool]$managedPath
    vmPath = $vmPath
    switch = [ordered]@{ discovered = ($switchNames.Count -eq 1); name = if ($switchNames.Count -eq 1) { [string]$switchNames[0] } else { '' }; count = $switchNames.Count }
    disk = [ordered]@{ gate = [bool]$diskGate; count = $disks.Count; sourceAllocationBytes = $sourceAllocation; chain = @($diskRecords) }
    eDrive = [ordered]@{ freeBytes = [int64]$eDrive.Free; requiredBytes = $requiredBytes; capacityGate = ([int64]$eDrive.Free -ge $requiredBytes) }
    template = [ordered]@{ manifestPresent = $null -ne $templateManifest; sunshineInstalled = [bool]$templateSunshine; ready = [bool]$templateReady }
    bootstrapCredential = Test-Path -LiteralPath $BootstrapCredentialPath -PathType Leaf
    tailscaleOAuthSecret = Test-Path -LiteralPath $TailscaleOAuthSecretPath -PathType Leaf
    tailscaleOAuthClient = $null -ne $config -and -not [string]::IsNullOrWhiteSpace([string]$config.TailscaleOAuthClientId)
    tailscaleTailnet = $null -ne $config -and -not [string]::IsNullOrWhiteSpace([string]$config.TailscaleTailnet)
    sunshine = [ordered]@{ installed = [bool]$sunshineInstalled; servicePresent = $null -ne $sunshineService; desktopAppAvailable = [bool]$sunshineInstalled }
    gpuPartitionable = [bool]$gpuPartitionable
    staleBuilderArtifacts = [bool]$staleArtifacts
    readyForBuilder = [bool]($ownership -and $managedPath -and ([string]$vm.State -ieq 'Running') -and ($switchNames.Count -eq 1) -and $diskGate -and ([int64]$eDrive.Free -ge $requiredBytes) -and -not $staleArtifacts)
    readyForHostConfig = [bool]($ownership -and $managedPath -and ($switchNames.Count -eq 1) -and ([int64]$eDrive.Free -ge 20GB))
}
if ([string]::IsNullOrWhiteSpace($ReportPath)) { $report | ConvertTo-Json -Depth 8 }
else {
    $parent = Split-Path -Parent $ReportPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $temporary = "$ReportPath.tmp"
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath $temporary -Destination $ReportPath -Force
    $report | ConvertTo-Json -Depth 8
}
