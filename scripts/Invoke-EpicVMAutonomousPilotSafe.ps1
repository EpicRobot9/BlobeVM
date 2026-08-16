#Requires -Version 7.0
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9-]{2,31}$')][string]$Name,
    [string]$ConfigPath = 'C:\ProgramData\EpicVM\agent\config.json',
    [string]$CredentialRoot = (Join-Path $env:LOCALAPPDATA 'EpicVM\pilot14-probe'),
    [string]$GuestCredentialPath,
    [string]$SunshineCredentialPath,
    [ValidateSet('Auto','CreateOnly')][string]$Mode = 'Auto',
    [string]$HostId = 'epic-pc',
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$DashboardHost = 'kvm2',
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$DashboardContainer = 'blobedash',
    [string]$ReportPath = (Join-Path $env:TEMP ($Name + '-autonomous.json'))
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'EpicVMAutonomousPilot.ps1')
$result = Invoke-EpicVMAutonomousPilot @PSBoundParameters
$result | ConvertTo-Json -Depth 16 -Compress
if (-not [bool]$result.ok) { exit 1 }
