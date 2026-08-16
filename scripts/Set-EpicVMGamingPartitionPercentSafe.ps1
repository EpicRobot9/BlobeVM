#Requires -Version 7.0
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$Name,
    [Parameter(Mandatory=$true)]
    [ValidateRange(1,100)]
    [int]$Percent,
    [string]$ConfigPath='C:\ProgramData\EpicVM\agent\config.json',
    [string]$ReportPath=(Join-Path $PSScriptRoot '..\.epicvm-gaming-partition-response.json')
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$token=$null
$body=$null
$json=$null
try {
    if($Name -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$'){ throw 'Invalid VM name.' }
    $config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $token=(Get-Content -LiteralPath ([string]$config.TokenFile) -Raw -Encoding UTF8).Trim()
    $headers=@{Authorization='Bearer '+$token}
    $base='http://'+[string]$config.BindAddress+':'+[string]$config.Port
    $body=(@{percent=$Percent} | ConvertTo-Json -Compress)
    $safeName=[uri]::EscapeDataString($Name)
    $response=Invoke-WebRequest -Uri ($base+'/v1/vms/'+$safeName+'/gpu-partition') -Method Post -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 120 -SkipHttpErrorCheck
    $json=$response.Content | ConvertFrom-Json
    $ok=($response.StatusCode -ge 200 -and $response.StatusCode -lt 300 -and [bool]$json.ok)
    $safe=[ordered]@{
        ok=$ok
        status=[int]$response.StatusCode
        name=$Name
        percent=$Percent
        error=if(-not $ok){'GPU-P partition update failed.'}else{$null}
    }
    $safe|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $safe|ConvertTo-Json -Depth 8 -Compress
    if(-not $ok){ exit 1 }
} catch {
    $safe=[ordered]@{ok=$false;status=$null;name=$Name;percent=$Percent;errorType=$_.Exception.GetType().FullName;error='request_failed'}
    $safe|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $safe|ConvertTo-Json -Compress
    exit 1
} finally {
    $token=$null
    $body=$null
    $json=$null
}
