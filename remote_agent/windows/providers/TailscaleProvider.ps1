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

function Invoke-EpicVMTailscaleHttp {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$Method,[Parameter(Mandatory)][string]$Path,[AllowNull()][object]$Body=$null,[Parameter(Mandatory)][string]$AccessToken)
    $invoker=Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleHttpInvoker' -Default $null
    $base=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleApiBaseUrl' -Default 'https://api.tailscale.com/api/v2')
    $url=$base.TrimEnd('/') + '/' + $Path.TrimStart('/')
    $headers=@{Authorization="Bearer $AccessToken";Accept='application/json'}
    if($null -ne $invoker){return & $invoker $Method $url $headers $Body}
    if($Method -eq 'GET'){return Invoke-RestMethod -Method Get -Uri $url -Headers $headers -ErrorAction Stop}
    $json=if($null -eq $Body){$null}else{$Body|ConvertTo-Json -Depth 12 -Compress}
    return Invoke-RestMethod -Method $Method -Uri $url -Headers ($headers+@{'Content-Type'='application/json'}) -Body $json -ErrorAction Stop
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
        $result=if($null -ne $tokenInvoker){& $tokenInvoker $body}else{Invoke-RestMethod -Method Post -Uri 'https://api.tailscale.com/api/v2/oauth/token' -Body $body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop}
        $access=[string](Get-EpicVMHyperVValue -Object $result -Name 'access_token' -Default '')
        if([string]::IsNullOrWhiteSpace($access)){throw 'Tailscale OAuth did not return an access token.'}
        return $access
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
        $result=Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'POST' -Path ('tailnet/' + [Uri]::EscapeDataString($tailnet) + '/keys') -Body $body -AccessToken $access
        $key=[string](Get-EpicVMHyperVValue -Object $result -Name 'key' -Default '')
        if([string]::IsNullOrWhiteSpace($key)){throw 'Tailscale did not return an auth key.'}
        return $key
    } catch { throw 'Tailscale guest key creation failed.' }
    finally {$access=$null}
}

function Get-EpicVMTailscaleDeviceId {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$VmName,[Parameter(Mandatory)][string]$GuestIp)
    $tailnet=[string](Get-EpicVMHyperVValue -Object $Provider -Name 'TailscaleTailnet' -Default '')
    $access=Get-EpicVMTailscaleAccessToken -Provider $Provider
    try {
        $response=Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'GET' -Path ('tailnet/' + [Uri]::EscapeDataString($tailnet) + '/devices') -AccessToken $access
        $devices=@(Get-EpicVMHyperVValue -Object $response -Name 'devices' -Default @())
        $matches=@($devices | Where-Object {
            $addresses=@(Get-EpicVMHyperVValue -Object $_ -Name 'addresses' -Default @())
            $hostname=[string](Get-EpicVMHyperVValue -Object $_ -Name 'hostname' -Default '')
            ($addresses -contains $GuestIp) -or $hostname -ceq $VmName
        })
        if($matches.Count -ne 1){throw 'The enrolled Tailscale device could not be uniquely identified.'}
        $id=[string](Get-EpicVMHyperVValue -Object $matches[0] -Name 'id' -Default '')
        if([string]::IsNullOrWhiteSpace($id)){throw 'The enrolled Tailscale device has no revocation identifier.'}
        return $id
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
using System.Text;
using System.Threading.Tasks;
namespace EpicVM {
    public static class NamedPipeSecret {
        public static Task Serve(string pipeName, string value) {
            return Task.Run(() => {
                using (var pipe = new NamedPipeServerStream(pipeName, PipeDirection.Out, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous)) {
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
        $pipeName='EpicVM-Tailscale-' + [Guid]::NewGuid().ToString('N')
        $pipeTask=[EpicVM.NamedPipeSecret]::Serve($pipeName,$AuthKey)
        try {
            # The one-use key is served from memory through a named pipe. The
            # Tailscale process receives only the pipe path in its arguments.
            & $Executable up --authkey ("file:\\.\pipe\" + $pipeName) --hostname $Hostname --unattended --accept-dns=false --reset 2>$null | Out-Null
            if(-not $pipeTask.Wait(30000)){throw 'Tailscale did not consume the enrollment key.'}
        }
        finally {$AuthKey=$null;$pipeTask=$null}
        $ip=[string]((& $Executable ip -4 2>$null | Select-Object -First 1)).Trim()
        if($ip -notmatch '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$'){throw 'Guest Tailscale IP verification failed.'}
        return [ordered]@{ok=$true;ip=$ip}
    }
}

function Invoke-EpicVMTailscaleEnrollment {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][object]$Config,[Parameter(Mandatory)][string]$VmName,[Parameter(Mandatory)][string]$Username,[Parameter(Mandatory)][string]$Password)
    $key=New-EpicVMTailscaleAuthKey -Provider $Provider -VmName $VmName
    $credential=[PSCredential]::new($Username,(ConvertTo-SecureString $Password -AsPlainText -Force))
    $script=Get-EpicVMTailscaleGuestScript
    $exe=[string](Get-EpicVMHyperVValue -Object $Config -Name 'TailscaleExecutable' -Default 'C:\Program Files\Tailscale\tailscale.exe')
    try {
        $result=Invoke-EpicVMPowerShellDirect -Provider $Provider -VmName $VmName -Credential $credential -Script $script -ArgumentList @($key,$VmName,$exe)
        $ip=[string](Get-EpicVMHyperVValue -Object $result -Name 'ip' -Default '')
        if($ip -notmatch '^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.') {throw 'Guest Tailscale IP is invalid.'}
        $known=@(Get-EpicVMHyperVValue -Object $Provider -Name 'KnownTailscaleIps' -Default @())
        if($known -contains $ip){throw 'The guest received a duplicate Tailscale IP.'}
        $deviceId=Get-EpicVMTailscaleDeviceId -Provider $Provider -VmName $VmName -GuestIp $ip
        return [ordered]@{ok=$true;ip=$ip;deviceId=$deviceId;tag='tag:epicvm-guest'}
    } catch { throw (New-EpicVMHyperVError -Code 'TailscaleEnrollmentFailed' -Message 'Tailscale guest enrollment failed.') }
    finally {$key=$null;$Password=$null;$credential=$null}
}

function Revoke-EpicVMTailscaleDevice {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$DeviceId)
    if([string]::IsNullOrWhiteSpace($DeviceId)){return [ordered]@{ok=$true;revoked=$false}}
    $access=Get-EpicVMTailscaleAccessToken -Provider $Provider
    try { Invoke-EpicVMTailscaleHttp -Provider $Provider -Method 'DELETE' -Path ('device/' + [Uri]::EscapeDataString($DeviceId)) -AccessToken $access | Out-Null; return [ordered]@{ok=$true;revoked=$true} }
    catch { throw (New-EpicVMHyperVError -Code 'TailscaleRevokeFailed' -Message 'Tailscale device revocation failed.') }
    finally {$access=$null}
}
