#Requires -Version 7.0
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string] $SourceVmName = 'testre',
    [string] $VmRoot = 'E:\EpicVM\vms',
    [string] $TemplateRoot = 'E:\EpicVM\templates',
    [string] $WorkingRoot = 'E:\EpicVM\template-work',
    [string] $PrivateSwitchName = 'EpicVM-Template-Private',
    [string] $BuilderVmName = 'EpicVM-TemplateBuilder',
    [string] $BootstrapUser = 'EpicVMBootstrap',
    [string] $SourceCredentialUser = 'EpicVMBootstrap',
    [string] $BootstrapCredentialPath = 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi',
    [string] $SunshineVersion = '2026.516.143833',
    [switch] $UseCleanTemplateSource,
    [string] $CleanTemplateSourceRoot = 'E:\EpicVM\clean-template-source',
    [string] $ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Credentials are collected by the elevated console, used in memory by the
# builder, and cleared before this wrapper exits. Nothing credential-bearing is
# included in the child process arguments or output.
$guestCredential = $null
$bootstrapCredential = $null
try {
    $guestMessage = if ($UseCleanTemplateSource) { 'Enter the current EpicVM-CleanTemplateSource password.' } else { 'Enter the current testre password.' }
    $guestCredential = Get-Credential -UserName $SourceCredentialUser -Message $guestMessage
    $bootstrapCredential = Get-Credential -UserName $BootstrapUser -Message 'Enter a new EpicVMBootstrap credential for the golden template.'
    if ($null -eq $guestCredential -or $null -eq $bootstrapCredential) { throw 'Both interactive credentials are required.' }

    $builder = Join-Path $PSScriptRoot '..\remote_agent\windows\TemplateBuilder.ps1'
    $result = & $builder -Run -SourceVmName $SourceVmName -VmRoot $VmRoot -TemplateRoot $TemplateRoot -WorkingRoot $WorkingRoot -PrivateSwitchName $PrivateSwitchName -BuilderVmName $BuilderVmName -BootstrapUser $BootstrapUser -BootstrapCredentialPath $BootstrapCredentialPath -SunshineVersion $SunshineVersion -UseCleanTemplateSource:$UseCleanTemplateSource -CleanTemplateSourceRoot $CleanTemplateSourceRoot -GuestCredential $guestCredential -BootstrapCredential $bootstrapCredential
    if ($ReportPath) {
        $parent = Split-Path -Parent $ReportPath
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Set-Content -LiteralPath "$ReportPath.tmp" -Value (($result | Select-Object -Last 1 | ConvertTo-Json -Depth 8 -Compress)) -Encoding UTF8 -NoNewline
        Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
    } else { $result }
}
catch {
    $code = if ($_.Exception.PSObject.Properties.Name -contains 'ErrorCode') { [string]$_.Exception.ErrorCode } else { 'template_build_failed' }
    if ($ReportPath) {
        $parent = Split-Path -Parent $ReportPath
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        $safeMessage = ([string]$_.Exception.Message) -replace '(?i)(password|secret|token|credential)\s*[^\r\n]{0,120}', '$1=<redacted>'
        Set-Content -LiteralPath "$ReportPath.tmp" -Value (@{ok=$false;error=$code;message=$safeMessage} | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
        Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
    } else { Write-Error ('Template builder failed: ' + $code + '. ' + [string]$_.Exception.Message) }
    exit 1
}
finally {
    $guestCredential = $null
    $bootstrapCredential = $null
}
