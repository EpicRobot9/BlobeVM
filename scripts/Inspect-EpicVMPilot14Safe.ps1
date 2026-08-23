#Requires -Version 7.0
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$ConfigPath = 'C:\ProgramData\EpicVM\agent\config.json',
    [string]$ReportPath = (Join-Path $PSScriptRoot '..\.epicvm-pilot14-safe-state.json')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$token = $null
try {
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $token = (Get-Content -LiteralPath ([string]$config.TokenFile) -Raw -Encoding UTF8).Trim()
    $base = 'http://' + [string]$config.BindAddress + ':' + [string]$config.Port
    $headers = @{ Authorization = 'Bearer ' + $token }
    $job = Invoke-RestMethod -Uri ($base + '/v1/provisioning-jobs/b74b0d36293f47fdb757dbd602b39fbf') -Headers $headers -TimeoutSec 20
    $record = $job.job
    $safe = [ordered]@{
        ok = [bool]$job.ok
        state = [string]$record.state
        errorCode = [string]$record.errorCode
        failureStage = [string]$record.failureStage
        claimConsumed = [bool]$record.claimConsumed
        operationIdPresent = -not [string]::IsNullOrWhiteSpace([string]$record.operationId)
        tailnetIpPresent = -not [string]::IsNullOrWhiteSpace([string]$record.tailnetIp)
        tailnetDevicePresent = -not [string]::IsNullOrWhiteSpace([string]$record.tailnetDeviceId)
        managementTransport = [string]$record.managementTransport
        managementReadyAtPresent = -not [string]::IsNullOrWhiteSpace([string]$record.managementReadyAt)
        completedStages = @($record.completedStages | ForEach-Object { [string]$_ })
        consoleRoutePrefix = [string]$record.consoleRoutePrefix
        consoleVerifiedAtPresent = -not [string]::IsNullOrWhiteSpace([string]$record.consoleVerifiedAt)
        updatedAt = [string]$record.updatedAt
    }
    $json = $safe | ConvertTo-Json -Depth 8
    $json | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $safe | ConvertTo-Json -Depth 8 -Compress
} catch {
    $safe = [ordered]@{ ok = $false; errorType = $_.Exception.GetType().FullName }
    $safe | ConvertTo-Json -Compress | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    exit 1
} finally {
    $token = $null
}
