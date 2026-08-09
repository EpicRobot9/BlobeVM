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
    return Invoke-Command -VMName $VmName -Credential $Credential -ScriptBlock $Script -ArgumentList $ArgumentList -ErrorAction Stop
}

function Get-EpicVMGuestConfigurationScript {
    return {
        param($DesiredUser,$DesiredPassword,$BootstrapUser,$BootstrapCredentialPath)
        $ErrorActionPreference='Stop'
        if($DesiredUser -notmatch '^[A-Za-z][A-Za-z0-9._-]{2,31}$'){throw 'Invalid desired user.'}
        if([string]::IsNullOrEmpty([string]$DesiredPassword) -or $DesiredPassword.Length -lt 12){throw 'Invalid desired password.'}
        $secure=ConvertTo-SecureString $DesiredPassword -AsPlainText -Force
        $user=Get-LocalUser -Name $DesiredUser -ErrorAction SilentlyContinue
        if($null -eq $user){New-LocalUser -Name $DesiredUser -Password $secure -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword|Out-Null}
        else{Set-LocalUser -Name $DesiredUser -Password $secure -AccountNeverExpires -PasswordNeverExpires}
        Add-LocalGroupMember -Group 'Administrators' -Member $DesiredUser -ErrorAction SilentlyContinue
        $adminMembers=@(Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop | ForEach-Object { [string]$_.Name.Split('\')[-1] })
        $adminOk=$adminMembers -contains $DesiredUser
        Set-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Type DWord -Value 0
        Set-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -Type DWord -Value 1
        Set-Service -Name TermService -StartupType Automatic
        Start-Service -Name TermService
        Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue | Disable-NetFirewallRule -ErrorAction SilentlyContinue
        Get-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -DisplayName 'EpicVM RDP (Tailscale only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3389 -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block | Out-Null
        $listener=Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction SilentlyContinue
        $rule=Get-NetFirewallRule -Name 'EpicVM-RDP-Tailscale' -ErrorAction SilentlyContinue
        $addressFilter=if($null -ne $rule){Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue}else{$null}
        $nlaOk=(Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -ErrorAction Stop).UserAuthentication -eq 1
        $portOk=$null -ne $listener
        $ruleOk=$null -ne $rule -and [string]$rule.Direction -eq 'Inbound' -and [string]$rule.Action -eq 'Allow' -and $null -ne $addressFilter -and (@($addressFilter.RemoteAddress) -contains '100.64.0.0/10')
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
    $credential=if($null -ne $loader){& $loader $path $user}else{Get-EpicVMBootstrapCredential -Path $path -Username $user}
    $script=Get-EpicVMGuestConfigurationScript
    try {
        $result=Invoke-EpicVMPowerShellDirect -Provider $Provider -VmName $VmName -Credential $credential -Script $script -ArgumentList @($DesiredUser,$DesiredPassword,$user,$path)
        $safe=@{ok=$false;listener=$false;firewallScoped=$false;bootstrapRemoved=$false}
        foreach($key in @($safe.Keys)){$safe[$key]=[bool](Get-EpicVMHyperVValue -Object $result -Name $key -Default $false)}
        if(-not $safe.ok){throw 'Guest configuration did not verify.'}
        return $safe
    } catch { throw (New-EpicVMHyperVError -Code 'GuestConfigurationFailed' -Message 'PowerShell Direct guest configuration failed.') }
    finally {$DesiredPassword=$null;$credential=$null}
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
