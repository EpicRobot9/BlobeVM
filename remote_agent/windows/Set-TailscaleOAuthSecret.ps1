#Requires -Version 7.0
<#
    One-time interactive host setup.  The OAuth secret is typed into the
    secure prompt, DPAPI machine-encrypted, and ACL'd to SYSTEM/Administrators.
    Nothing secret is accepted on the command line or printed.
#>
[CmdletBinding()]
param([string]$Path='C:\ProgramData\EpicVM\agent\tailscale-oauth.dpapi')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'providers/GuestProvider.ps1')
$secret=Read-Host 'Tailscale OAuth client secret' -AsSecureString
if($null -eq $secret){throw 'No OAuth secret was provided.'}
Protect-EpicVMMachineSecret -Secret $secret -Path $Path
Write-Output 'Tailscale OAuth secret stored as a DPAPI machine-encrypted file.'
