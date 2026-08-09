#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string] $InstallRoot = 'C:\ProgramData\EpicVM\agent',
    [string] $VmRoot = 'E:\EpicVM\vms',
    [int] $Port = 8765,
    [string] $TailscaleAddress,
    [string] $SwitchName,
    [switch] $NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-SetupStep {
    param([string] $Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-TailscaleIPv4Addresses {
    $addresses = @()
    $tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($tailscale) {
        try {
            $addresses = @(tailscale ip -4 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        }
        catch { $addresses = @() }
    }
    if ($addresses.Count -eq 0) {
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -match '^100\.(6[4-9]|[7-9][0-9])\.' } |
            Select-Object -ExpandProperty IPAddress)
    }
    return @($addresses | Select-Object -Unique)
}

Write-SetupStep 'Checking prerequisites'
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwsh) {
    throw 'PowerShell 7 (pwsh) is required. Install it, reopen an elevated PowerShell 7 window, and run setup.ps1 again.'
}
if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
    throw 'The Hyper-V PowerShell module is unavailable. Enable Hyper-V, then reopen PowerShell 7 and run setup.ps1 again.'
}
$tailscaleAddresses = @(Get-TailscaleIPv4Addresses)
if ([string]::IsNullOrWhiteSpace($TailscaleAddress)) {
    if ($tailscaleAddresses.Count -eq 1) {
        $TailscaleAddress = [string]$tailscaleAddresses[0]
    }
    elseif ($tailscaleAddresses.Count -gt 1 -and -not $NonInteractive) {
        Write-Host 'Multiple Tailscale IPv4 addresses were found:' -ForegroundColor Yellow
        for ($index = 0; $index -lt $tailscaleAddresses.Count; $index++) {
            Write-Host ("  [{0}] {1}" -f ($index + 1), $tailscaleAddresses[$index])
        }
        $selection = Read-Host 'Choose the address number'
        $selectedIndex = 0
        if (-not [int]::TryParse($selection, [ref]$selectedIndex) -or
            $selectedIndex -lt 1 -or $selectedIndex -gt $tailscaleAddresses.Count) {
            throw 'Invalid Tailscale address selection.'
        }
        $TailscaleAddress = [string]$tailscaleAddresses[$selectedIndex - 1]
    }
}
if ([string]::IsNullOrWhiteSpace($TailscaleAddress)) {
    throw 'No unique Tailscale IPv4 address was found. Reconnect Tailscale or pass -TailscaleAddress 100.x.y.z.'
}

if ([string]::IsNullOrWhiteSpace($SwitchName) -and -not $NonInteractive) {
    $SwitchName = Read-Host 'Hyper-V switch name (leave blank to use no explicit switch)'
}

Write-SetupStep "Installing the EpicVM RemoteVM agent on $TailscaleAddress"
$installer = Join-Path $PSScriptRoot 'install.ps1'
& $pwsh.Source -NoProfile -ExecutionPolicy Bypass -File $installer `
    -InstallRoot $InstallRoot -VmRoot $VmRoot -Port $Port -TailscaleAddress $TailscaleAddress

if (-not [string]::IsNullOrWhiteSpace($SwitchName)) {
    Write-SetupStep "Configuring Hyper-V switch '$SwitchName'"
    $configPath = Join-Path $InstallRoot 'config.json'
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $config.SwitchName = $SwitchName
    $config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath -Encoding UTF8
    Restart-Service -Name EpicVMRemoteAgent -Force
}

Write-Host "`nSetup complete." -ForegroundColor Green
$tokenPath = Join-Path $InstallRoot 'agent.txt'
if (-not (Test-Path -LiteralPath $tokenPath)) {
    $tokenPath = Join-Path $InstallRoot 'agent.token'
}
Write-Host "Protected token file: $tokenPath"
Write-Host 'Next: transfer that protected file privately to the EpicVM server.'
Write-Host 'Then run: sudo epicvm-remote-host setup'
Write-Host 'The server wizard will ask for the host details and probe the agent.'
