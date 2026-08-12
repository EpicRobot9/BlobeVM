#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$InstallerPath = 'C:\Users\Epic\.codex\visualizations\2026\08\02\019fc3f3-fb3f-7361-b1b6-8eed5c67abe2\epicvm-provisioning-integration\.deps\Sunshine-Windows-AMD64-installer.msi',
    [string]$ReportPath = 'C:\Users\Epic\.codex\visualizations\2026\08\02\019fc3f3-fb3f-7361-b1b6-8eed5c67abe2\epicvm-provisioning-integration\.epicvm-clean-source-sunshine.json',
    [string]$CredentialUser = 'EpicVMBootstrap'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$expectedHash = 'e7208b11a4ab9dd89871133a054bbb8dc55dfbba408227b0eccab22c60b273a2'
$destination = 'C:\ProgramData\EpicVM\sunshine-install\Sunshine-Windows-AMD64-installer.msi'
$credential = $null
try {
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw 'Pinned Sunshine installer is missing.' }
    if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Pinned Sunshine installer hash mismatch.' }
    $integration = Get-VMIntegrationService -VMName 'EpicVM-CleanTemplateSource' -Name 'Guest Service Interface' -ErrorAction Stop
    if (-not $integration.Enabled) { Enable-VMIntegrationService -VMName 'EpicVM-CleanTemplateSource' -Name 'Guest Service Interface' }
    $credential = Get-Credential -UserName $CredentialUser -Message 'Enter the current Windows password for EpicVM-CleanTemplateSource. It remains in memory only.'
    if ($null -eq $credential) { throw 'Guest credentials were not provided.' }
    Copy-VMFile -VMName 'EpicVM-CleanTemplateSource' -SourcePath $InstallerPath -DestinationPath $destination -FileSource Host -CreateFullPath -Force
    $result = Invoke-Command -VMName 'EpicVM-CleanTemplateSource' -Credential $credential -ErrorAction Stop -ScriptBlock {
        param($Msi,$ExpectedHash)
        $ErrorActionPreference = 'Stop'
        try {
            if ((Get-FileHash -LiteralPath $Msi -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) { throw 'Guest installer hash mismatch.' }
            $installed = Get-Service -Name 'SunshineService' -ErrorAction SilentlyContinue
            $exitCode = 0
            if ($null -eq $installed) {
                $p = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList @('/i',$Msi,'/qn','/norestart','ALLUSERS=1') -Wait -PassThru
                $exitCode = [int]$p.ExitCode
                if ($exitCode -notin @(0,3010)) { throw "Sunshine MSI failed with exit code $exitCode." }
            }
            Set-Service -Name 'SunshineService' -StartupType Automatic
            if ((Get-Service -Name 'SunshineService').Status -ne 'Running') { Start-Service -Name 'SunshineService' }
            Get-NetFirewallRule -Name 'EpicVM-Sunshine-Tailscale-TCP','EpicVM-Sunshine-Tailscale-UDP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
            New-NetFirewallRule -Name 'EpicVM-Sunshine-Tailscale-TCP' -DisplayName 'EpicVM Sunshine (Tailscale TCP)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 47984,47989,47990,48010 -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block | Out-Null
            New-NetFirewallRule -Name 'EpicVM-Sunshine-Tailscale-UDP' -DisplayName 'EpicVM Sunshine (Tailscale UDP)' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 47998,47999,48000,48002 -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block | Out-Null
            Start-Sleep -Seconds 2
            $service = Get-CimInstance Win32_Service -Filter "Name='SunshineService'" -ErrorAction Stop
            $path = [string]$service.PathName
            if ($path -match '^"([^"]+)"') { $path = $Matches[1] } elseif ($path -match '^([^ ]+)') { $path = $Matches[1] }
            [ordered]@{ ok = $true; service = [string](Get-Service -Name 'SunshineService').Status; version = [string]([Diagnostics.FileVersionInfo]::GetVersionInfo($path).ProductVersion); rebootRequired = ($exitCode -eq 3010) }
        }
        finally { Remove-Item -LiteralPath $Msi -Force -ErrorAction SilentlyContinue }
    } -ArgumentList $destination,$expectedHash
    $json = $result | ConvertTo-Json -Compress
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -LiteralPath "$ReportPath.tmp" -Value $json -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
}
catch {
    $parent = Split-Path -Parent $ReportPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -LiteralPath "$ReportPath.tmp" -Value (@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath "$ReportPath.tmp" -Destination $ReportPath -Force
    exit 1
}
finally { $credential = $null }
