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
    [string] $BootstrapCredentialPath = 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi',
    [string] $SunshineVersion = '2026.516.143833',
    [switch] $UseCleanTemplateSource,
    [string] $CleanTemplateSourceRoot = 'E:\EpicVM\clean-template-source'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Credentials are collected by the elevated console, used in memory by the
# builder, and cleared before this wrapper exits. Nothing credential-bearing is
# included in the child process arguments or output.
$guestCredential = $null
$bootstrapCredential = $null
try {
    $guestCredential = Get-Credential -Message (if ($UseCleanTemplateSource) { 'Enter the current EpicVM-CleanTemplateSource Windows credential.' } else { 'Enter the current testre Windows credential.' })
    $bootstrapCredential = Get-Credential -UserName $BootstrapUser -Message 'Enter a new EpicVMBootstrap credential for the golden template.'
    if ($null -eq $guestCredential -or $null -eq $bootstrapCredential) { throw 'Both interactive credentials are required.' }

    $builder = Join-Path $PSScriptRoot '..\remote_agent\windows\TemplateBuilder.ps1'
    & $builder -Run -SourceVmName $SourceVmName -VmRoot $VmRoot -TemplateRoot $TemplateRoot -WorkingRoot $WorkingRoot -PrivateSwitchName $PrivateSwitchName -BuilderVmName $BuilderVmName -BootstrapUser $BootstrapUser -BootstrapCredentialPath $BootstrapCredentialPath -SunshineVersion $SunshineVersion -UseCleanTemplateSource:$UseCleanTemplateSource -CleanTemplateSourceRoot $CleanTemplateSourceRoot -GuestCredential $guestCredential -BootstrapCredential $bootstrapCredential
}
catch {
    $code = if ($_.Exception.PSObject.Properties.Name -contains 'ErrorCode') { [string]$_.Exception.ErrorCode } else { 'template_build_failed' }
    Write-Error ('Template builder failed: ' + $code + '. ' + [string]$_.Exception.Message)
    exit 1
}
finally {
    $guestCredential = $null
    $bootstrapCredential = $null
}
