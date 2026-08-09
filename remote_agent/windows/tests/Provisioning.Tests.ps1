# Requires -Version 7.0
# Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'EpicVM.Agent.ps1') -NoStart

    function New-ProvisioningTestProvider {
        [pscustomobject]@{
            Name='Mock'
            GetVMs={ @() }
            CreateVM={ param($request) @{ name=$request.name; state='Off'; managed=$true } }
            StartVM={ param($name) @{ name=$name; state='Running'; managed=$true } }
            StopVM={ param($name) @{ name=$name; state='Off'; managed=$true } }
            DeleteVM={ param($name) @{ name=$name; deleted=$true } }
        }
    }
}

Describe 'EpicVM provisioning safety' {
    It 'uses the locked standard and gaming resource profiles' {
        (Get-EpicVMProvisioningProfile -Profile standard).cpuCount | Should -Be 4
        (Get-EpicVMProvisioningProfile -Profile standard).memoryBytes | Should -Be 8589934592
        (Get-EpicVMProvisioningProfile -Profile standard).diskSizeBytes | Should -Be 103079215104
        (Get-EpicVMProvisioningProfile -Profile gaming).cpuCount | Should -Be 6
        (Get-EpicVMProvisioningProfile -Profile gaming).gpuPartition | Should -Be '50%'
    }

    It 'fails closed when the template manifest is absent' {
        $config=Get-EpicVMDefaultConfig
        $config.TemplateManifestPath=Join-Path $TestDrive 'missing.json'
        (Test-EpicVMTemplateManifest -Config $config) | Should -BeFalse
    }

    It 'persists only redacted job fields and never claim material' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'jobs.json'
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider (New-ProvisioningTestProvider)
        $job=New-EpicVMProvisioningJob -State $state -Request @{ name='alpha'; profile='standard'; username='operator'; password='never-persist-this' }
        $raw=Get-Content -LiteralPath $config.ProvisioningStatePath -Raw
        $raw | Should -Not -Match 'never-persist-this'
        (ConvertTo-EpicVMRedactedJob -Job $job).Keys | Should -Not -Contain 'password'
    }

    It 'requires exact-name confirmation before teardown' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'jobs.json'
        $provider=New-ProvisioningTestProvider
        $provider.GetVMs={ @(@{ name='alpha'; state='Off'; managed=$true }) }
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider $provider
        { New-EpicVMDeprovisioningJob -State $state -Request @{ name='alpha'; confirmName='ALPHA' } } | Should -Throw
    }
}
