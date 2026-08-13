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
            TestBootstrapGuest={ param($name,$timeout,$poll) $true }
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

    It 'requires bootstrap readiness before issuing a one-time claim' {
        $text = Get-Content (Join-Path $windowsRoot 'Provisioning.ps1') -Raw
        $text | Should -Match 'TestBootstrapGuest'
        $text | Should -Match 'guest_bootstrap_not_ready'
        $text | Should -Match 'Guest.*10-minute|\$Job\.name 600 1000'
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
        $job=New-EpicVMProvisioningJobObject -Id 'job-1' -Name 'alpha' -Profile 'standard' -State 'unclaimed'
        $job.claimHash=ConvertTo-EpicVMClaimHash 'single-use'
        $job.claimExpires=[DateTime]::UtcNow.AddMinutes(5).ToString('o')
        $state.Provisioning.Jobs[$job.id]=$job
        $state.Provisioning.Claims[$job.id]=$job

        Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='single-use';username='operator';password='transient-password'}
        $job.state | Should -Be 'streaming_setup'
        $job.claimConsumed | Should -BeTrue
        $job.operationId | Should -Match '^[0-9a-f]{32}$'
        $job.completedStages | Should -Be @('claim','guest_setup','network_setup')
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
        $job=New-EpicVMProvisioningJobObject -Id 'job-2' -Name 'retained' -Profile 'standard' -State 'unclaimed'
        $job.vmId='retained'
        $job.claimHash=ConvertTo-EpicVMClaimHash 'single-use'
        $job.claimExpires=[DateTime]::UtcNow.AddMinutes(5).ToString('o')
        $state.Provisioning.Jobs[$job.id]=$job
        $state.Provisioning.Claims[$job.id]=$job
        { Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='single-use';username='operator';password='transient-password'} } | Should -Throw
        $job.state | Should -Be 'setup_failed:guest'
        $job.errorCode | Should -Be 'guest_account_failed'
        $job.claimConsumed | Should -BeTrue
        $script:deletedAfterClaim | Should -BeFalse
    }

    It 'keeps the credential-free console gate stable across agent restart recovery' {
        $config=Get-EpicVMDefaultConfig
        $config.ProvisioningStatePath=Join-Path $TestDrive 'restart-jobs.json'
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider (New-ProvisioningTestProvider)
        $awaiting=New-EpicVMProvisioningJobObject -Id 'job-restart-1' -Name 'alpha' -Profile 'standard' -State 'streaming_setup'
        $awaiting.tailnetIp='100.111.82.1'
        $awaiting.completedStages=@('claim','guest_setup','network_setup')
        $inFlight=New-EpicVMProvisioningJobObject -Id 'job-restart-2' -Name 'beta' -Profile 'standard' -State 'guest_setup'
        $state.Provisioning.Jobs[$awaiting.id]=$awaiting
        $state.Provisioning.Jobs[$inFlight.id]=$inFlight
        Invoke-EpicVMProvisioningRecovery -State $state
        $awaiting.state | Should -Be 'setup_failed:agent_restart'
        $inFlight.state | Should -Be 'setup_failed:agent_restart'
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
        $job=New-EpicVMProvisioningJobObject -Id 'job-sunshine' -Name 'sunshine' -Profile 'standard' -State 'streaming_setup'
        $state.Provisioning.Jobs[$job.id]=$job
        $response=Invoke-EpicVMApiRequest -State $state -Method 'POST' -Path '/v1/provisioning-jobs/job-sunshine/console-credentials' -Headers @{Authorization='Bearer agent-token'} -Body (@{username='operator';password='guest-pass';sunshineUsername='sun-user';sunshinePassword='sun-pass'} | ConvertTo-Json)
        $response.StatusCode | Should -Be 200
        $script:sunshineReceived | Should -BeTrue
        $response.Json | Should -Not -Match 'guest-pass|sun-pass'
        (Get-Content -LiteralPath $config.ProvisioningStatePath -Raw) | Should -Not -Match 'guest-pass|sun-pass'
    }
}

Describe 'EpicVM claim state and migration boundaries' {
    It 'rejects malformed credential input without consuming the claim' {
        $config=Get-EpicVMDefaultConfig; $config.ProvisioningStatePath=Join-Path $TestDrive 'invalid-input.json'
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider (New-ProvisioningTestProvider)
        $job=New-EpicVMProvisioningJobObject -Id 'invalid-input' -Name 'invalid-input' -Profile 'standard' -State 'unclaimed'
        $job.claimHash=ConvertTo-EpicVMClaimHash 'valid-claim'; $job.claimExpires=[DateTime]::UtcNow.AddMinutes(5).ToString('o')
        $state.Provisioning.Jobs[$job.id]=$job; $state.Provisioning.Claims[$job.id]=$job
        $caught=$null
        try { Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='valid-claim';username='bad';password=''} } catch { $caught=$_.Exception }
        $caught.ErrorCode | Should -Be 'invalid_credential_input'
        $job.state | Should -Be 'unclaimed'; $job.claimConsumed | Should -BeFalse; $job.claimHash | Should -Not -BeNullOrEmpty
    }

    It 'normalizes legacy labels without manufacturing checkpoints' {
        $legacy=New-EpicVMProvisioningJobObject -Id 'legacy' -Name 'legacy' -Profile 'standard' -State 'enrolling_tailscale'
        (ConvertTo-EpicVMCanonicalProvisioningState -Record $legacy) | Should -Be 'network_setup'
        $legacy.completedStages | Should -BeNullOrEmpty
        $legacy.state='awaiting_console'
        (ConvertTo-EpicVMCanonicalProvisioningState -Record $legacy) | Should -Be 'setup_failed:legacy_state_uncertain'
    }

    It 'does not resurrect a consumed claim after reload' {
        $config=Get-EpicVMDefaultConfig; $config.ProvisioningStatePath=Join-Path $TestDrive 'consumed.json'
        $job=New-EpicVMProvisioningJobObject -Id 'consumed' -Name 'consumed' -Profile 'standard' -State 'claim_in_progress'
        $job.claimConsumed=$true; $job.claimUsed=$true; $job.operationId='0123456789abcdef0123456789abcdef'; $job.completedStages=@('claim')
        $store=[pscustomobject]@{Path=$config.ProvisioningStatePath;Jobs=@{$job.id=$job};Deprovisioning=@{};Claims=@{}}
        Save-EpicVMProvisioningStore -Store $store
        $reloaded=New-EpicVMProvisioningStore -Config $config
        $reloaded.Claims.ContainsKey($job.id) | Should -BeFalse
        $reloaded.Jobs[$job.id].claimConsumed | Should -BeTrue
    }

    It 'does not consume a claim when the atomic state write fails' {
        $config=Get-EpicVMDefaultConfig; $config.ProvisioningStatePath=Join-Path $TestDrive 'atomic-failure.json'
        $provider=New-ProvisioningTestProvider
        $provider | Add-Member NoteProperty ConfigureGuest { param($name,$username,$password) @{ok=$true} }
        $state=New-EpicVMAgentState -Config $config -Token 'agent-token' -Provider $provider
        $job=New-EpicVMProvisioningJobObject -Id 'atomic-failure' -Name 'atomic-failure' -Profile 'standard' -State 'unclaimed'
        $job.claimHash=ConvertTo-EpicVMClaimHash 'valid-claim'; $job.claimExpires=[DateTime]::UtcNow.AddMinutes(5).ToString('o')
        $state.Provisioning.Jobs[$job.id]=$job; $state.Provisioning.Claims[$job.id]=$job
        Mock -CommandName Save-EpicVMProvisioningStore -MockWith { throw 'simulated atomic write failure' }
        $caught=$null
        try { Invoke-EpicVMProvisioningClaim -State $state -Job $job -Request @{claimToken='valid-claim';username='operator';password='transient-password'} } catch { $caught=$_.Exception }
        $caught.ErrorCode | Should -Be 'claim_atomic_commit_failed'
        $job.state | Should -Be 'unclaimed'; $job.claimConsumed | Should -BeFalse
        $job.claimHash | Should -Be (ConvertTo-EpicVMClaimHash 'valid-claim')
        $state.Provisioning.Claims.ContainsKey($job.id) | Should -BeTrue
    }

    It 'serializes concurrent claim owners with the provisioning mutex' {
        $mutex=[Threading.Mutex]::new($false,'Local\EpicVM-ProvisioningState')
        $held=$false
        try {
            $held=$mutex.WaitOne(1000)
            $worker=Start-Job -ScriptBlock {
                $other=[Threading.Mutex]::new($false,'Local\EpicVM-ProvisioningState')
                try { [bool]$other.WaitOne(100) } finally { $other.Dispose() }
            }
            Wait-Job -Job $worker -Timeout 10 | Out-Null
            $acquired=[bool](Receive-Job -Job $worker -ErrorAction Stop | Select-Object -Last 1)
            Remove-Job -Job $worker -Force -ErrorAction SilentlyContinue
            $held | Should -BeTrue
            $acquired | Should -BeFalse
        } finally { if($held){$mutex.ReleaseMutex()};$mutex.Dispose() }
    }

    It 'keeps an expired legacy claim unclaimed only for diagnosis, never for reuse' {
        $legacy=New-EpicVMProvisioningJobObject -Id 'expired-legacy' -Name 'expired-legacy' -Profile 'standard' -State 'awaiting_claim'
        $legacy.claimHash='a' * 64
        $legacy.claimExpires=[DateTime]::UtcNow.AddMinutes(-1).ToString('o')
        (ConvertTo-EpicVMCanonicalProvisioningState -Record $legacy) | Should -Be 'setup_failed:legacy_state_uncertain'
    }
}
