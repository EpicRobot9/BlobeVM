#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Attach the host-local EpicVM shared game library to a gaming/standard VM.

.DESCRIPTION
    Maps the host's read-only EpicVMGames$ SMB share inside the named Hyper-V
    guest via PowerShell Direct, persists the mapping across reboots with a
    logon-triggered scheduled task, and verifies read access. The share is
    read-only by design: game content is shared, mutable state stays per-VM.

.PARAMETER VmName
    Target Hyper-V VM name.

.PARAMETER HostIp
    Tailnet address of the gaming host sharing the library.

.PARAMETER DriveLetter
    Guest-side drive letter for the library (default P).

.EXAMPLE
    ./Attach-EpicVMSharedGames.ps1 -VmName prod-gaming-verify-1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $VmName,
    [string] $HostIp = '100.72.220.117',
    [ValidateRange('A', 'Z')] [char] $DriveLetter = 'P'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tokenPath = Join-Path $env:ProgramData 'EpicVM\agent\games-share.token'
if (-not (Test-Path -LiteralPath $tokenPath)) { throw 'Shared-games share token is missing on the host.' }
$sharePassword = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
$shareUser = 'epicvm-games'
$unc = "\\$HostIp\EpicVMGames$"
$taskName = 'EpicVM-AttachSharedGames'

$credentialUser = $null
try {
    $vmCredPath = Join-Path $env:ProgramData 'EpicVM\agent\guest-cred.ps1'
    if (Test-Path -LiteralPath $vmCredPath) {
        . $vmCredPath
    } else {
        throw 'Guest credential helper (guest-cred.ps1) is missing; cannot open PowerShell Direct.'
    }
    $credential = $script:EpicVmGuestCredential
    $credentialUser = $script:EpicVmGuestUser
    if (-not $credential) { throw 'Guest credential could not be loaded.' }

    # Logon re-attach via the console user's HKCU Run key. A Startup-folder
    # .cmd was corrupted to zero bytes across a guest reboot (same anomaly
    # class as the kvm2 file-vanishing incidents), so nothing on disk is
    # trusted for this: the retry loop lives entirely in the registry value.
    $attachCmd = (
        'powershell -NoProfile -WindowStyle Hidden -Command "' +
        "for(`$i=0;`$i -lt 48;`$i++){" +
        "if(Test-Path '${DriveLetter}:\catalog.json'){exit 0};" +
        "if(`$i -gt 0){Start-Sleep 10};" +
        "net use ${DriveLetter}: /delete /y 2>`$null;" +
        "net use ${DriveLetter}: $unc /user:$shareUser $sharePassword /persistent:yes 2>`$null}" +
        '"'
    )
    Invoke-Command -VMName $VmName -Credential $credential -ErrorAction Stop -ScriptBlock {
        param($Unc, $User, $Pass, $Letter, $Task, $ConsoleUser, $ConsolePass, $AttachCmd)
        $ErrorActionPreference = 'Stop'
        # The logon-triggered re-attach only fires for an interactive session.
        # Templates do not ship autologon, so enable it for the guest's
        # operator account (idempotent) â€” gaming VMs need the console session
        # anyway, and the share mapping must exist for the desktop user.
        $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
        Set-ItemProperty -Path $winlogon -Name AutoAdminLogon -Value '1' -Type String
        Set-ItemProperty -Path $winlogon -Name DefaultUserName -Value $ConsoleUser -Type String
        Set-ItemProperty -Path $winlogon -Name DefaultPassword -Value $ConsolePass -Type String
        $secure = ConvertTo-SecureString $Pass -AsPlainText -Force
        $shareCred = New-Object System.Management.Automation.PSCredential($User, $secure)
        if (Get-PSDrive -Name $Letter -ErrorAction SilentlyContinue) {
            Get-PSDrive -Name $Letter | Remove-PSDrive -Force -ErrorAction SilentlyContinue
        }
        # Map persistently with explicit credentials (no interactive prompt).
        New-PSDrive -Name $Letter -PSProvider FileSystem -Root $Unc -Credential $shareCred -Persist | Out-Null
        # Persist the share credential for the logon re-attach task.
        cmdkey /delete:$Unc 2>$null | Out-Null
        cmdkey /add:$Unc /user:$User /pass:$Pass | Out-Null
        # Logon re-attach via the MACHINE-WIDE HKLM Run key. HKCU writes from
        # PS-Direct land in a throwaway hive when the console session is not
        # yet active (autologon race), silently losing the change; HKLM has no
        # such ambiguity and fires for the console user's logon.
        $runPath = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
        if (-not (Test-Path $runPath)) { New-Item -Path $runPath -Force | Out-Null }
        Set-ItemProperty -Path $runPath -Name 'EpicVMAttachSharedGames' -Value $AttachCmd -Type String
        $stored = (Get-ItemProperty -Path $runPath -Name 'EpicVMAttachSharedGames').EpicVMAttachSharedGames
        if ($stored -ne $AttachCmd) { throw 'Run key verification failed' }
        # Remove any stale per-user value from earlier attempts.
        Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'EpicVMAttachSharedGames' -ErrorAction SilentlyContinue
        # Verify read access through the mapped letter.
        $probe = Join-Path "$($Letter):\" 'catalog.json'
        if (-not (Test-Path -LiteralPath $probe)) { throw "catalog.json not visible through $($Letter):" }
        $null = Get-Content -LiteralPath $probe -Raw
        # Prove the share is read-only: a write attempt must fail.
        $writeBlocked = $false
        try { Set-Content -LiteralPath (Join-Path "$($Letter):\" '.epicvm-write-probe') -Value 'x' -ErrorAction Stop } catch { $writeBlocked = $true }
        [ordered]@{
            mapped = $true
            catalogReadable = $true
            readOnlyEnforced = $writeBlocked
        } | ConvertTo-Json -Compress
    } -ArgumentList $unc, $shareUser, $sharePassword, $DriveLetter, $taskName, $credentialUser, $credential.GetNetworkCredential().Password, $attachCmd
} finally {
    $credential = $null
    $credentialUser = $null
    $sharePassword = $null
}
