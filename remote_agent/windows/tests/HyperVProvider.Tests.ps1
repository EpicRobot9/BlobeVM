# Requires -Version 7.0
# Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'providers/HyperVProvider.ps1')

    $script:vmState = @{
        alpha = [pscustomobject]@{ Name = 'alpha'; State = 'Off'; Notes = 'EpicVM-Managed: true'; Path = 'C:\EpicVM\VMs\alpha' }
        manual = [pscustomobject]@{ Name = 'manual'; State = 'Off'; Notes = 'Owned by the Windows administrator'; Path = 'C:\EpicVM\VMs\manual' }
    }
    $script:commandCalls = [System.Collections.Generic.List[object]]::new()

    function Invoke-MockHyperVCmdlet {
        param(
            [Parameter(Mandatory)] [string] $CommandName,
            [hashtable] $Parameters
        )

        $script:commandCalls.Add([pscustomobject]@{
                Name = $CommandName
                Parameters = if ($null -eq $Parameters) { @{} } else { $Parameters }
            })

        switch ($CommandName) {
            'Get-VM' {
                if ($Parameters.ContainsKey('Name')) {
                    $candidate = $script:vmState[$Parameters.Name]
                    if ($null -eq $candidate) { throw "VM was not found" }
                    return $candidate
                }
                return @($script:vmState.Values)
            }
            'Get-VMHost' {
                return [pscustomobject]@{
                    LogicalProcessorCount = 16
                    MemoryCapacity = 68719476736
                    VirtualMachinePath = 'C:\Hyper-V'
                }
            }
            'Get-VMSwitch' { return [pscustomobject]@{ Name = 'EpicVM'; SwitchType = 'Internal' } }
            'Start-VM' {
                $script:vmState[$Parameters.Name].State = 'Running'
                return $script:vmState[$Parameters.Name]
            }
            'Stop-VM' {
                $script:vmState[$Parameters.Name].State = 'Off'
                return $script:vmState[$Parameters.Name]
            }
            'Restart-VM' {
                $script:vmState[$Parameters.Name].State = 'Running'
                return $script:vmState[$Parameters.Name]
            }
            'Remove-VM' {
                $script:vmState.Remove($Parameters.Name)
                return $null
            }
            'New-VHD' { return [pscustomobject]@{ Path = $Parameters.Path; Size = $Parameters.SizeBytes } }
            'New-VM' {
                $vm = [pscustomobject]@{ Name = $Parameters.Name; State = 'Off'; Notes = ''; Path = $Parameters.Path }
                $script:vmState[$Parameters.Name] = $vm
                return $vm
            }
            'Set-VM' {
                $script:vmState[$Parameters.Name].Notes = $Parameters.Notes
                return $script:vmState[$Parameters.Name]
            }
            'Set-VMProcessor' { return $null }
            default { throw "Unexpected mocked Hyper-V command: $CommandName" }
        }
    }

    function New-TestHyperVProvider {
        $config = @{
            VmRoot = 'C:\EpicVM\VMs'
            SwitchName = 'EpicVM'
            DefaultMemoryBytes = 4294967296
            DefaultCpuCount = 2
            DefaultDiskSizeBytes = 68719476736
            MinMemoryBytes = 536870912
            MaxMemoryBytes = 17179869184
            MinCpuCount = 1
            MaxCpuCount = 16
            MaxDiskSizeBytes = 549755813888
            Generation = 2
        }
        New-EpicVMHyperVProvider -Config $config -CommandInvoker ${function:Invoke-MockHyperVCmdlet}
    }
}

Describe 'Hyper-V provider capabilities' {
    BeforeEach {
        $script:commandCalls.Clear()
        $script:provider = New-TestHyperVProvider
    }

    It 'reports cmdlet-backed host resources and feature capabilities' {
        $capabilities = & $provider.GetCapabilities

        $capabilities.provider | Should -Be 'HyperV'
        $capabilities.available | Should -BeTrue
        $capabilities.create_vm | Should -BeTrue
        $capabilities.start | Should -BeTrue
        $capabilities.stop | Should -BeTrue
        $capabilities.restart | Should -BeTrue
        $capabilities.delete | Should -BeTrue
        $capabilities.provisioning | Should -BeFalse
        $capabilities.features | Should -Contain 'lifecycle'
        $capabilities.resources.logicalProcessorCount | Should -Be 16
        $capabilities.resources.memoryCapacityBytes | Should -Be 68719476736
    }

    It 'projects allowlisted Tailscale configuration onto the enrollment provider' {
        $provider = New-EpicVMHyperVProvider -Config @{
            TailscaleOAuthClientId='client-id'
            TailscaleOAuthSecretPath='C:\ProgramData\EpicVM\agent\tailscale-oauth.dpapi'
            TailscaleTailnet='example.ts.net'
            TailscaleGuestTag='tag:epicvm-guest'
        } -CommandInvoker ${function:Invoke-MockHyperVCmdlet}

        $provider.TailscaleOAuthClientId | Should -Be 'client-id'
        $provider.TailscaleOAuthSecretPath | Should -Be 'C:\ProgramData\EpicVM\agent\tailscale-oauth.dpapi'
        $provider.TailscaleTailnet | Should -Be 'example.ts.net'
        $provider.TailscaleGuestTag | Should -Be 'tag:epicvm-guest'
    }
}

Describe 'Hyper-V provider lifecycle safety' {
    BeforeEach {
        $script:commandCalls.Clear()
        $script:vmState = @{
            alpha = [pscustomobject]@{ Name = 'alpha'; State = 'Off'; Notes = 'EpicVM-Managed: true'; Path = 'C:\EpicVM\VMs\alpha' }
            manual = [pscustomobject]@{ Name = 'manual'; State = 'Off'; Notes = 'Owned by the Windows administrator'; Path = 'C:\EpicVM\VMs\manual' }
        }
        $script:provider = New-TestHyperVProvider
    }

    It 'starts, stops, and restarts a managed VM through the adapter' {
        (& $provider.StartVM 'alpha').state | Should -Be 'Running'
        (& $provider.StopVM 'alpha').state | Should -Be 'Off'
        $script:vmState.alpha.State = 'Running'
        (& $provider.RestartVM 'alpha').state | Should -Be 'Running'

        @($script:commandCalls | ForEach-Object Name) | Should -Contain 'Start-VM'
        @($script:commandCalls | ForEach-Object Name) | Should -Contain 'Stop-VM'
        @($script:commandCalls | ForEach-Object Name) | Should -Contain 'Restart-VM'
    }

    It 'refuses to delete a VM without the EpicVM ownership marker' {
        { & $provider.DeleteVM 'manual' } | Should -Throw '*not owned by EpicVM*'
        @($script:commandCalls | Where-Object Name -eq 'Remove-VM') | Should -BeNullOrEmpty
    }

    It 'deletes only a managed VM and uses force-free removal parameters' {
        $result = & $provider.DeleteVM 'alpha'

        $result.deleted | Should -BeTrue
        $script:vmState.ContainsKey('alpha') | Should -BeFalse
        $removeCall = @($script:commandCalls | Where-Object Name -eq 'Remove-VM') | Select-Object -First 1
        $removeCall.Parameters.Force | Should -BeTrue
    }

    It 'refuses to delete a marked VM outside the configured EpicVM root' {
        $script:vmState.outside = [pscustomobject]@{
            Name = 'outside'; State = 'Off'; Notes = 'EpicVM-Managed: true'; Path = 'C:\Other\outside'
        }

        { & $provider.DeleteVM 'outside' } | Should -Throw '*managed root*'
        @($script:commandCalls | Where-Object Name -eq 'Remove-VM') | Should -BeNullOrEmpty
    }
}
