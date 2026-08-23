[CmdletBinding()]
param([string]$VmName='epicvm-pilot-01',[string]$ReportPath=(Join-Path $PSScriptRoot '..\.epicvm-pilot-capabilities-safe.json'))
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$sealed=$null;$bytes=$null;$plain=$null;$credential=$null
try {
    $config=Get-Content -LiteralPath 'C:\ProgramData\EpicVM\agent\config.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    $sealed=[IO.File]::ReadAllBytes([string]$config.BootstrapCredentialPath)
    $bytes=[Security.Cryptography.ProtectedData]::Unprotect($sealed,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    $plain=[Text.Encoding]::UTF8.GetString($bytes)
    $credential=[PSCredential]::new([string]$config.BootstrapUser,(ConvertTo-SecureString $plain -AsPlainText -Force))
    $safe=Invoke-Command -VMName $VmName -Credential $credential -ScriptBlock {
        $out=[ordered]@{connected=$true}
        try{$os=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop;$out.edition=[string]$os.Caption;$out.build=[string]$os.BuildNumber}catch{$out.osReadable=$false}
        try{$null=Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop;$out.adminGroupReadable=$true}catch{$out.adminGroupReadable=$false;$out.adminGroupError=$_.Exception.GetType().FullName}
        try{$rdp=Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -ErrorAction Stop;$nla=Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -ErrorAction Stop;$out.rdpEnabled=([int]$rdp.fDenyTSConnections -eq 0);$out.nlaEnabled=([int]$nla.UserAuthentication -eq 1)}catch{$out.registryReadable=$false;$out.registryError=$_.Exception.GetType().FullName}
        $out.localUserCmd=[bool](Get-Command Get-LocalUser -ErrorAction SilentlyContinue)
        $out.localUserSetCmd=[bool](Get-Command Set-LocalUser -ErrorAction SilentlyContinue)
        $out.firewallReadCmd=[bool](Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)
        $out.firewallWriteCmd=[bool](Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)
        $out.serviceCmd=[bool](Get-Command Set-Service -ErrorAction SilentlyContinue)
        $out.termService=[string](Get-Service -Name TermService -ErrorAction SilentlyContinue).Status
        $out|ConvertTo-Json -Compress
    } -ErrorAction Stop
    $safe|Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $safe
} catch {
    [ordered]@{ok=$false;connected=$false;errorType=$_.Exception.GetType().FullName}|ConvertTo-Json -Compress|Set-Content -LiteralPath $ReportPath -Encoding UTF8
    exit 1
} finally {
    $plain=$null;$credential=$null
    if($null -ne $bytes){[Array]::Clear($bytes,0,$bytes.Length)}
    if($null -ne $sealed){[Array]::Clear($sealed,0,$sealed.Length)}
}
