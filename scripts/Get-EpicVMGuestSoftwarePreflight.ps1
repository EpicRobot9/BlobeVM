#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('testre','EpicVM-CleanTemplateSource')][string]$VmName,
    [string]$CredentialPath = 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi',
    [string]$CredentialUser = 'EpicVMBootstrap',
    [switch]$Prompt,
    [string]$ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$credential = $null
try {
    if ($Prompt) { $credential = Get-Credential -Message ("Enter the PowerShell Direct credential for {0}." -f $VmName) }
    else {
        . (Join-Path $PSScriptRoot '..\remote_agent\windows\providers\GuestProvider.ps1')
        $credential = Get-EpicVMBootstrapCredential -Path $CredentialPath -Username $CredentialUser
    }
    if ($null -eq $credential) { throw 'A guest credential is required.' }
    $script = {
        $sunshine = Get-CimInstance Win32_Service -Filter "Name='SunshineService'" -ErrorAction SilentlyContinue
        $sunshinePath = if ($null -ne $sunshine) { [string]$sunshine.PathName } else { '' }
        if ($sunshinePath -match '^"([^"]+)"') { $sunshinePath = $Matches[1] } elseif ($sunshinePath -match '^([^ ]+)') { $sunshinePath = $Matches[1] }
        $version = if ($sunshinePath -and (Test-Path -LiteralPath $sunshinePath -PathType Leaf)) { [string]([Diagnostics.FileVersionInfo]::GetVersionInfo($sunshinePath).ProductVersion) } else { '' }
        [ordered]@{
            os = [string](Get-CimInstance Win32_OperatingSystem).Caption
            computerName = [string]$env:COMPUTERNAME
            sunshineServicePresent = $null -ne $sunshine
            sunshineStatus = if ($null -ne $sunshine) { [string]$sunshine.State } else { '' }
            sunshineVersion = $version
            tailscaleInstalled = Test-Path -LiteralPath 'C:\Program Files\Tailscale\tailscale.exe' -PathType Leaf
            sunshineListeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(47989,47990) } | Select-Object -ExpandProperty LocalPort -Unique | Sort-Object)
        }
    }
    $result = Invoke-Command -VMName $VmName -Credential $credential -ScriptBlock $script -ErrorAction Stop
    $json = $result | ConvertTo-Json -Depth 6 -Compress
    if ($ReportPath) {
        $parent = Split-Path -Parent $ReportPath
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        $tmp = "$ReportPath.tmp"
        Set-Content -LiteralPath $tmp -Value $json -Encoding UTF8 -NoNewline
        Move-Item -LiteralPath $tmp -Destination $ReportPath -Force
    }
    else { $json }
}
catch {
    if ($ReportPath) {
        $parent = Split-Path -Parent $ReportPath
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        $tmp = "$ReportPath.tmp"
        Set-Content -LiteralPath $tmp -Value (@{ error = $_.Exception.Message } | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
        Move-Item -LiteralPath $tmp -Destination $ReportPath -Force
        exit 1
    }
    throw
}
finally { $credential = $null }
