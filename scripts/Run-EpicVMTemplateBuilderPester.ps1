#Requires -Version 7.0
#Requires -RunAsAdministrator

[CmdletBinding()]
param([string]$ReportPath = (Join-Path $PSScriptRoot '..\.pester-template-release.json'))

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:PSModulePath = (Join-Path $root '.deps\powershell') + ';' + $env:PSModulePath
Import-Module Pester -MinimumVersion 5.7.1 -Force
$result = Invoke-Pester -Path (Join-Path $root 'remote_agent\windows\tests\TemplateBuilder.Tests.ps1') -PassThru -Output Detailed
[ordered]@{ passed = $result.PassedCount; failed = $result.FailedCount; skipped = $result.SkippedCount } | ConvertTo-Json -Compress | Set-Content -LiteralPath $ReportPath -Encoding UTF8
if ($result.FailedCount -ne 0) { exit 1 }
