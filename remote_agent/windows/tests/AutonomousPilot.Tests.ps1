# Requires -Version 7.0
# Requires -Modules Pester

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    . (Join-Path $repoRoot 'scripts\EpicVMAutonomousPilot.ps1')
}

Describe 'EpicVM autonomous pilot command' {
    It 'accepts both direct ssh JSON and diagnostic process envelopes' {
        $direct = ConvertFrom-EpicVMAutonomousMoonlightResult -Stdout '{"ok":true,"operationId":"direct-op","routeReady":true}'
        $envelope = @{ exit = 0; out = '{"ok":true,"operationId":"enveloped-op","routeReady":true}' } | ConvertTo-Json -Compress
        $wrapped = ConvertFrom-EpicVMAutonomousMoonlightResult -Stdout $envelope

        $direct.ok | Should -BeTrue
        $direct.operationId | Should -Be 'direct-op'
        $wrapped.ok | Should -BeTrue
        $wrapped.operationId | Should -Be 'enveloped-op'
    }

    It 'supports create-only handoff without persisting the one-time claim' {
        Mock Get-EpicVMAutonomousConfig {
            [pscustomobject]@{ BaseUrl = 'http://127.0.0.1:9999'; Token = 'test-agent-token' }
        }
        Mock Invoke-EpicVMAutonomousJson {
            param([string]$Method,[string]$Uri,[hashtable]$Headers,[object]$Body,[int]$TimeoutSec,[string]$Stage)
            [pscustomobject]@{
                StatusCode = 202
                Payload = [pscustomobject]@{
                    ok = $true
                    claimToken = 'claim-material-must-not-be-written'
                    job = [pscustomobject]@{
                        id = 'job-create-only'
                        name = 'alpha-one'
                        profile = 'standard'
                        state = 'unclaimed'
                        claimConsumed = $false
                    }
                }
            }
        }

        $reportPath = Join-Path $TestDrive 'create-only.json'
        $result = Invoke-EpicVMAutonomousPilot -Name 'alpha-one' -Mode CreateOnly -ConfigPath 'ignored' -ReportPath $reportPath

        $result.ok | Should -BeTrue
        $result.stage | Should -Be 'create_only'
        $result.claimAvailable | Should -BeTrue
        $raw = Get-Content -LiteralPath $reportPath -Raw
        $raw | Should -Not -Match 'claim-material-must-not-be-written'
        $raw | Should -Match 'job-create-only'
    }

    It 'runs create, claim, console, and ready stages without exposing credentials' {
        Mock Get-EpicVMAutonomousConfig {
            [pscustomobject]@{ BaseUrl = 'http://127.0.0.1:9999'; Token = 'test-agent-token' }
        }
        Mock Get-EpicVMAutonomousCredentialBundle {
            [pscustomobject]@{
                GuestUsername = 'operator'
                GuestPassword = 'guest-secret-fixture'
                SunshineUsername = 'sunshine'
                SunshinePassword = 'sunshine-secret-fixture'
            }
        }
        Mock Invoke-EpicVMAutonomousMoonlight {
            [pscustomobject]@{ ok = $true; operationId = 'operation-1'; routeReady = $true }
        }
        Mock Invoke-EpicVMAutonomousJson {
            param([string]$Method,[string]$Uri,[hashtable]$Headers,[object]$Body,[int]$TimeoutSec,[string]$Stage)
            switch ($Stage) {
                'create' {
                    return [pscustomobject]@{
                        StatusCode = 202
                        Payload = [pscustomobject]@{
                            ok = $true
                            claimToken = 'claim-token-fixture'
                            job = [pscustomobject]@{
                                id = 'job-autonomous'
                                name = 'alpha-auto'
                                profile = 'standard'
                                state = 'unclaimed'
                                claimConsumed = $false
                            }
                        }
                    }
                }
                'claim' {
                    return [pscustomobject]@{
                        StatusCode = 200
                        Payload = [pscustomobject]@{
                            ok = $true
                            job = [pscustomobject]@{
                                id = 'job-autonomous'
                                name = 'alpha-auto'
                                profile = 'standard'
                                state = 'streaming_setup'
                                tailnetIp = '100.100.50.10'
                                claimConsumed = $true
                                completedStages = @('claim','guest_setup','network_setup','management_handoff')
                            }
                        }
                    }
                }
                'finalize' {
                    return [pscustomobject]@{
                        StatusCode = 200
                        Payload = [pscustomobject]@{
                            ok = $true
                            job = [pscustomobject]@{
                                id = 'job-autonomous'
                                name = 'alpha-auto'
                                profile = 'standard'
                                state = 'ready'
                                tailnetIp = '100.100.50.10'
                                vmId = 'vm-autonomous'
                                consoleRoutePrefix = '/vm/alpha-auto--epic-pc/'
                                claimConsumed = $true
                                completedStages = @('claim','guest_setup','network_setup','management_handoff','streaming_setup','stream_validation')
                            }
                        }
                    }
                }
            }
        }

        $reportPath = Join-Path $TestDrive 'autonomous.json'
        $result = Invoke-EpicVMAutonomousPilot -Name 'alpha-auto' -ConfigPath 'ignored' -ReportPath $reportPath

        $result.ok | Should -BeTrue
        $result.stage | Should -Be 'ready'
        $result.operationId | Should -Be 'operation-1'
        $result.routeReady | Should -BeTrue
        $result.job.state | Should -Be 'ready'
        $result.job.completedStages | Should -Contain 'stream_validation'
        $raw = Get-Content -LiteralPath $reportPath -Raw
        $raw | Should -Not -Match 'guest-secret-fixture|sunshine-secret-fixture|claim-token-fixture'
        $raw | Should -Match 'operation-1'
    }

    It 'fails closed for an invalid dashboard target before sending credentials' {
        { Invoke-EpicVMAutonomousMoonlight -DashboardHost 'bad host' -DashboardContainer 'blobedash' -HostId 'epic-pc' -JobId 'job-1' -Name 'alpha-one' -GuestIp '100.100.50.10' -GuestUsername 'operator' -GuestPassword 'fixture' -SunshineUsername 'sunshine' -SunshinePassword 'fixture' } | Should -Throw
    }
}
