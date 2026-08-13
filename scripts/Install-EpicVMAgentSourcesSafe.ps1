#Requires -Version 7.0
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..\remote_agent\windows'),
    [string]$InstallRoot = 'C:\ProgramData\EpicVM\agent',
    [string]$ReportPath = (Join-Path $PSScriptRoot '..\.epicvm-agent-source-update-safe.json')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$files = @(
    'EpicVM.Agent.ps1',
    'Provisioning.ps1',
    'providers\GuestProvider.ps1',
    'providers\TailscaleProvider.ps1'
)
$serviceName = 'EpicVMRemoteAgent'
$stageRoot = Join-Path $InstallRoot ('.source-update-' + [guid]::NewGuid().ToString('N'))
$backupRoot = Join-Path $stageRoot 'backup'
$stagedRoot = Join-Path $stageRoot 'staged'
$serviceWasRunning = $false
$serviceStarted = $false
$rollbackAttempted = $false
$sourceHashes = [ordered]@{}
$installedHashes = [ordered]@{}
$targetAcls = @{}
$backupFiles = @{}
$safe = $null

function Get-RelativeTarget {
    param([Parameter(Mandatory)][string]$RelativePath)
    $root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\') + '\'
    $target = [IO.Path]::GetFullPath((Join-Path $InstallRoot $RelativePath))
    if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Agent source target escaped the managed install root.'
    }
    return $target
}

function Write-SafeReport {
    param([Parameter(Mandatory)][hashtable]$Value)
    $parent = Split-Path -Parent $ReportPath
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8 -NoNewline
}

try {
    $sourceFull = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\') + '\'
    $installFull = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\') + '\'
    if (-not $sourceFull -or -not $installFull) { throw 'Agent source roots are invalid.' }
    foreach ($relative in $files) {
        $source = Join-Path $SourceRoot $relative
        $target = Get-RelativeTarget -RelativePath $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Agent source file is missing.' }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw 'Installed agent source file is missing.' }
        $sourceHashes[$relative] = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $installedHashes[$relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        $targetAcls[$relative] = Get-Acl -LiteralPath $target
    }

    $service = Get-CimInstance Win32_Service -Filter ("Name='" + $serviceName + "'") -ErrorAction Stop
    if ([string]$service.StartName -notin @('LocalSystem', 'NT AUTHORITY\LocalSystem')) { throw 'The agent service identity is not LocalSystem.' }
    $serviceBefore = Get-Service -Name $serviceName -ErrorAction Stop
    $serviceWasRunning = $serviceBefore.Status -eq 'Running'
    $servicePathBefore = [string]$service.PathName
    $configPath = Join-Path $InstallRoot 'config.json'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'The installed agent configuration is missing.' }
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $tokenPath = [string]$config.TokenFile
    if ([string]::IsNullOrWhiteSpace($tokenPath) -or -not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) { throw 'The protected agent token file is missing.' }

    New-Item -ItemType Directory -Path $backupRoot, $stagedRoot -Force | Out-Null
    foreach ($relative in $files) {
        $source = Join-Path $SourceRoot $relative
        $target = Get-RelativeTarget -RelativePath $relative
        $backup = Join-Path $backupRoot $relative
        $staged = Join-Path $stagedRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup), (Split-Path -Parent $staged) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $backup -Force
        $backupFiles[$relative] = $backup
        Copy-Item -LiteralPath $source -Destination $staged -Force
        Set-Acl -LiteralPath $staged -AclObject $targetAcls[$relative]
    }

    if ($serviceWasRunning) { Stop-Service -Name $serviceName -Force -ErrorAction Stop }
    foreach ($relative in $files) {
        $target = Get-RelativeTarget -RelativePath $relative
        $staged = Join-Path $stagedRoot $relative
        Move-Item -LiteralPath $staged -Destination $target -Force
        Set-Acl -LiteralPath $target -AclObject $targetAcls[$relative]
    }
    Start-Service -Name $serviceName -ErrorAction Stop
    $serviceStarted = $true

    $healthy = $false
    $token = $null
    try {
        $token = (Get-Content -LiteralPath $tokenPath -Raw -Encoding UTF8).Trim()
        $healthUri = 'http://' + [string]$config.BindAddress + ':' + [string]$config.Port + '/v1/health'
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                $health = Invoke-RestMethod -Uri $healthUri -Headers @{ Authorization = 'Bearer ' + $token } -TimeoutSec 2 -ErrorAction Stop
                if ($health.ok -eq $true) { $healthy = $true; break }
            }
            catch { Start-Sleep -Seconds 1 }
        }
    }
    finally { $token = $null }
    if (-not $healthy) { throw 'The updated agent did not pass its safe health check.' }

    foreach ($relative in $files) {
        $target = Get-RelativeTarget -RelativePath $relative
        $installedHashes[$relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $serviceAfter = Get-Service -Name $serviceName -ErrorAction Stop
    $serviceAfterCim = Get-CimInstance Win32_Service -Filter ("Name='" + $serviceName + "'") -ErrorAction Stop
    $safe = [ordered]@{
        ok = (($serviceAfter.Status -eq 'Running') -and ([string]$serviceAfterCim.StartName -in @('LocalSystem', 'NT AUTHORITY\LocalSystem')) -and ($servicePathBefore -eq [string]$serviceAfterCim.PathName) -and $healthy)
        sourceHashes = $sourceHashes
        installedHashes = $installedHashes
        hashesMatch = (@($files | Where-Object { $sourceHashes[$_] -ne $installedHashes[$_] }).Count -eq 0)
        serviceStatus = [string]$serviceAfter.Status
        serviceIdentity = [string]$serviceAfterCim.StartName
        servicePathPreserved = ($servicePathBefore -eq [string]$serviceAfterCim.PathName)
        tokenFilePreserved = (Test-Path -LiteralPath $tokenPath -PathType Leaf)
        healthVerified = $healthy
        rollbackAttempted = $false
    }
    Write-SafeReport -Value $safe
    $safe | ConvertTo-Json -Depth 8 -Compress
}
catch {
    if ($serviceStarted -or $serviceWasRunning) {
        $rollbackAttempted = $true
        try {
            if ((Get-Service -Name $serviceName -ErrorAction Stop).Status -ne 'Stopped') { Stop-Service -Name $serviceName -Force -ErrorAction Stop }
            foreach ($relative in $files) {
                $target = Get-RelativeTarget -RelativePath $relative
                $backup = [string]$backupFiles[$relative]
                if ($backup -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
                    Copy-Item -LiteralPath $backup -Destination $target -Force
                    Set-Acl -LiteralPath $target -AclObject $targetAcls[$relative]
                }
            }
            Start-Service -Name $serviceName -ErrorAction Stop
        }
        catch { }
    }
    $failure = [ordered]@{ ok = $false; error = 'agent_source_update_failed'; rollbackAttempted = $rollbackAttempted; serviceIdentity = [string](Get-CimInstance Win32_Service -Filter ("Name='" + $serviceName + "'") -ErrorAction SilentlyContinue).StartName }
    Write-SafeReport -Value $failure
    $failure | ConvertTo-Json -Compress
    exit 1
}
finally {
    $token = $null
    if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
