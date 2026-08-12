#Requires -RunAsAdministrator

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$vm = Get-VM -Name 'testre' -ErrorAction Stop
if ([string]$vm.Notes -notmatch '(?i)EpicVM-Managed\s*:\s*true' -or [IO.Path]::GetFullPath([string]$vm.Path).TrimEnd('\') -ine 'E:\EpicVM\vms\testre') { throw 'testre failed the ownership/path gate.' }
if ([string]$vm.State -ieq 'Off') { Start-VM -Name 'testre' -ErrorAction Stop | Out-Null }
$deadline = [DateTime]::UtcNow.AddMinutes(3)
do {
    Start-Sleep -Seconds 2
    $vm = Get-VM -Name 'testre' -ErrorAction Stop
} while ([string]$vm.State -ine 'Running' -and [DateTime]::UtcNow -lt $deadline)
if ([string]$vm.State -ine 'Running') { throw 'testre did not reach Running state.' }
[ordered]@{ ok = $true; name = 'testre'; state = [string]$vm.State } | ConvertTo-Json -Compress
