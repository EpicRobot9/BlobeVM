#Requires -Version 7.0
#Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'providers/HyperVProvider.ps1')
    . (Join-Path $windowsRoot 'providers/GuestProvider.ps1')
}

Describe 'PowerShell Direct guest provider' {
    It 'uses the injected boundary and returns only redacted verification fields' {
        $script:invoked = $false
        $provider = [pscustomobject]@{
            Name='Mock'
        PowerShellDirectInvoker={ param($vm,$credential,$scriptBlock,$args) $script:invoked=$true; if($credential.UserName -eq 'EpicVMBootstrap'){ @{ ok=$true; listener=$true; firewallScoped=$true; bootstrapRemoved=$false } } else { @{ ok=$true; bootstrapRemoved=$true } } }
            BootstrapCredentialLoader={ param($path,$user) [PSCredential]::new($user,(ConvertTo-SecureString ('x' * 16) -AsPlainText -Force)) }
        }
        $config=[pscustomobject]@{BootstrapCredentialPath='mock.dpapi';BootstrapUser='EpicVMBootstrap'}
        $result=Invoke-EpicVMGuestConfiguration -Provider $provider -Config $config -VmName 'alpha' -DesiredUser 'operator' -DesiredPassword ('y' * 16)
        $script:invoked | Should -BeTrue
        $result.ok | Should -BeTrue
        ($result.Keys -join ',') | Should -Not -Match 'password|secret|token'
    }

    It 'ships the scoped NLA and Tailscale-only firewall reconciliation' {
        $script = Get-EpicVMGuestConfigurationScript
        $text = $script.ToString()
        $text | Should -Match 'UserAuthentication'
        $text | Should -Match '100.64.0.0/10'
        $text | Should -Match '100.64.0.0/255.192.0.0'
        $text | Should -Match 'EpicVM-RDP-Tailscale'
        $cleanup = Get-EpicVMGuestBootstrapCleanupScript
        $cleanup.ToString() | Should -Match 'Remove-LocalUser'
        $text | Should -Match 'bootstrapRemovalRequired'
        }

    It 'defers bootstrap removal until readiness and cleanup sessions use the new administrator' {
        $script:credentialUsers = @()
        $provider = [pscustomobject]@{
            PowerShellDirectInvoker={ param($vm,$credential,$scriptBlock,$args) $script:credentialUsers += $credential.UserName; if($credential.UserName -eq 'EpicVMBootstrap'){ @{ ok=$true; listener=$true; firewallScoped=$true; bootstrapRemoved=$false } } else { @{ ok=$true; bootstrapRemoved=$true } } }
            BootstrapCredentialLoader={ param($path,$user) [PSCredential]::new($user,(ConvertTo-SecureString ('x' * 16) -AsPlainText -Force)) }
        }
        $config=[pscustomobject]@{BootstrapCredentialPath='mock.dpapi';BootstrapUser='EpicVMBootstrap'}
        $result=Invoke-EpicVMGuestConfiguration -Provider $provider -Config $config -VmName 'alpha' -DesiredUser 'operator' -DesiredPassword ('y' * 16)
        $result.bootstrapRemoved | Should -BeTrue
        $script:credentialUsers | Should -Be @('EpicVMBootstrap','operator','operator')
    }

    It 'waits for a read-only bootstrap probe before guest mutation' {
        $provider = [pscustomobject]@{
            PowerShellDirectInvoker={ param($vm,$credential,$scriptBlock,$args) @{ ok=$true; powershellDirect=$true } }
            BootstrapCredentialLoader={ param($path,$user) [PSCredential]::new($user,(ConvertTo-SecureString ('x' * 16) -AsPlainText -Force)) }
        }
        $config=[pscustomobject]@{BootstrapCredentialPath='mock.dpapi';BootstrapUser='EpicVMBootstrap'}
        (Wait-EpicVMGuestBootstrapReady -Provider $provider -Config $config -VmName 'alpha' -TimeoutSeconds 1 -PollMilliseconds 100) | Should -BeTrue
    }

    It 'bounds the native Hyper-V VMName command without an incompatible session option' {
        $text = Get-Content (Join-Path $windowsRoot 'providers/GuestProvider.ps1') -Raw
        $text | Should -Match 'Invoke-Command -VMName \$VmName.*-AsJob'
        $text | Should -Match '\[int\]\$TimeoutSeconds=10'
        $text | Should -Match 'Wait-Job -Job \$guestJob -Timeout \(\[Math\]::Max\(1,\$TimeoutSeconds\)\)'
        $text | Should -Not -Match 'Invoke-Command -VMName \$VmName.*-SessionOption'
    }

    It 'classifies PowerShell Direct failures without exposing transport text' {
        $provider = [pscustomobject]@{
            PowerShellDirectInvoker={ throw 'PowerShell Direct channel unavailable.' }
            BootstrapCredentialLoader={ param($path,$user) [PSCredential]::new($user,(ConvertTo-SecureString ('x' * 16) -AsPlainText -Force)) }
        }
        $config=[pscustomobject]@{BootstrapCredentialPath='mock.dpapi';BootstrapUser='EpicVMBootstrap'}
        $caught=$null
        try { Invoke-EpicVMGuestConfiguration -Provider $provider -Config $config -VmName 'alpha' -DesiredUser 'operator' -DesiredPassword ('y' * 16) } catch { $caught=$_.Exception }
        $caught.ErrorCode | Should -Be 'powershell_direct_failed'
        $caught.Message | Should -Be 'The guest configuration gate failed.'
    }

    It 'classifies bootstrap credential failures safely' {
        $provider = [pscustomobject]@{
            BootstrapCredentialLoader={ throw 'DPAPI bootstrap secret unavailable.' }
        }
        $config=[pscustomobject]@{BootstrapCredentialPath='mock.dpapi';BootstrapUser='EpicVMBootstrap'}
        $caught=$null
        try { Invoke-EpicVMGuestConfiguration -Provider $provider -Config $config -VmName 'alpha' -DesiredUser 'operator' -DesiredPassword ('y' * 16) } catch { $caught=$_.Exception }
        $caught.ErrorCode | Should -Be 'bootstrap_credential_unavailable'
        $caught.Message | Should -Be 'The machine bootstrap credential could not open the guest channel.'
    }
}
