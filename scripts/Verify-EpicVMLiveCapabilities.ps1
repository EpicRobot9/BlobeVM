#Requires -Version 7.0
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [string] $ConfigPath = 'C:\ProgramData\EpicVM\agent\config.json',
    [string] $ReportPath = (Join-Path $PSScriptRoot '..\.epicvm-live-capabilities.json')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $token = (Get-Content -LiteralPath ([string]$config.TokenFile) -Raw -Encoding UTF8).Trim()
    $headers = @{ Authorization = 'Bearer ' + $token }
    $base = 'http://' + [string]$config.BindAddress + ':' + [string]$config.Port
    $health = Invoke-RestMethod -Uri ($base + '/v1/health') -Headers $headers -TimeoutSec 10
    $capabilities = Invoke-RestMethod -Uri ($base + '/v1/capabilities') -Headers $headers -TimeoutSec 10
    $report = [ordered]@{
        ok = [bool]$health.ok
        provisioning = [bool]$capabilities.provisioning
        gamingProvisioning = [bool]$capabilities.gaming_provisioning
        provisioningChecks = $capabilities.provisioningChecks
        templateReady = if ($capabilities.PSObject.Properties.Name -contains 'templateReady') { [bool]$capabilities.templateReady } else { $null }
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $report | ConvertTo-Json -Depth 8 -Compress
}
catch {
    $errorPath = $ReportPath + '.error.txt'
    ($_ | Format-List * -Force | Out-String) | Set-Content -LiteralPath $errorPath -Encoding UTF8 -NoNewline
    exit 1
}
finally { $token = $null }
