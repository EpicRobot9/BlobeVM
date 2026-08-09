# Requires -Version 7.0
# Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'providers/HyperVProvider.ps1')
    . (Join-Path $windowsRoot 'EpicVM.Agent.ps1') -NoStart

    function New-TestProvider {
        [pscustomobject]@{
            Name = 'Mock'
            GetCapabilities = { @{ provider = 'Mock'; available = $true; features = @('lifecycle') } }
            GetVMs = { @(@{ name = 'alpha'; state = 'Off'; managed = $true }) }
            CreateVM = { param($request) @{ name = $request.Name; state = 'Off'; managed = $true } }
            StartVM = { param($name) @{ name = $name; state = 'Running'; managed = $true } }
            StopVM = { param($name) @{ name = $name; state = 'Off'; managed = $true } }
            RestartVM = { param($name) @{ name = $name; state = 'Running'; managed = $true } }
            DeleteVM = { param($name) @{ name = $name; deleted = $true } }
        }
    }
}

Describe 'EpicVM agent authentication' {
    It 'compares bearer tokens without accepting a near match' {
        Test-EpicVMBearerToken -ProvidedToken 'correct-token' -ExpectedToken 'correct-token' | Should -BeTrue
        Test-EpicVMBearerToken -ProvidedToken 'correct-toke' -ExpectedToken 'correct-token' | Should -BeFalse
        Test-EpicVMBearerToken -ProvidedToken 'correct-tokenx' -ExpectedToken 'correct-token' | Should -BeFalse
        Test-EpicVMBearerToken -ProvidedToken '' -ExpectedToken 'correct-token' | Should -BeFalse
    }

    It 'requires the bearer token and never echoes it in the error response' {
        $state = New-EpicVMAgentState -Config (Get-EpicVMDefaultConfig) -Token 'test-secret-token' -Provider (New-TestProvider)
        $response = Invoke-EpicVMApiRequest -State $state -Method 'GET' -Path '/v1/health' -Headers @{ Authorization = 'Bearer wrong-token' }

        $response.StatusCode | Should -Be 401
        ($response.Body | ConvertTo-Json -Depth 10) | Should -Not -Match 'test-secret-token'
        ($response.Body | ConvertTo-Json -Depth 10) | Should -Not -Match 'wrong-token'
    }
}

Describe 'EpicVM agent capabilities and routing' {
    BeforeEach {
        $config = Get-EpicVMDefaultConfig
        $config.Provider = 'Mock'
        $script:state = New-EpicVMAgentState -Config $config -Token 'test-secret-token' -Provider (New-TestProvider)
    }

    It 'returns JSON-ready capabilities for an authenticated request' {
        $response = Invoke-EpicVMApiRequest -State $state -Method 'GET' -Path '/v1/capabilities' -Headers @{ Authorization = 'Bearer test-secret-token' }

        $response.StatusCode | Should -Be 200
        $response.Body.provider | Should -Be 'Mock'
        $response.Body.available | Should -BeTrue
    }

    It 'rejects unknown routes with a clean JSON error' {
        $response = Invoke-EpicVMApiRequest -State $state -Method 'GET' -Path '/v1/does-not-exist' -Headers @{ Authorization = 'Bearer test-secret-token' }

        $response.StatusCode | Should -Be 404
        $response.Body.error.code | Should -Be 'not_found'
    }

    It 'uses the documented lifecycle routes and DELETE verb' {
        $start = Invoke-EpicVMApiRequest -State $state -Method 'POST' -Path '/v1/vms/alpha/start' -Headers @{ Authorization = 'Bearer test-secret-token' }
        $delete = Invoke-EpicVMApiRequest -State $state -Method 'DELETE' -Path '/v1/vms/alpha' -Headers @{ Authorization = 'Bearer test-secret-token' }

        $start.StatusCode | Should -Be 200
        $delete.StatusCode | Should -Be 200
        $delete.Body.ok | Should -BeTrue
    }

    It 'validates VM names against the public contract' {
        $longName = ('a' * 63) -join ''
        foreach ($name in @('alpha', 'dev.box-01', $longName)) {
            Test-EpicVMName -Name $name | Should -BeTrue
        }

        $tooLongName = ('a' * 64) -join ''
        foreach ($name in @('', 'Aalpha', '-alpha', 'alpha/', $tooLongName, 'alpha name')) {
            Test-EpicVMName -Name $name | Should -BeFalse
        }
    }
}

Describe 'EpicVM provider selection' {
    It 'selects the configured Hyper-V adapter and rejects unknown providers' {
        $config = Get-EpicVMDefaultConfig
        $config.Provider = 'HyperV'
        $provider = New-EpicVMProvider -Config $config -CommandInvoker { param($commandName, $parameters) @() }
        $provider.Name | Should -Be 'HyperV'

        $config.Provider = 'OtherProvider'
        { New-EpicVMProvider -Config $config } | Should -Throw '*Unsupported provider*'
    }
}

Describe 'EpicVM agent binding defaults' {
    It 'does not default to an all-interface listener' {
        (Get-EpicVMDefaultConfig).BindAddress | Should -Not -Be '0.0.0.0'
    }

    It 'bounds request bodies before JSON dispatch' {
        $stream = [System.IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes('12345'))
        try {
            $result = Read-EpicVMBoundedBody -Stream $stream -MaxBytes 4
        }
        finally {
            $stream.Dispose()
        }
        $result.TooLarge | Should -BeTrue
    }

    It 'accepts only Tailscale or loopback addresses' {
        Test-EpicVMBindAddress -Address '100.64.12.34' | Should -BeTrue
        Test-EpicVMBindAddress -Address '127.0.0.1' | Should -BeTrue
        Test-EpicVMBindAddress -Address '192.168.1.20' | Should -BeFalse
        Test-EpicVMBindAddress -Address '0.0.0.0' | Should -BeFalse
    }

    It 'ships a guided Windows setup wrapper' {
        $setupPath = Join-Path $windowsRoot 'setup.ps1'
        Test-Path -LiteralPath $setupPath | Should -BeTrue
        $setup = Get-Content -LiteralPath $setupPath -Raw
        $setup | Should -Match 'TailscaleAddress'
        $setup | Should -Match 'SwitchName'
        $setup | Should -Match 'install.ps1'
    }

    It 'keeps the PowerShell agent behind the NSSM service wrapper' {
        $install = Get-Content -LiteralPath (Join-Path $windowsRoot 'install.ps1') -Raw
        $install | Should -Match 'nssm\.exe'
        $install | Should -Match 'AppParameters'
        $install | Should -Not -Match 'New-Service'
    }

    It 'ships a downloader that invokes guided setup' {
        $downloadPath = Join-Path $windowsRoot 'download-setup.ps1'
        Test-Path -LiteralPath $downloadPath | Should -BeTrue
        $download = Get-Content -LiteralPath $downloadPath -Raw
        $download | Should -Match 'Invoke-WebRequest'
        $download | Should -Match 'setup.ps1'
        $download | Should -Match 'RemoteVM'
    }
}
