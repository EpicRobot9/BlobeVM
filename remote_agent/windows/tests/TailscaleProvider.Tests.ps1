#Requires -Version 7.0
#Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'providers/HyperVProvider.ps1')
    . (Join-Path $windowsRoot 'providers/GuestProvider.ps1')
    . (Join-Path $windowsRoot 'providers/TailscaleProvider.ps1')
}

Describe 'Tailscale OAuth enrollment' {
    It 'creates a non-reusable preauthorized guest key and verifies the guest IP' {
        $secretPath = 'mock://oauth-secret'
        $script:request = $null
        $provider=[pscustomobject]@{
            TailscaleOAuthClientId='client-id'
            TailscaleOAuthSecretPath=$secretPath
            TailscaleTailnet='example.ts.net'
            TailscaleGuestTag='tag:epicvm-guest'
            TailscaleApiBaseUrl='https://example.invalid/api/v2'
            TailscaleOAuthInvoker={ param($body) @{ access_token='access' } }
            TailscaleOAuthSecretLoader={ param($path) [PSCredential]::new('oauth-secret',(ConvertTo-SecureString ('z' * 24) -AsPlainText -Force)) }
            TailscaleHttpInvoker={ param($method,$url,$headers,$body) if($null -ne $body){$script:request=$body}; if($method -eq 'GET'){ @{ devices=@(@{ id='device-1'; hostname='alpha'; addresses=@('100.111.82.44') }) } } else { @{ key='one-use-key' } } }
            PowerShellDirectInvoker={ param($vm,$credential,$scriptBlock,$args) @{ ok=$true; ip='100.111.82.44' } }
            KnownTailscaleIps=@()
        }
        $result=Invoke-EpicVMTailscaleEnrollment -Provider $provider -Config ([pscustomobject]@{TailscaleExecutable='tailscale.exe'}) -VmName 'alpha' -Username 'operator' -Password ('q' * 16)
        $result.ok | Should -BeTrue
        $result.ip | Should -Be '100.111.82.44'
        $script:request.capabilities.devices.create.reusable | Should -BeFalse
        $script:request.capabilities.devices.create.preauthorized | Should -BeTrue
        $script:request.capabilities.devices.create.tags | Should -Contain 'tag:epicvm-guest'
        (Get-EpicVMTailscaleGuestScript).ToString() | Should -Match 'file:\\\\.\\pipe\\'
        (Get-EpicVMTailscaleGuestScript).ToString() | Should -Not -Match '--authkey\s+\$AuthKey'
    }

    It 'rejects a duplicate guest address' {
        $provider=[pscustomobject]@{
            TailscaleOAuthClientId='client-id';TailscaleOAuthSecretPath='mock://oauth-secret';TailscaleTailnet='example.ts.net';TailscaleGuestTag='tag:epicvm-guest'
            TailscaleOAuthSecretLoader={ [PSCredential]::new('oauth-secret',(ConvertTo-SecureString ('z' * 24) -AsPlainText -Force)) }
            TailscaleOAuthInvoker={ @{access_token='access'} }
            TailscaleHttpInvoker={ param($method,$url,$headers,$body) @{key='one-use-key'} }
            PowerShellDirectInvoker={ @{ok=$true;ip='100.111.82.44'} }
            KnownTailscaleIps=@('100.111.82.44')
        }
        { Invoke-EpicVMTailscaleEnrollment -Provider $provider -Config ([pscustomobject]@{TailscaleExecutable='tailscale.exe'}) -VmName 'alpha' -Username 'operator' -Password ('q' * 16) } | Should -Throw '*enrollment*'
    }

    It 'revokes only the recorded Tailscale device identifier' {
        $script:revocation=$null
        $provider=[pscustomobject]@{
            TailscaleOAuthClientId='client-id';TailscaleOAuthSecretPath='mock://oauth-secret'
            TailscaleOAuthSecretLoader={ [PSCredential]::new('oauth-secret',(ConvertTo-SecureString ('z' * 24) -AsPlainText -Force)) }
            TailscaleOAuthInvoker={ @{access_token='access'} }
            TailscaleHttpInvoker={ param($method,$url,$headers,$body) $script:revocation=@{method=$method;url=$url}; @{} }
        }
        $result=Revoke-EpicVMTailscaleDevice -Provider $provider -DeviceId 'device-123'
        $result.revoked | Should -BeTrue
        $script:revocation.method | Should -Be 'DELETE'
        $script:revocation.url | Should -Match '/device/device-123$'
    }
}
