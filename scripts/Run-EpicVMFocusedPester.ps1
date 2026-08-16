#Requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ReportPath)
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$env:PSModulePath=(Join-Path $repo '.deps\powershell')+';'+$env:PSModulePath
Import-Module (Join-Path $repo '.deps\powershell\Pester\5.7.1\Pester.psd1')
try {
    $result=Invoke-Pester -Path (Join-Path $repo 'remote_agent\windows\tests\GuestProvider.Tests.ps1'),(Join-Path $repo 'remote_agent\windows\tests\TailscaleProvider.Tests.ps1'),(Join-Path $repo 'remote_agent\windows\tests\Provisioning.Tests.ps1') -Output None -PassThru
    [ordered]@{ok=($result.Failed -eq 0);passed=[int]$result.Passed;failed=[int]$result.Failed;skipped=[int]$result.Skipped;total=[int]$result.TotalCount}|ConvertTo-Json -Compress|Set-Content -LiteralPath $ReportPath -Encoding UTF8 -NoNewline
    if($result.Failed -gt 0){exit 1}
}
catch {
    [ordered]@{ok=$false;code='pester_runner_failed'}|ConvertTo-Json -Compress|Set-Content -LiteralPath $ReportPath -Encoding UTF8 -NoNewline
    exit 1
}
