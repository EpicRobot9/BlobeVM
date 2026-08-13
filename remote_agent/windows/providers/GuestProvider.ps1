#Requires -Version 7.0
<# Injectable PowerShell Direct guest configuration provider. #>

Set-StrictMode -Version Latest

function Protect-EpicVMMachineSecret {
    param([Parameter(Mandatory)][SecureString]$Secret,[Parameter(Mandatory)][string]$Path)
    $ptr=[IntPtr]::Zero; $bytes=$null
    try {
        $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
        $plain=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        $bytes=[Text.Encoding]::UTF8.GetBytes($plain)
        $sealed=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        $parent=Split-Path -Parent $Path
        if(-not(Test-Path -LiteralPath $parent)){New-Item -ItemType Directory -Path $parent -Force|Out-Null}
        [IO.File]::WriteAllBytes($Path,$sealed)
        $acl=Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true,$false)
        foreach($rule in @($acl.Access)){[void]$acl.RemoveAccessRule($rule)}
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new('SYSTEM','Read','Allow'))
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new('Administrators','Read','Allow'))
        Set-Acl -LiteralPath $Path -AclObject $acl
    } finally {
        if($null -ne $bytes){[Array]::Clear($bytes,0,$bytes.Length)}
        if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
        $plain=$null
    }
}

function Get-EpicVMBootstrapCredential {
    param([Parameter(Mandatory)][string]$Path,[Parameter(Mandatory)][string]$Username)
    if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){throw 'The DPAPI bootstrap credential is unavailable.'}
    $sealed=[IO.File]::ReadAllBytes($Path); $bytes=$null; $secure=$null
    try {
        $bytes=[Security.Cryptography.ProtectedData]::Unprotect($sealed,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        $plain=[Text.Encoding]::UTF8.GetString($bytes)
        $secure=ConvertTo-SecureString $plain -AsPlainText -Force
        return [PSCredential]::new($Username,$secure)
    } catch { throw 'The DPAPI bootstrap credential could not be opened.' }
    finally {
        if($null -ne $sealed){[Array]::Clear($sealed,0,$sealed.Length)}
        if($null -ne $bytes){[Array]::Clear($bytes,0,$bytes.Length)}
        $plain=$null
    }
}

function Invoke-EpicVMPowerShellDirect {
    param(
        [Parameter(Mandatory)][object]$Provider,
        [Parameter(Mandatory)][string]$VmName,
        [Parameter(Mandatory)][PSCredential]$Credential,
        [Parameter(Mandatory)][scriptblock]$Script,
        [AllowNull()][object[]]$ArgumentList=@()
    )
    $invoker=Get-EpicVMHyperVValue -Object $Provider -Name 'PowerShellDirectInvoker' -Default $null
    if($null -ne $invoker){return & $invoker $VmName $Credential $Script $ArgumentList}
    # The Hyper-V VMName parameter set does not accept SessionOption. Run the
    # command as a bounded background job instead, so a guest still booting in
    # OOBE cannot block the agent worker indefinitely.
    $guestJob=$null
    try {
        $guestJob=Invoke-Command -VMName $VmName -Credential $Credential -ScriptBlock $Script -ArgumentList $ArgumentList -AsJob -ErrorAction Stop
        $completedJob=Wait-Job -Job $guestJob -Timeout 5
        if($null -eq $completedJob){
            Stop-Job -Job $guestJob -ErrorAction SilentlyContinue
            throw 'PowerShell Direct probe timed out.'
        }
        return Receive-Job -Job $guestJob -ErrorAction Stop
    } finally {
        if($null -ne $guestJob){Remove-Job -Job $guestJob -Force -ErrorAction SilentlyContinue}
    }
}

function Get-EpicVMGuestProviderErrorCode {
    param([AllowNull()][object]$ErrorRecord)

    # Never return exception text to the API.  These stable classes are enough
    # to tell the operator which trust boundary failed without exposing guest
    # names, paths, or credential-bearing transport details.
    $message = [string](Get-EpicVMHyperVValue -Object $ErrorRecord -Name 'Exception' -Default $ErrorRecord)
    if($message -match '(?i)RDP/NLA/firewall|firewall verification|guest account could not be placed'){
        return 'rdp_verification_failed'
    }
    if($message -match '(?i)PowerShell Direct|PSRemoting|WinRM|logon failure|access is denied|cannot connect|connection'){
        return 'powershell_direct_failed'
    }
    return 'guest_configuration_failed'
}

function Get-EpicVMGuestBootstrapReadinessScript {
    return {
        $ErrorActionPreference='Stop'
        $os=Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        [ordered]@{ok=$null -ne $os; powershellDirect=$true}
    }
}

function Test-EpicVMGuestBootstrapReady {
    param(
        [Parameter(Mandatory)][object]$Provider,
        [Parameter(Mandatory)][object]$Config,
        [Parameter(Mandatory)][string]$VmName
    )
    $credential=$null
    try {
        $loader=Get-EpicVMHyperVValue -Object $Provider -Name 'BootstrapCredentialLoader' -Default $null
        $path=[string](Get-EpicVMHyperVValue -Object $Config -Name 'BootstrapCredentialPath' -Default 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi')
        $user=[string](Get-EpicVMHyperVValue -Object $Config -Name 'BootstrapUser' -Default 'EpicVMBootstrap')
        $credential=if($null -ne $loader){& $loader $path $user}else{Get-EpicVMBootstrapCredential -Path $path -Username $user}
        $result=Invoke-EpicVMPowerShellDirect -Provider $Provider -VmName $VmName -Credential $credential -Script (Get-EpicVMGuestBootstrapReadinessScript)
        return [bool](Get-EpicVMHyperVValue -Object $result -Name 'ok' -Default $false)
    } catch { return $false }
    finally { $credential=$null }
}

function Wait-EpicVMGuestBootstrapReady {
    param(
        [Parameter(Mandatory)][object]$Provider,
        [Parameter(Mandatory)][object]$Config,
        [Parameter(Mandatory)][string]$VmName,
        [int]$TimeoutSeconds=180,
        [int]$PollMilliseconds=1000
    )
    $deadline=[DateTime]::UtcNow.AddSeconds([Math]::Max(1,$TimeoutSeconds))
    do {
        if(Test-EpicVMGuestBootstrapReady -Provider $Provider -Config $Config -VmName $VmName){ return $true }
        if([DateTime]::UtcNow -lt $deadline){ Start-Sleep -Milliseconds ([Math]::Max(100,$PollMilliseconds)) }
    } while([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Get-EpicVMGuestConfigurationScript {
    return {
        param($DesiredUser,$DesiredPassword,$BootstrapUser,$BootstrapCredentialPath)
        $ErrorActionPreference='Stop'
        if($DesiredUser -notmatch '^[A-Za-z][A-Za-z0-9._-]{2,31}$'){throw 'Invalid desired user.'}
        if([string]::IsNullOrEmpty([string]$DesiredPassword)){throw 'Invalid desired password.'}
        $secure=ConvertTo-SecureString $DesiredPassword -AsPlainText -Force
        $user=Get-LocalUser -Name $DesiredUser -ErrorAction SilentlyContinue
        if($null -eq $user){New-LocalUser -Name $DesiredUser -Password $secure -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword|Out-Null}
        else{Set-LocalUser -Name $DesiredUser -Password $secure -AccountNeverExpires -PasswordNeverExpires}
        $adminOk=$false
        try { Add-LocalGroupMember -Group 'Administrators' -Member $DesiredUser -ErrorAction Stop } catch { }
        try {
            $adminMembers=@(Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop | ForEach-Object { [string]$_.Name.Split('\')[-1] })
            $adminOk=$adminMembers -contains $DesiredUser
        } catch { }
        # Some Windows images expose the built-in group through a localized
        # alias or return an unresolved member from Get-LocalGroupMember.  The
        # native localgroup command is a narrow, username-only fallback.
        if(-not $adminOk){
            try { & "$env:SystemRoot\System32\net.exe" localgroup Administrators $DesiredUser /add | Out-Null } catch { }
            try {
                $groupText=@(& "$env:SystemRoot\System32\net.exe" localgroup Administrators 2>$null)
                $adminOk=[bool](@($groupText | Where-Object { [string]$_ -match [regex]::Escape($DesiredUser) }))
            } catch { }
        }
        if(-not $adminOk){throw 'Guest account could not be placed in Administrators.'}
        New-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -PropertyType DWord -Value 0 -Force | Out-Null
        New-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -PropertyType DWord -Value 1 -Force | Out-Null
        Set-Service -Name TermService -StartupType Automatic
        Start-Service -Name TermService
        Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue | Disable-NetFirewallRule -ErrorAction SilentlyContinue
        Get-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -DisplayName 'EpicVM RDP (Tailscale only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3389 -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block | Out-Null
        $listener=$null
        $listenerDeadline=[DateTime]::UtcNow.AddSeconds(20)
        do {
            $listener=Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction SilentlyContinue
            if($null -ne $listener){break}
            Start-Sleep -Milliseconds 500
        } while([DateTime]::UtcNow -lt $listenerDeadline)
        $rule=Get-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -ErrorAction SilentlyContinue
        $addressFilter=if($null -ne $rule){Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue}else{$null}
        $nlaOk=(Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -ErrorAction Stop).UserAuthentication -eq 1
        $portOk=$null -ne $listener
        $expectedScopes=@('100.64.0.0/10','100.64.0.0/255.192.0.0')
        $ruleOk=$null -ne $rule -and [string]$rule.Direction -eq 'Inbound' -and [string]$rule.Action -eq 'Allow' -and $null -ne $addressFilter -and [bool](@($addressFilter.RemoteAddress)|Where-Object { $expectedScopes -contains [string]$_ })
        if(-not $adminOk -or -not $portOk -or -not $ruleOk -or -not $nlaOk){throw 'RDP/NLA/firewall verification failed.'}
        if($BootstrapUser -and $BootstrapUser -cne $DesiredUser){
            Remove-LocalUser -Name $BootstrapUser -ErrorAction SilentlyContinue
            if($BootstrapCredentialPath){Remove-Item -LiteralPath $BootstrapCredentialPath -Force -ErrorAction SilentlyContinue}
        }
        [ordered]@{ok=$true;adminConfigured=$adminOk;listener=$portOk;nla=$nlaOk;firewallScoped=$ruleOk;bootstrapRemoved=($BootstrapUser -cne $DesiredUser)}
    }
}

function Invoke-EpicVMGuestConfiguration {
    param(
        [Parameter(Mandatory)][object]$Provider,
        [Parameter(Mandatory)][object]$Config,
        [Parameter(Mandatory)][string]$VmName,
        [Parameter(Mandatory)][string]$DesiredUser,
        [Parameter(Mandatory)][string]$DesiredPassword
    )
    $loader=Get-EpicVMHyperVValue -Object $Provider -Name 'BootstrapCredentialLoader' -Default $null
    $path=[string](Get-EpicVMHyperVValue -Object $Config -Name 'BootstrapCredentialPath' -Default 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi')
    $user=[string](Get-EpicVMHyperVValue -Object $Config -Name 'BootstrapUser' -Default 'EpicVMBootstrap')
    try {
        $credential=if($null -ne $loader){& $loader $path $user}else{Get-EpicVMBootstrapCredential -Path $path -Username $user}
    } catch {
        throw (New-EpicVMHyperVError -Code 'bootstrap_credential_unavailable' -Message 'The machine bootstrap credential could not open the guest channel.')
    }
    $script=Get-EpicVMGuestConfigurationScript
    try {
        $result=Invoke-EpicVMPowerShellDirect -Provider $Provider -VmName $VmName -Credential $credential -Script $script -ArgumentList @($DesiredUser,$DesiredPassword,$user,$path)
        $safe=@{ok=$false;listener=$false;firewallScoped=$false;bootstrapRemoved=$false}
        foreach($key in @($safe.Keys)){$safe[$key]=[bool](Get-EpicVMHyperVValue -Object $result -Name $key -Default $false)}
        if(-not $safe.ok){throw 'Guest configuration did not verify.'}
        return $safe
    } catch {
        $code=Get-EpicVMGuestProviderErrorCode -ErrorRecord $_
        throw (New-EpicVMHyperVError -Code $code -Message 'The guest configuration gate failed.')
    }
    finally {$DesiredPassword=$null;$credential=$null}
}

function Get-EpicVMSunshineConfigurationScript {
    return {
        param($SunshineUsername,$SunshinePassword,$ServiceName,$ExpectedVersion,$StatePaths)
        $ErrorActionPreference='Stop'
        if([string]::IsNullOrWhiteSpace([string]$SunshineUsername) -or [string]$SunshineUsername -match '[\r\n]' -or ([string]$SunshineUsername).Length -gt 128){throw 'Invalid Sunshine username.'}
        if([string]::IsNullOrEmpty([string]$SunshinePassword)){throw 'Invalid Sunshine password.'}
        $service=Get-CimInstance Win32_Service -Filter ("Name='" + ([string]$ServiceName).Replace("'","''") + "'") -ErrorAction SilentlyContinue
        if($null -eq $service){throw 'Sunshine service is not installed.'}
        $servicePath=[string]$service.PathName
        if($servicePath -match '^"([^"]+)"'){$servicePath=$Matches[1]}
        elseif($servicePath -match '^([^ ]+)'){$servicePath=$Matches[1]}
        if([string]::IsNullOrWhiteSpace($servicePath) -or -not (Test-Path -LiteralPath $servicePath -PathType Leaf)){throw 'Sunshine executable could not be located.'}
        $serviceDirectory=Split-Path -Parent $servicePath
        $mainSunshinePath=Join-Path (Split-Path -Parent $serviceDirectory) 'sunshine.exe'
        if(-not (Test-Path -LiteralPath $mainSunshinePath -PathType Leaf)){throw 'Sunshine executable could not be located.'}
        $installedVersion=[string]([Diagnostics.FileVersionInfo]::GetVersionInfo($mainSunshinePath).ProductVersion)
        if(-not [string]::IsNullOrWhiteSpace([string]$ExpectedVersion) -and $installedVersion -cne [string]$ExpectedVersion){throw 'Sunshine version did not match the pinned release.'}
        $paths=@($StatePaths | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
        $statePath=@($paths | Where-Object { Test-Path -LiteralPath ([string]$_) -PathType Leaf } | Select-Object -First 1)
        if($statePath.Count -eq 0){
            $statePath=@(Join-Path (Join-Path (Split-Path -Parent $servicePath) 'config') 'sunshine_state.json')
        }
        $statePath=[string]$statePath[0]
        $parent=Split-Path -Parent $statePath
        if(-not(Test-Path -LiteralPath $parent)){New-Item -ItemType Directory -Path $parent -Force|Out-Null}

        # Sunshine's supported state format is username + random salt +
        # SHA-256(UTF-8(password + salt)).  The clear password exists only in
        # this remoting process and is never passed to an executable.
        $saltBytes=New-Object byte[] 16
        $hashBytes=$null
        $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
        try{
            $rng.GetBytes($saltBytes)
            $salt=([BitConverter]::ToString($saltBytes)-replace '-','').ToLowerInvariant()
            $hashBytes=[Text.Encoding]::UTF8.GetBytes(([string]$SunshinePassword)+$salt)
            $sha=[Security.Cryptography.SHA256]::Create()
            try{$digest=$sha.ComputeHash($hashBytes)}finally{$sha.Dispose()}
            $passwordHash=([BitConverter]::ToString($digest)-replace '-','').ToLowerInvariant()
            $record=[ordered]@{username=[string]$SunshineUsername;salt=$salt;password=$passwordHash}
            $json=$record|ConvertTo-Json -Depth 4 -Compress
            $temporary=Join-Path $parent ('.sunshine_state-' + [Guid]::NewGuid().ToString('N') + '.tmp')
            try{
                [IO.File]::WriteAllText($temporary,$json,(New-Object Text.UTF8Encoding($false)))
                Move-Item -LiteralPath $temporary -Destination $statePath -Force -ErrorAction Stop
            }finally{if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue}}
        }finally{
            if($null -ne $hashBytes){[Array]::Clear($hashBytes,0,$hashBytes.Length)}
            if($null -ne $saltBytes){[Array]::Clear($saltBytes,0,$saltBytes.Length)}
            if($null -ne $rng){$rng.Dispose()}
            $SunshinePassword=$null;$json=$null;$passwordHash=$null;$digest=$null;$salt=$null
        }

        $acl=Get-Acl -LiteralPath $statePath
        $acl.SetAccessRuleProtection($true,$false)
        @($acl.Access)|ForEach-Object{[void]$acl.RemoveAccessRule($_)}
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new('SYSTEM','Read','Allow'))
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new('Administrators','Read','Allow'))
        $serviceAccount=[string]$service.StartName
        if($serviceAccount -and $serviceAccount -notin @('LocalSystem','NT AUTHORITY\LocalSystem','LocalService','NT AUTHORITY\LocalService','NetworkService','NT AUTHORITY\NetworkService') -and $serviceAccount -match '^(NT SERVICE\\|[A-Za-z0-9_.-]+\\)'){
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($serviceAccount,'Read','Allow'))
        }
        Set-Acl -LiteralPath $statePath -AclObject $acl
        Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { [string]$_.DisplayName -match '(?i)Sunshine' } | Disable-NetFirewallRule -ErrorAction SilentlyContinue
        Get-NetFirewallRule -Name 'EpicVM-Sunshine-Tailscale-TCP','EpicVM-Sunshine-Tailscale-UDP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -Name 'EpicVM-Sunshine-Tailscale-TCP' -DisplayName 'EpicVM Sunshine (Tailscale TCP)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 47984,47989,47990,48010 -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block | Out-Null
        New-NetFirewallRule -Name 'EpicVM-Sunshine-Tailscale-UDP' -DisplayName 'EpicVM Sunshine (Tailscale UDP)' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 47998,47999,48000,48002 -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block | Out-Null
        Restart-Service -Name $ServiceName -Force -ErrorAction Stop
        $deadline=[DateTime]::UtcNow.AddSeconds(15)
        $running=$false;$listener=$false
        while([DateTime]::UtcNow -lt $deadline){
            $current=Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
            $running=$null -ne $current -and [string]$current.Status -eq 'Running'
            $listener=$null -ne (Get-NetTCPConnection -LocalPort 47990 -State Listen -ErrorAction SilentlyContinue)
            if($running -and $listener){break}
            Start-Sleep -Milliseconds 500
        }
        if(-not $running -or -not $listener){throw 'Sunshine did not pass its service/listener verification.'}
        [ordered]@{ok=$true;serviceRunning=$true;listener=$true;credentialsConfigured=$true;firewallScoped=$true}
    }
}

function Invoke-EpicVMSunshineConfiguration {
    param(
        [Parameter(Mandatory)][object]$Provider,
        [Parameter(Mandatory)][object]$Config,
        [Parameter(Mandatory)][string]$VmName,
        [Parameter(Mandatory)][string]$GuestUsername,
        [Parameter(Mandatory)][string]$GuestPassword,
        [Parameter(Mandatory)][string]$SunshineUsername,
        [Parameter(Mandatory)][string]$SunshinePassword
    )
    if($GuestUsername -notmatch '^[A-Za-z][A-Za-z0-9._-]{2,31}$' -or [string]::IsNullOrEmpty($GuestPassword) -or [string]::IsNullOrEmpty($SunshineUsername) -or [string]::IsNullOrEmpty($SunshinePassword)){
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'Guest and Sunshine credentials are required.')
    }
    $credential=$null
    $sunshineScript=Get-EpicVMSunshineConfigurationScript
    $serviceName=[string](Get-EpicVMHyperVValue -Object $Config -Name 'SunshineServiceName' -Default 'SunshineService')
    $expectedVersion=[string](Get-EpicVMHyperVValue -Object $Config -Name 'SunshineVersion' -Default '2026.516.143833')
    $statePaths=@(Get-EpicVMHyperVValue -Object $Config -Name 'SunshineStatePaths' -Default @('C:\Program Files\Sunshine\config\sunshine_state.json','C:\ProgramData\Sunshine\config\sunshine_state.json'))
    try{
        $credential=[PSCredential]::new($GuestUsername,(ConvertTo-SecureString $GuestPassword -AsPlainText -Force))
        $result=Invoke-EpicVMPowerShellDirect -Provider $Provider -VmName $VmName -Credential $credential -Script $sunshineScript -ArgumentList @($SunshineUsername,$SunshinePassword,$serviceName,$expectedVersion,$statePaths)
        if(-not [bool](Get-EpicVMHyperVValue -Object $result -Name 'ok' -Default $false) -or -not [bool](Get-EpicVMHyperVValue -Object $result -Name 'listener' -Default $false)){
            throw 'Sunshine configuration did not verify.'
        }
        return [ordered]@{ok=$true;serviceRunning=[bool](Get-EpicVMHyperVValue -Object $result -Name 'serviceRunning' -Default $false);listener=$true;credentialsConfigured=$true;firewallScoped=[bool](Get-EpicVMHyperVValue -Object $result -Name 'firewallScoped' -Default $false)}
    }catch{throw (New-EpicVMHyperVError -Code 'SunshineConfigurationFailed' -Message 'Automatic Sunshine configuration failed.')}
    finally{$GuestPassword=$null;$SunshinePassword=$null;$credential=$null}
}

function Test-EpicVMGuestConfiguration {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$VmName,[Parameter(Mandatory)][PSCredential]$Credential)
    $script={
        $listener=Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction SilentlyContinue
        $rule=Get-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -ErrorAction SilentlyContinue
        $nla=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp').UserAuthentication -eq 1
        [bool]($listener -and $rule -and $nla)
    }
    try{return [bool](Invoke-EpicVMPowerShellDirect -Provider $Provider -VmName $VmName -Credential $Credential -Script $script)}catch{return $false}
}

function Test-EpicVMGuestRdpReachability {
    param([Parameter(Mandatory)][string]$Address,[int]$Port=3389,[int]$TimeoutMilliseconds=3000)
    if($Address -notmatch '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}$'){return $false}
    $client=[Net.Sockets.TcpClient]::new()
    try {
        $task=$client.ConnectAsync($Address,$Port)
        if(-not $task.Wait($TimeoutMilliseconds)){return $false}
        return $client.Connected
    } catch { return $false }
    finally { $client.Dispose() }
}
