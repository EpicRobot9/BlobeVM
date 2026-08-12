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
    It 'skips the expensive content hash only for capability readiness' {
        $text = Get-Content (Join-Path $windowsRoot 'Provisioning.ps1') -Raw
        $text | Should -Match 'Test-EpicVMTemplateManifest -Config \$Config -SkipContentHash'
        $text | Should -Match 'if \(-not \$SkipContentHash\)'
        $text | Should -Match 'Test-EpicVMTemplateManifest -Config \$State\.Config\)'
    }

    It 'uses the locked standard and gaming resource profiles' {
        (Get-EpicVMProvisioningProfile -Profile standard).cpuCount | Should -Be 4
        (Get-EpicVMProvisioningProfile -Profile standard).memoryBytes | Should -Be 8589934592
        (Get-EpicVMProvisioningProfile -Profile standard).diskSizeBytes | Should -Be 103079215104
        (Get-EpicVMProvisioningProfile -Profile gaming).cpuCount | Should -Be 6
        (Get-EpicVMProvisioningProfile -Profile gaming).gpuPartition | Should -Be '50%'
        (Get-EpicVMDefaultConfig).VmRoot | Should -Be 'E:\EpicVM\vms'
    }

    It 'fails closed when the template manifest is absent' {
        $config=Get-EpicVMDefaultConfig
        $config.TemplateManifestPath=Join-Path $TestDrive 'missing.json'
        (Test-EpicVMTemplateManifest -Config $config) | Should -BeFalse
        (Test-EpicVMProvisioningPrerequisites -Config $config) | Should -BeFalse
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

    It 'keeps gaming provisioning fail closed before the GPU-P pilot' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'gaming-jobs.json'
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider (New-ProvisioningTestProvider)
        { New-EpicVMProvisioningJob -State $state -Request @{ name='gaming-one'; profile='gaming' } } | Should -Throw
    }

    It 'uses a two-phase credential-free console completion gate' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'two-phase-jobs.json'
        $provider=New-ProvisioningTestProvider
        $provider | Add-Member NoteProperty ConfigureGuest { param($name,$username,$password) @{ok=$true} }
        $provider | Add-Member NoteProperty EnrollTailscale { param($name,$username,$password) @{ok=$true;ip='100.111.82.1';deviceId='device-1'} }
        $provider | Add-Member NoteProperty VerifyGuest { param($name,$ip) $ip -eq '100.111.82.1' }
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider $provider
        $job=New-EpicVMProvisioningJobObject -Id 'job-1' -Name 'alpha' -Profile 'standard' -State 'awaiting_claim'
        $job.claimHash=ConvertTo-EpicVMClaimHash 'single-use'
        $job.claimExpires=[DateTime]::UtcNow.AddMinutes(5).ToString('o')
        $state.Provisioning.Jobs[$job.id]=$job
        $state.Provisioning.Claims[$job.id]=$job

        Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='single-use';username='operator';password='transient-password'}
        $job.state | Should -Be 'awaiting_console'
        $job.claimHash | Should -BeNullOrEmpty
        { Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='single-use';username='operator';password='transient-password'} } | Should -Throw
        Complete-EpicVMProvisioningConsole -State $state -Job $job -Request @{routePrefix='/vm/alpha/';guestTcpVerified=$true}
        $job.state | Should -Be 'ready'
        $job.consoleRoutePrefix | Should -Be '/vm/alpha/'
        (Get-Content -LiteralPath $config.ProvisioningStatePath -Raw) | Should -Not -Match 'transient-password|single-use'
    }

    It 'retains a VM after a post-claim guest mutation failure' {
        $script:deletedAfterClaim=$false
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'retained-jobs.json'
        $provider=New-ProvisioningTestProvider
        $provider.DeleteVM={ param($name) $script:deletedAfterClaim=$true }
        $provider | Add-Member NoteProperty ConfigureGuest { throw 'guest mutation failed' }
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider $provider
        $job=New-EpicVMProvisioningJobObject -Id 'job-2' -Name 'retained' -Profile 'standard' -State 'awaiting_claim'
        $job.vmId='retained'
        $job.claimHash=ConvertTo-EpicVMClaimHash 'single-use'
        $job.claimExpires=[DateTime]::UtcNow.AddMinutes(5).ToString('o')
        $state.Provisioning.Jobs[$job.id]=$job
        $state.Provisioning.Claims[$job.id]=$job
        { Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='single-use';username='operator';password='transient-password'} } | Should -Throw
        $job.state | Should -Be 'failed'
        $script:deletedAfterClaim | Should -BeFalse
    }

    It 'keeps the credential-free console gate stable across agent restart recovery' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'restart-jobs.json'
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider (New-ProvisioningTestProvider)
        $awaiting=New-EpicVMProvisioningJobObject -Id 'job-restart-1' -Name 'alpha' -Profile 'standard' -State 'awaiting_console'
        $awaiting.tailnetIp='100.111.82.1'
        $inFlight=New-EpicVMProvisioningJobObject -Id 'job-restart-2' -Name 'beta' -Profile 'standard' -State 'configuring_guest'
        $state.Provisioning.Jobs[$awaiting.id]=$awaiting
        $state.Provisioning.Jobs[$inFlight.id]=$inFlight
        Invoke-EpicVMProvisioningRecovery -State $state
        $awaiting.state | Should -Be 'awaiting_console'
        $inFlight.state | Should -Be 'failed'
        $inFlight.errorCode | Should -Be 'agent_restarted'
    }

    It 'accepts request-only Sunshine credentials only at the console gate' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'sunshine-jobs.json'
        $script:sunshineReceived=$false
        $provider=New-ProvisioningTestProvider
        $provider | Add-Member NoteProperty ConfigureGuest { param($name,$username,$password) @{ok=$true} }
        $provider | Add-Member NoteProperty ConfigureSunshine { param($name,$guestUsername,$guestPassword,$sunshineUsername,$sunshinePassword) $script:sunshineReceived=($sunshineUsername -eq 'sun-user' -and $sunshinePassword -eq 'sun-pass'); @{ok=$true} }
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider $provider
        $job=New-EpicVMProvisioningJobObject -Id 'job-sunshine' -Name 'sunshine' -Profile 'standard' -State 'awaiting_console'
        $state.Provisioning.Jobs[$job.id]=$job
        $response=Invoke-EpicVMApiRequest -State $state -Method 'POST' -Path '/v1/provisioning-jobs/job-sunshine/console-credentials' -Headers @{Authorization='Bearer agent-token'} -Body (@{username='operator';password='guest-pass';sunshineUsername='sun-user';sunshinePassword='sun-pass'} | ConvertTo-Json)
        $response.StatusCode | Should -Be 200
        $script:sunshineReceived | Should -BeTrue
        $response.Json | Should -Not -Match 'guest-pass|sun-pass'
        (Get-Content -LiteralPath $config.ProvisioningStatePath -Raw) | Should -Not -Match 'guest-pass|sun-pass'
    }
}
