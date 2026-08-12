#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ReportPath = 'C:\ProgramData\EpicVM\template-publication-verification.json')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    Import-Module Hyper-V
    $root = 'E:\EpicVM\templates\win11-25h2'
    $image = Join-Path $root 'win11-25h2.vhdx'
    $manifestPath = Join-Path $root 'manifest.json'
    if (-not (Test-Path -LiteralPath $image -PathType Leaf) -or -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Published template files are missing.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $vhd = Get-VHD -Path $image -ErrorAction Stop
    $imageHash = (Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $acl = Get-Acl -LiteralPath 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi'
    $allowed = @($acl.Access | ForEach-Object { [string]$_.IdentityReference.Value })
    $unexpected = @($allowed | Where-Object { $_ -notmatch '(?i)^(NT AUTHORITY\\SYSTEM|BUILTIN\\Administrators|SYSTEM|Administrators)$' })
    $result = [ordered]@{
        ok = $true; root = $root; imageExists = $true; manifestExists = $true
        imageSha256 = $imageHash; manifestSha256 = $manifestHash; manifestImageSha256 = [string]$manifest.sha256
        imageHashMatchesManifest = ($imageHash -ieq [string]$manifest.sha256)
        readonlyImage = [bool](Get-ItemPropertyValue -LiteralPath $image -Name IsReadOnly)
        readonlyManifest = [bool](Get-ItemPropertyValue -LiteralPath $manifestPath -Name IsReadOnly)
        vhdType = [string]$vhd.VhdType; parentPath = [string]$vhd.ParentPath
        standalone = [string]::IsNullOrWhiteSpace([string]$vhd.ParentPath); fullCopy = [bool]$manifest.fullCopy; immutable = [bool]$manifest.immutable
        sunshine = [string]$manifest.sunshine; sunshineVersion = [string]$manifest.sunshineVersion; sourceVm = [string]$manifest.sourceVm
        bootstrapExists = Test-Path -LiteralPath 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi' -PathType Leaf
        bootstrapUnexpectedAcl = $unexpected
    }
    $parent=Split-Path -Parent $ReportPath
    if($parent){New-Item -ItemType Directory -Path $parent -Force|Out-Null}
    Set-Content -LiteralPath "$ReportPath.tmp" -Value ($result|ConvertTo-Json -Depth 8 -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
}
catch {
    $parent=Split-Path -Parent $ReportPath
    if($parent){New-Item -ItemType Directory -Path $parent -Force|Out-Null}
    Set-Content -LiteralPath "$ReportPath.tmp" -Value (@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
    exit 1
}
