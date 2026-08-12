#Requires -Version 7.0
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string] $InstallRoot = 'C:\ProgramData\EpicVM\agent',
    [string] $VmRoot = 'E:\EpicVM\vms',
    [string] $TemplateManifestPath = 'E:\EpicVM\templates\win11-25h2\manifest.json',
    [string] $ProvisioningStatePath = 'E:\EpicVM\provisioning-jobs.json',
    [Parameter(Mandatory)][string] $SwitchName,
    [string] $TailscaleOAuthClientId,
    [string] $TailscaleTailnet,
    [switch] $EnableGamingProvisioning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-RequiredSetupValue {
    param([string]$Value,[string]$Prompt)
    $candidate = [string]$Value
    if ([string]::IsNullOrWhiteSpace($candidate)) { $candidate = Read-Host $Prompt }
    if ([string]::IsNullOrWhiteSpace($candidate) -or $candidate -match '[\r\n]') { throw ('Invalid ' + $Prompt + '.') }
    return $candidate.Trim()
}

$configPath = Join-Path $InstallRoot 'config.json'
$secretPath = Join-Path $InstallRoot 'tailscale-oauth.dpapi'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'The installed EpicVM agent configuration was not found.' }
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$clientId = Read-RequiredSetupValue -Value $TailscaleOAuthClientId -Prompt 'Tailscale OAuth client ID'
$tailnet = Read-RequiredSetupValue -Value $TailscaleTailnet -Prompt 'Tailscale tailnet'
if ($clientId.Length -gt 256 -or $tailnet.Length -gt 256) { throw 'The Tailscale OAuth values are too long.' }

$config.VmRoot = $VmRoot
$config.TemplateManifestPath = $TemplateManifestPath
$config.ProvisioningStatePath = $ProvisioningStatePath
$config.SwitchName = $SwitchName.Trim()
$config.TailscaleOAuthClientId = $clientId
$config.TailscaleTailnet = $tailnet
$config.TailscaleGuestTag = 'tag:epicvm-guest'
$config.TailscaleOAuthSecretPath = $secretPath
$config.EnableGamingProvisioning = [bool]$EnableGamingProvisioning
$config.SunshineServiceName = 'SunshineService'
$config.SunshineVersion = '2026.516.143833'
$config.SunshineStatePaths = @(
    'C:\Program Files\Sunshine\config\sunshine_state.json',
    'C:\ProgramData\Sunshine\config\sunshine_state.json'
)

$secret = $null
$temporary = $configPath + '.tmp'
try {
    $secret = Read-Host 'Tailscale OAuth client secret (masked)' -AsSecureString
    if ($null -eq $secret) { throw 'No Tailscale OAuth secret was provided.' }
    . (Join-Path $PSScriptRoot '..\remote_agent\windows\providers\GuestProvider.ps1')
    Protect-EpicVMMachineSecret -Secret $secret -Path $secretPath
    $config | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath $temporary -Destination $configPath -Force
}
finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
    $secret = $null
}

Restart-Service -Name 'EpicVMRemoteAgent' -Force -ErrorAction Stop
[ordered]@{ ok = $true; switchConfigured = $true; standardProvisioningConfigured = $true; gamingProvisioningEnabled = [bool]$EnableGamingProvisioning } | ConvertTo-Json -Compress
