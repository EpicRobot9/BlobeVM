#Requires -RunAsAdministrator
#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ReportPath = 'C:\Users\Epic\.codex\visualizations\2026\08\02\019fc3f3-fb3f-7361-b1b6-8eed5c67abe2\epicvm-provisioning-integration\.epicvm-template-publish.json',
    [string]$BootstrapCredentialPath = 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi',
    [string]$BootstrapUser = 'EpicVMBootstrap'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$credential = $null
$stageRoot = $null
try {
    Import-Module Hyper-V
    $vm = Get-VM -Name 'EpicVM-CleanTemplateSource' -ErrorAction Stop
    if ([string]$vm.State -ne 'Off') { throw 'Clean source must be Off before publication.' }
    if ([string]$vm.Notes -notmatch '(?i)EpicVM-CleanTemplateSource\s*:\s*true') { throw 'Clean-source ownership marker did not match.' }
    if ([IO.Path]::GetFullPath([string]$vm.Path).TrimEnd('\') -ine 'E:\EpicVM\clean-template-source\EpicVM-CleanTemplateSource') { throw 'Clean-source VM path did not match the managed fallback root.' }
    $drives = @(Get-VMHardDiskDrive -VM $vm -ErrorAction Stop)
    if ($drives.Count -ne 1) { throw 'Clean source must have exactly one attached disk.' }
    $sourceDisk = [IO.Path]::GetFullPath([string]$drives[0].Path)
    if (-not $sourceDisk.StartsWith('E:\EpicVM\clean-template-source\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Clean-source disk is outside the managed fallback root.' }
    $sourceVhd = Get-VHD -Path $sourceDisk -ErrorAction Stop
    if ([string]$sourceVhd.VhdType -ine 'Dynamic' -or -not [string]::IsNullOrWhiteSpace([string]$sourceVhd.ParentPath)) { throw 'Clean-source disk is not standalone Dynamic.' }
    $credential = Get-Credential -UserName $BootstrapUser -Message 'Enter the EpicVMBootstrap password used in the clean source. It remains in memory only.'
    if ($null -eq $credential) { throw 'Bootstrap credential was not provided.' }

    $templateRoot = 'E:\EpicVM\templates'
    $finalRoot = Join-Path $templateRoot 'win11-25h2'
    if (Test-Path -LiteralPath $finalRoot) { throw 'The immutable template directory already exists.' }
    $stageRoot = Join-Path $templateRoot ('.win11-25h2-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    $imagePath = Join-Path $stageRoot 'win11-25h2.vhdx'
    Convert-VHD -Path $sourceDisk -DestinationPath $imagePath -VHDType Dynamic -ErrorAction Stop | Out-Null
    $publishedVhd = Get-VHD -Path $imagePath -ErrorAction Stop
    if ([string]$publishedVhd.VhdType -ine 'Dynamic' -or -not [string]::IsNullOrWhiteSpace([string]$publishedVhd.ParentPath)) { throw 'Published disk failed the standalone Dynamic gate.' }
    $imageHash = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest = [ordered]@{
        templateVersion='1.0.0'; name='win11-25h2'; build=('win11-25h2-' + [DateTime]::UtcNow.ToString('yyyyMMdd'))
        windowsBuild='Windows 11 Pro 25H2'; sha256=$imageHash; imagePath=(Join-Path $finalRoot 'win11-25h2.vhdx')
        bootstrap='machine-dpapi-encrypted-system-admin'; sunshine='installed'; sunshineVersion='2026.516.143833'
        sunshineService='SunshineService'; sunshineCredentials='request-only'; tailscale='installed-logged-out'
        gpu='none'; gpuPartition='none'; diskType='Dynamic'; sourceVm='EpicVM-CleanTemplateSource'
        # The source must be generalized without entering interactive OOBE so
        # the protected bootstrap account can authenticate on first clone boot.
        sysprep='/generalize /shutdown /mode:vm'; network='private-switch'; fullCopy=$true; immutable=$true
        sanitation='manual-bitlocker-off;sunshine-credentials-cleared;tailscale-identity-cleared;sysprep-generalize'; createdAt=[DateTime]::UtcNow.ToString('o')
    }
    $manifestPath = Join-Path $stageRoot 'manifest.json'
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8 -NoNewline
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Move-Item -LiteralPath $stageRoot -Destination $finalRoot -Force
    Set-ItemProperty -LiteralPath (Join-Path $finalRoot 'win11-25h2.vhdx') -Name IsReadOnly -Value $true
    Set-ItemProperty -LiteralPath (Join-Path $finalRoot 'manifest.json') -Name IsReadOnly -Value $true

    . (Join-Path $PSScriptRoot '..\remote_agent\windows\TemplateBuilder.ps1')
    Protect-EpicVMTemplateBootstrapSecret -Credential $credential -Path $BootstrapCredentialPath

    $result = [ordered]@{ok=$true; templatePath=$finalRoot; imageSha256=$imageHash; manifestSha256=$manifestHash; sourceVm='EpicVM-CleanTemplateSource'; sourceDisk=$sourceDisk}
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
finally { $credential = $null; if ($stageRoot -and (Test-Path -LiteralPath $stageRoot)) { Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue } }
