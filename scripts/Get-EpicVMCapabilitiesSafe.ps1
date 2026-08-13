#Requires -Version 7.0
#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ConfigPath='C:\ProgramData\EpicVM\agent\config.json',[string]$ReportPath=(Join-Path $PSScriptRoot '..\.epicvm-capabilities-safe.json'))
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$token=$null
try {
    $config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $token=(Get-Content -LiteralPath ([string]$config.TokenFile) -Raw -Encoding UTF8).Trim()
    $base='http://'+[string]$config.BindAddress+':'+[string]$config.Port
    $headers=@{Authorization='Bearer '+$token}
    $response=Invoke-RestMethod -Uri ($base+'/v1/capabilities') -Headers $headers -TimeoutSec 10 -ErrorAction Stop
    $checks=$response.provisioningChecks
    $safe=[ordered]@{
        ok=[bool]$response.ok
        provisioning=[bool]$response.provisioning
        gamingProvisioning=[bool]$response.gaming_provisioning
        provisioningChecks=[ordered]@{
            template=[bool]$checks.template
            bootstrapCredential=[bool]$checks.bootstrapCredential
            tailscaleOAuthClient=[bool]$checks.tailscaleOAuthClient
            tailscaleTailnet=[bool]$checks.tailscaleTailnet
            tailscaleOAuthSecret=[bool]$checks.tailscaleOAuthSecret
            gpuPartitionable=[bool]$checks.gpuPartitionable
        }
    }
    $safe | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding UTF8 -NoNewline
    $safe | ConvertTo-Json -Depth 5 -Compress
}
catch {
    $safe=[ordered]@{ok=$false;provisioning=$false;gamingProvisioning=$false;error='capabilities_unavailable'}
    $safe | ConvertTo-Json -Compress | Set-Content -LiteralPath $ReportPath -Encoding UTF8 -NoNewline
    $safe | ConvertTo-Json -Compress
    exit 1
}
finally { $token=$null }
