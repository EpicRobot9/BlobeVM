#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ReportPath = 'C:\Users\Epic\.codex\visualizations\2026\08\02\019fc3f3-fb3f-7361-b1b6-8eed5c67abe2\epicvm-provisioning-integration\.epicvm-template-quarantine.json')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$source = 'E:\EpicVM\templates\win11-25h2'
$quarantineRoot = 'E:\EpicVM\templates\quarantine'
try {
    $sourceFull = [IO.Path]::GetFullPath($source).TrimEnd('\')
    if ($sourceFull -ine 'E:\EpicVM\templates\win11-25h2' -or -not (Test-Path -LiteralPath $sourceFull -PathType Container)) { throw 'The exact invalid template directory was not found.' }
    $manifestPath = Join-Path $sourceFull 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'The template manifest is missing.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $sunshineValue = if ($manifest.PSObject.Properties['sunshine']) { [string]$manifest.sunshine } else { '' }
    if ($sunshineValue -ieq 'installed') { throw 'The existing template is already Sunshine-ready; refusing to move it.' }
    $quarantine = Join-Path $quarantineRoot ('win11-25h2-invalid-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
    New-Item -ItemType Directory -Path $quarantineRoot -Force | Out-Null
    Move-Item -LiteralPath $sourceFull -Destination $quarantine -Force
    $result = [ordered]@{ ok = $true; source = $sourceFull; quarantine = $quarantine; reason = 'manifest_sunshine_not_installed' }
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
