#Requires -Version 7.0
<# Tailscale OAuth/auth-key provider. Secrets exist only in local variables. #>

Set-StrictMode -Version Latest

function Set-EpicVMTailscaleOAuthSecret {
    param([Parameter(Mandatory)][SecureString]$Secret,[Parameter(Mandatory)][string]$Path)
    Protect-EpicVMMachineSecret -Secret $Secret -Path $Path
}

function Get-EpicVMTailscaleOAuthSecret {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$Path)
    $loader=Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleOAuthSecretLoader' -Default $null
    if($null -ne $loader){return & $loader $Path}
    return Get-EpicVMBootstrapCredential -Path $Path -Username 'oauth-secret'
}

function ConvertTo-EpicVMSecretText {
    param([Parameter(Mandatory)][SecureString]$Secret)
    $ptr=[IntPtr]::Zero
    try { $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret); return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)} }
}

function Test-EpicVMTailscaleTransientError {
    param([AllowNull()][object]$ErrorRecord)
    $exception=Get-EpicVMHyperVValue -Object $ErrorRecord -Name 'Exception' -Default $ErrorRecord
    $response=Get-EpicVMHyperVValue -Object $exception -Name 'Response' -Default $null
    $status=0
    try { $status=[int](Get-EpicVMHyperVValue -Object $response -Name 'StatusCode' -Default 0) } catch { $status=0 }
    if($status -ge 500 -and $status -lt 600){ return $true }
    $message=[string](Get-EpicVMHyperVValue -Object $exception -Name 'Message' -Default $exception)
    return $message -match '(?i)timeout|timed out|connection reset|connection closed|temporarily unavailable|name resolution|network is unreachable'
}

function Invoke-EpicVMTailscaleHttp {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$Method,[Parameter(Mandatory)][string]$Path,[AllowNull()][object]$Body=$null,[Parameter(Mandatory)][string]$AccessToken,[int]$TimeoutSeconds=30)
    $invoker=Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleHttpInvoker' -Default $null
    $base=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleApiBaseUrl' -Default 'https://api.tailscale.com/api/v2')
    $url=$base.TrimEnd('/') + '/' + $Path.TrimStart('/')
    $headers=@{Authorization="Bearer $AccessToken";Accept='application/json'}
    if($null -ne $invoker){return & $invoker $Method $url $headers $Body}
    if($Method -eq 'GET'){return Invoke-RestMethod -Method Get -Uri $url -Headers $headers -TimeoutSec ([Math]::Max(1,$TimeoutSeconds)) -ErrorAction Stop}
    $json=if($null -eq $Body){$null}else{$Body|ConvertTo-Json -Depth 12 -Compress}
    return Invoke-RestMethod -Method $Method -Uri $url -Headers ($headers+@{'Content-Type'='application/json'}) -Body $json -TimeoutSec ([Math]::Max(1,$TimeoutSeconds)) -ErrorAction Stop
}

function Get-EpicVMTailscaleAccessToken {
    param([Parameter(Mandatory)][object]$Provider)
    $clientId=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleOAuthClientId' -Default '')
    $secretPath=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleOAuthSecretPath' -Default '')
    $tokenInvoker=Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleOAuthInvoker' -Default $null
    if([string]::IsNullOrWhiteSpace($clientId) -or [string]::IsNullOrWhiteSpace($secretPath)){throw 'Tailscale OAuth configuration is incomplete.'}
    $secret=Get-EpicVMTailscaleOAuthSecret -Provider $Provider -Path $secretPath
    $secretText=$null
    try {
        $secretText=ConvertTo-EpicVMSecretText -Secret $secret.Password
        $body=@{grant_type='client_credentials';client_id=$clientId;client_secret=$secretText}
        $attempt=0
        while($true){
            try {
                $result=if($null -ne $tokenInvoker){& $tokenInvoker $body}else{Invoke-RestMethod -Method Post -Uri 'https://api.tailscale.com/api/v2/oauth/token' -Body $body -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 30 -ErrorAction Stop}
                $access=[string](Get-EpicVMHyperVValue -Object $result -Name 'access_token' -Default '')
                if([string]::IsNullOrWhiteSpace($access)){throw 'Tailscale OAuth did not return an access token.'}
                return $access
            } catch {
                if($attempt -ge 1 -or -not (Test-EpicVMTailscaleTransientError -ErrorRecord $_)){throw}
                $attempt++
                Start-Sleep -Milliseconds 250
            }
        }
    } catch { throw 'Tailscale OAuth authentication failed.' }
    finally {$secretText=$null;$secret=$null}
}

function New-EpicVMTailscaleAuthKey {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$VmName)
    $tailnet=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleTailnet' -Default '')
    $tag=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleGuestTag' -Default 'tag:epicvm-guest')
    if($tag -ne 'tag:epicvm-guest'){throw 'The guest tag is fixed to tag:epicvm-guest.'}
    if([string]::IsNullOrWhiteSpace($tailnet)){throw 'The Tailscale tailnet is not configured.'}
    $access=Get-EpicVMTailscaleAccessToken -Provider $Provider
    try {
        $body=[ordered]@{capabilities=[ordered]@{devices=[ordered]@{create=[ordered]@{reusable=$false;ephemeral=$false;preauthorized=$true;tags=@($tag)}}};expirySeconds=3600;description=('EpicVM one-use ' + $VmName)}
        $attempt=0
        while($true){
            try {
                $result=Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'POST' -Path ('tailnet/' + [Uri]::EscapeDataString($tailnet) + '/keys') -Body $body -AccessToken $access -TimeoutSeconds 30
                $key=[string](Get-EpicVMHyperVValue -Object $result -Name 'key' -Default '')
                if([string]::IsNullOrWhiteSpace($key)){throw 'Tailscale did not return an auth key.'}
                return $key
            } catch {
                if($attempt -ge 1 -or -not (Test-EpicVMTailscaleTransientError -ErrorRecord $_)){throw}
                $attempt++
                Start-Sleep -Milliseconds 250
            }
        }
    } catch { throw 'Tailscale guest key creation failed.' }
    finally {$access=$null}
}

function Get-EpicVMTailscaleDeviceId {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$VmName,[Parameter(Mandatory)][string]$GuestIp)
    $tailnet=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleTailnet' -Default '')
    $access=Get-EpicVMTailscaleAccessToken -Provider $Provider
    try {
        $attempt=0
        while($true){
            try {
                $response=Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'GET' -Path ('tailnet/' + [Uri]::EscapeDataString($tailnet) + '/devices') -AccessToken $access -TimeoutSeconds 20
                $devices=@(Get-EpicVMHyperVValue -Object $response -Name 'devices' -Default @())
                # A retained clone can leave an offline control-plane record
                # with the same hostname. The address returned by the guest
                # enrollment is the authoritative identity for this operation;
                # use hostname only as a bounded fallback when no exact
                # address match exists.
                $addressMatches=@($devices | Where-Object {
                    $addresses=@(Get-EpicVMHyperVValue -Object $_ -Name 'addresses' -Default @())
                    $addresses -contains $GuestIp
                })
                $matches=@(if($addressMatches.Count -eq 1){$addressMatches}else{@($devices | Where-Object {
                    [string](Get-EpicVMHyperVValue -Object $_ -Name 'hostname' -Default '') -ceq $VmName
                })})
                if($matches.Count -ne 1){throw 'The enrolled Tailscale device could not be uniquely identified.'}
                $id=[string](Get-EpicVMHyperVValue -Object $matches[0] -Name 'id' -Default '')
                if([string]::IsNullOrWhiteSpace($id)){throw 'The enrolled Tailscale device has no revocation identifier.'}
                return $id
            } catch {
                if($attempt -ge 1 -or -not (Test-EpicVMTailscaleTransientError -ErrorRecord $_)){throw}
                $attempt++
                Start-Sleep -Milliseconds 250
            }
        }
    } finally {$access=$null}
}

function Get-EpicVMTailscaleGuestScript {
    return {
        param($AuthKey,$Hostname,$Executable)
        $ErrorActionPreference='Stop'
        if(-not(Test-Path -LiteralPath $Executable)){throw 'Tailscale is not installed in the guest.'}
        if(-not('EpicVM.NamedPipeSecret' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading.Tasks;
namespace EpicVM {
    public static class NamedPipeSecret {
        public static Task Serve(string pipeName, string value) {
            return Task.Run(() => {
                var security = new PipeSecurity();
                security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.ReadWrite, AccessControlType.Allow));
                security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), PipeAccessRights.ReadWrite, AccessControlType.Allow));
                using (var pipe = new NamedPipeServerStream(pipeName, PipeDirection.Out, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 0, 0, security)) {
                    pipe.WaitForConnection();
                    byte[] bytes = Encoding.UTF8.GetBytes(value);
                    try { pipe.Write(bytes, 0, bytes.Length); pipe.Flush(); }
                    finally { Array.Clear(bytes, 0, bytes.Length); }
                }
            });
        }
    }
}
'@
        }
        function Invoke-SystemTailscaleTask {
            param([Parameter(Mandatory)][string]$TaskName,[Parameter(Mandatory)][string]$Execute,[Parameter(Mandatory)][string]$Arguments,[int]$TimeoutSeconds=60)
            $action=$null;$trigger=$null;$principal=$null
            try {
                $action=New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -ErrorAction Stop
                $trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(2) -ErrorAction Stop
                $principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest -ErrorAction Stop
                Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force -ErrorAction Stop | Out-Null
                Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
                $deadline=[DateTime]::UtcNow.AddSeconds([Math]::Max(5,$TimeoutSeconds))
                $info=$null
                do {
                    Start-Sleep -Milliseconds 250
                    $info=Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
                    $state=[string]$info.State
                    if($state -notin @('Running','Queued')){break}
                } while([DateTime]::UtcNow -lt $deadline)
                if($null -eq $info -or [string]$info.State -in @('Running','Queued')){throw 'EPICVM_TAILSCALE_SYSTEM_TASK_TIMEOUT'}
                return [ordered]@{completed=$true;exitCode=[int]$info.LastTaskResult}
            } catch {
                $text=[string]$_.Exception.Message
                if($text -match 'EPICVM_TAILSCALE_SYSTEM_TASK_TIMEOUT'){throw}
                throw 'EPICVM_TAILSCALE_SYSTEM_TASK_FAILED'
            } finally {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            }
        }
        $pipeName='EpicVM-Tailscale-' + [Guid]::NewGuid().ToString('N')
        $pipeTask=[EpicVM.NamedPipeSecret]::Serve($pipeName,$AuthKey)
        $pipeConsumed=$false
        try {
            # The one-use key is served from memory. Only the pipe path appears
            # in the child process arguments.
            # The guest PowerShell session may be an administrator with a
            # filtered remote token. Run the CLI as LocalSystem so it can reach
            # Tailscale's protected local API pipe without weakening UAC or
            # granting the agent broader Hyper-V rights. The scheduled task
            # contains only the ephemeral pipe path, never the auth key.
            $pipePath='file:\\.\pipe\' + $pipeName
            $upArguments='up --auth-key "' + $pipePath + '" --hostname "' + $Hostname + '" --unattended=true --accept-dns=false --reset'
            $upTask=Invoke-SystemTailscaleTask -TaskName ('EpicVM-Tailscale-Up-' + [Guid]::NewGuid().ToString('N')) -Execute $Executable -Arguments $upArguments -TimeoutSeconds 60
            $pipeConsumed=$pipeTask.Wait(5000)
            if(-not $pipeConsumed){throw 'EPICVM_TAILSCALE_AUTH_INPUT_FAILED'}
            if([int]$upTask.exitCode -ne 0){throw 'EPICVM_TAILSCALE_GUEST_COMMAND_FAILED'}
        }
        finally {$AuthKey=$null;$pipeTask=$null}
        # Windows normally runs Tailscale in the logged-in user's context.
        # Make the unattended/system handoff explicit, then restart the
        # service once and verify the address returns.  Without this check a
        # clone can appear enrolled until reboot while losing its durable node
        # state, leaving a stale tailnetIp in the provisioning store.
        $setTask=Invoke-SystemTailscaleTask -TaskName ('EpicVM-Tailscale-Set-' + [Guid]::NewGuid().ToString('N')) -Execute $Executable -Arguments 'set --unattended=true' -TimeoutSeconds 30
        if([int]$setTask.exitCode -ne 0){throw 'EPICVM_TAILSCALE_UNATTENDED_FAILED'}
        try {
            Restart-Service -Name 'Tailscale' -Force -ErrorAction Stop
        } catch {
            $restartTask=Invoke-SystemTailscaleTask -TaskName ('EpicVM-Tailscale-Restart-' + [Guid]::NewGuid().ToString('N')) -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Arguments '-NoProfile -NonInteractive -Command "Restart-Service -Name Tailscale -Force"' -TimeoutSeconds 30
            if([int]$restartTask.exitCode -ne 0){throw 'EPICVM_TAILSCALE_RESTART_FAILED'}
        }
        $deadline=(Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Milliseconds 500
            $ip=[string](@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { [string]$_.IPAddress -match '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$' } | Select-Object -First 1 -ExpandProperty IPAddress)).Trim()
            if($ip -match '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$'){break}
        } while((Get-Date) -lt $deadline)
        if($ip -notmatch '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$'){throw 'Guest Tailscale IP verification failed.'}
        return [ordered]@{ok=$true;ip=$ip}
    }
}

function Get-EpicVMTailscaleGuestAddressScript {
    return {
        $ErrorActionPreference='Stop'
        $ip=[string](@(
            Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { [string]$_.IPAddress -match '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$' } |
                Select-Object -First 1 -ExpandProperty IPAddress
        )).Trim()
        if($ip -notmatch '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$'){
            throw 'Guest Tailscale IP verification failed.'
        }
        [ordered]@{ok=$true;ip=$ip}
    }
}

function Get-EpicVMGuestManagementConfigurationScript {
    return {
        param($Port,$UseSsl)
        $ErrorActionPreference='Stop'
        $winrm=Get-Service -Name 'WinRM' -ErrorAction Stop
        Set-Service -Name 'WinRM' -StartupType Automatic -ErrorAction Stop
        if([string]$winrm.Status -ne 'Running'){Start-Service -Name 'WinRM' -ErrorAction Stop}
        if(Get-Command -Name Enable-PSRemoting -ErrorAction SilentlyContinue){
            Enable-PSRemoting -SkipNetworkProfileCheck -Force -ErrorAction Stop | Out-Null
        }
        # Enable-PSRemoting may create broad built-in rules. Disable those and
        # replace them with one exact Tailscale-only management rule.
        Get-NetFirewallRule -DisplayGroup 'Windows Remote Management' -ErrorAction SilentlyContinue | Disable-NetFirewallRule -ErrorAction SilentlyContinue
        Get-NetFirewallRule -Name 'EpicVM-WinRM-Tailscale' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -Name 'EpicVM-WinRM-Tailscale' -DisplayName 'EpicVM WinRM (Tailscale)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort ([int]$Port) -RemoteAddress '100.64.0.0/10' -Profile Any -EdgeTraversalPolicy Block -ErrorAction Stop | Out-Null
        try {
            Set-Item -Path 'WSMan:\localhost\Service\Auth\Negotiate' -Value $true -Force -ErrorAction Stop
            Set-Item -Path 'WSMan:\localhost\Service\Auth\Basic' -Value $false -Force -ErrorAction Stop
            Set-Item -Path 'WSMan:\localhost\Service\Auth\CredSSP' -Value $false -Force -ErrorAction SilentlyContinue
            Set-Item -Path 'WSMan:\localhost\Service\AllowUnencrypted' -Value $false -Force -ErrorAction Stop
        } catch { throw 'EPICVM_MANAGEMENT_ENDPOINT_FAILED' }
        $listeners=@(Get-WSManInstance -ResourceURI 'winrm/config/listener' -Enumerate -ErrorAction SilentlyContinue)
        $transport=if([bool]$UseSsl){'HTTPS'}else{'HTTP'}
        $listener=$listeners | Where-Object { [string]$_.Transport -eq $transport -and ([int]$_.Port -eq [int]$Port -or [string]::IsNullOrWhiteSpace([string]$_.Port)) } | Select-Object -First 1
        if($null -eq $listener){throw 'EPICVM_MANAGEMENT_ENDPOINT_FAILED'}
        [ordered]@{ok=$true;managementEndpoint=$true;managementPort=[int]$Port;managementUseSsl=[bool]$UseSsl;firewallScoped=$true}
    }
}

function Invoke-EpicVMTailscaleEnrollment {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][object]$Config,[Parameter(Mandatory)][string]$VmName,[Parameter(Mandatory)][string]$Username,[Parameter(Mandatory)][string]$Password)
    $key=New-EpicVMTailscaleAuthKey -Provider $Provider -VmName $VmName
    $credential=[PSCredential]::new($Username,(ConvertTo-SecureString $Password -AsPlainText -Force))
    $script=Get-EpicVMTailscaleGuestScript
    $managementScript=Get-EpicVMGuestManagementConfigurationScript
    $managementPort=5985
    try { $managementPort=[int](Get-EpicVMHyperVValue -Object $Config -Name 'ManagementPort' -Default 5985) } catch { $managementPort=5985 }
    if($managementPort -lt 1 -or $managementPort -gt 65535){$managementPort=5985}
    $managementUseSsl=[bool](Get-EpicVMHyperVValue -Object $Config -Name 'ManagementUseSsl' -Default $false)
    $exe=[string](Get-EpicVMHyperVValue -Object $Config -Name 'TailscaleExecutable' -Default 'C:\Program Files\Tailscale\tailscale.exe')
    try {
        # The auth key is one-use. Once issued, this guest operation is never
        # retried automatically; recovery must be explicit and stage-limited.
        $result=Invoke-EpicVMPowerShellDirectOnce -Provider $Provider -VmName $VmName -Credential $credential -Script $script -ArgumentList @($key,$VmName,$exe) -TimeoutSeconds 60
        $ip=[string](Get-EpicVMHyperVValue -Object $result -Name 'ip' -Default '')
        if($ip -notmatch '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.') {throw 'Guest Tailscale IP is invalid.'}
        $known=@(Get-EpicVMHyperVValue -Object $Provider -Name 'KnownTailscaleIps' -Default @())
        if($known -contains $ip){throw 'The guest received a duplicate Tailscale IP.'}
        $deviceId=Get-EpicVMTailscaleDeviceId -Provider $Provider -VmName $VmName -GuestIp $ip
        # Tailscale is now the control-plane handoff point. Configure WinRM
        # through the already-authenticated pre-network channel exactly once;
        # the subsequent streaming stage must use the private management path.
        $management=Invoke-EpicVMPowerShellDirectOnce -Provider $Provider -VmName $VmName -Credential $credential -Script $managementScript -ArgumentList @($managementPort,$managementUseSsl) -TimeoutSeconds 45
        if(-not [bool](Get-EpicVMHyperVValue -Object $management -Name 'ok' -Default $false)){throw 'EPICVM_MANAGEMENT_ENDPOINT_FAILED'}
        return [ordered]@{ok=$true;ip=$ip;deviceId=$deviceId;tag='tag:epicvm-guest';managementReady=$true;managementTransport='tailscale_winrm';managementPort=$managementPort;managementUseSsl=$managementUseSsl}
    } catch {
        $safeCode='tailscale_enrollment_failed'
        $safeText=([string]$_.Exception.Message) + ' ' + ([string]$_.ToString())
        if($safeText -match 'EPICVM_TAILSCALE_AUTH_INPUT_FAILED'){$safeCode='tailscale_auth_input_failed'}
        if($safeText -match 'EPICVM_TAILSCALE_GUEST_COMMAND_FAILED'){$safeCode='tailscale_guest_command_failed'}
        if($safeText -match 'EPICVM_TAILSCALE_SYSTEM_TASK_TIMEOUT'){$safeCode='tailscale_system_task_timeout'}
        if($safeText -match 'EPICVM_TAILSCALE_SYSTEM_TASK_FAILED'){$safeCode='tailscale_system_task_failed'}
        if($safeText -match 'EPICVM_TAILSCALE_UNATTENDED_FAILED'){$safeCode='tailscale_unattended_failed'}
        if($safeCode -eq 'tailscale_enrollment_failed' -and $safeText -match '(?i)unattended|durable|state|Tailscale IP verification failed'){$safeCode='tailscale_state_not_persisted'}
        if($safeCode -eq 'tailscale_enrollment_failed' -and $safeText -match '(?i)EPICVM_MANAGEMENT|management endpoint|WinRM'){$safeCode='management_handoff_failed'}
        throw (New-EpicVMHyperVError -Code $safeCode -Message 'Tailscale guest enrollment failed.')
    }
    finally {$key=$null;$Password=$null;$credential=$null}
}

function Revoke-EpicVMTailscaleDevice {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$DeviceId)
    if([string]::IsNullOrWhiteSpace($DeviceId)){return [ordered]@{ok=$true;revoked=$false}}
    $access=Get-EpicVMTailscaleAccessToken -Provider $Provider
    try { Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'DELETE' -Path ('device/' + [Uri]::EscapeDataString($DeviceId)) -AccessToken $access | Out-Null; return [ordered]@{ok=$true;revoked=$true} }
    catch { throw (New-EpicVMHyperVError -Code 'tailscale_revoke_failed' -Message 'Tailscale device revocation failed.') }
    finally {$access=$null}
}

function Get-EpicVMTailscaleStaleDeviceIds {
    # Pure filter: every device sharing the VM's hostname except the one to
    # keep. Re-enrolling a VM name leaves the previous enrollment's
    # control-plane record behind; this selects them for revocation.
    param([AllowNull()][object[]]$Devices,[Parameter(Mandatory)][string]$VmName,[AllowNull()][string]$KeepDeviceId='')
    $keep=[string]$KeepDeviceId
    $stale=@($Devices | Where-Object {
        [string](Get-EpicVMHyperVValue -Object $_ -Name 'hostname' -Default '') -ceq $VmName
    } | ForEach-Object {
        [string](Get-EpicVMHyperVValue -Object $_ -Name 'id' -Default '')
    } | Where-Object {
        (-not [string]::IsNullOrWhiteSpace($_)) -and ($_ -cne $keep)
    })
    return ,$stale
}

function Clear-EpicVMTailscaleStaleDevices {
    # Best-effort sweep: revoke every tailnet device for VmName that is not
    # KeepDeviceId. Individual deletion failures are reported, never thrown;
    # callers must be able to finish teardown even when Tailscale is flaky.
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$VmName,[AllowNull()][string]$KeepDeviceId='',[AllowNull()][string]$AccessToken='')
    $tailnet=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleTailnet' -Default '')
    if([string]::IsNullOrWhiteSpace($tailnet)){
        return [ordered]@{ok=$false;skipped='tailnet_not_configured';revoked=@();failed=@()}
    }
    $access=[string]$AccessToken
    if([string]::IsNullOrWhiteSpace($access)){$access=Get-EpicVMTailscaleAccessToken -Provider $Provider}
    $revoked=@()
    $failed=@()
    try {
        $response=Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'GET' -Path ('tailnet/' + [Uri]::EscapeDataString($tailnet) + '/devices') -AccessToken $access -TimeoutSeconds 20
        $devices=@(Get-EpicVMHyperVValue -Object $response -Name 'devices' -Default @())
        foreach($deviceId in (Get-EpicVMTailscaleStaleDeviceIds -Devices $devices -VmName $VmName -KeepDeviceId ([string]$KeepDeviceId))){
            try{
                Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'DELETE' -Path ('device/' + [Uri]::EscapeDataString($deviceId)) -AccessToken $access -TimeoutSeconds 20 | Out-Null
                $revoked+=,$deviceId
            }catch{$failed+=,$deviceId}
        }
    } catch {
        return [ordered]@{ok=($revoked.Count -gt 0);skipped='device_list_unavailable';revoked=@($revoked);failed=@($failed)}
    } finally {$access=$null}
    return [ordered]@{ok=$true;skipped='';revoked=@($revoked);failed=@($failed)}
}
