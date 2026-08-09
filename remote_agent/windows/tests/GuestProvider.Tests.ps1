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
            PowerShellDirectInvoker={ param($vm,$credential,$scriptBlock,$args) $script:invoked=$true; @{ ok=$true; listener=$true; firewallScoped=$true; bootstrapRemoved=$true } }
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
        $text | Should -Match 'EpicVM-RDP-Tailscale'
        $text | Should -Match 'Remove-LocalUser'
    }
}
