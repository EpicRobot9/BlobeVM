#Requires -Version 7.0
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$SourcePath=(Join-Path $PSScriptRoot '..\remote_agent\windows\Provisioning.ps1'),
    [string]$InstallRoot='C:\ProgramData\EpicVM\agent',
    [string]$ReportPath=(Join-Path $PSScriptRoot '..\.epicvm-provisioning-patch-safe.json')
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$serviceName='EpicVMRemoteAgent'
$targetPath=Join-Path $InstallRoot 'Provisioning.ps1'
try {
    if(-not(Test-Path -LiteralPath $SourcePath -PathType Leaf)){throw 'Provisioning source is missing.'}
    if(-not(Test-Path -LiteralPath $targetPath -PathType Leaf)){throw 'Installed provisioning script is missing.'}
    $sourceHash=(Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $beforeHash=(Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $service=Get-CimInstance Win32_Service -Filter ("Name='"+$serviceName+"'") -ErrorAction Stop
    if([string]$service.StartName -notin @('LocalSystem','NT AUTHORITY\LocalSystem')){throw 'The agent service identity is not LocalSystem.'}
    $targetAcl=Get-Acl -LiteralPath $targetPath
    if((Get-Service -Name $serviceName -ErrorAction Stop).Status -ne 'Stopped'){Stop-Service -Name $serviceName -Force -ErrorAction Stop}
    Copy-Item -LiteralPath $SourcePath -Destination $targetPath -Force
    Set-Acl -LiteralPath $targetPath -AclObject $targetAcl
    Start-Service -Name $serviceName
    $afterHash=(Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $serviceAfter=Get-Service -Name $serviceName -ErrorAction Stop
    $safe=[ordered]@{ok=($afterHash -eq $sourceHash -and $serviceAfter.Status -eq 'Running');beforeHash=$beforeHash;sourceHash=$sourceHash;installedHash=$afterHash;serviceStatus=[string]$serviceAfter.Status;serviceIdentity=[string]$service.StartName}
    $safe|ConvertTo-Json -Compress|Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $safe|ConvertTo-Json -Compress
} catch {
    [ordered]@{ok=$false;error='provisioning_patch_failed';errorType=$_.Exception.GetType().FullName}|ConvertTo-Json -Compress|Set-Content -LiteralPath $ReportPath -Encoding UTF8
    throw
}
