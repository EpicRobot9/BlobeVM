# Requires -Version 7.0
# Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'providers/HyperVProvider.ps1')

function Assert-GamingIntegrationEqual {
    param([AllowNull()]$Actual,[AllowNull()]$Expected,[Parameter(Mandatory)][string]$Label)
    if ($Actual -ne $Expected) { throw ("{0}: expected '{1}', got '{2}'" -f $Label, $Expected, $Actual) }
}

function Assert-GamingIntegrationContains {
    param([AllowNull()][string]$Actual,[Parameter(Mandatory)][string]$Expected,[Parameter(Mandatory)][string]$Label)
    if ($Actual.IndexOf($Expected, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw ("{0}: expected '{1}' to contain '{2}'" -f $Label, $Actual, $Expected)
    }
}

function New-GamingIntegrationFixture {
    $script:commandCalls = [System.Collections.Generic.List[object]]::new()
    $script:gpuAdapters = @()
    $script:vm = [pscustomobject]@{
        Name = 'game'
        State = 'Running'
        Notes = "EpicVM-Managed: true`r`nEpicVM-Profile: gaming"
        Path = 'C:\EpicVM\VMs\game'
    }
    $script:partitionableGpu = [pscustomobject]@{
        Name = '\\?\PCI#VEN_1002&DEV_73BF#rx6800'
        MaxPartitionVRAM = 1000000000
        MaxPartitionDecode = 1000000000
        MaxPartitionCompute = 1000000000
        MaxPartitionEncode = [uint64]::MaxValue
    }

    function Invoke-GamingIntegrationMock {
        param([Parameter(Mandatory)][string]$CommandName,[hashtable]$Parameters)
        $script:commandCalls.Add([pscustomobject]@{ Name=$CommandName; Parameters=$Parameters })
        switch ($CommandName) {
            'Get-VM' { return $script:vm }
            'Get-VMHostPartitionableGpu' { return @($script:partitionableGpu) }
            'Get-VMGpuPartitionAdapter' { return @($script:gpuAdapters) }
            'Add-VMGpuPartitionAdapter' {
                $script:gpuAdapters = @([pscustomobject]@{ InstancePath=$Parameters.InstancePath })
                return $script:gpuAdapters[0]
            }
            'Set-VMGpuPartitionAdapter' { return $null }
            'Set-VM' { return $script:vm }
            'Set-VMMemory' { return $null }
            'Stop-VM' { $script:vm.State='Off'; return $script:vm }
            'Start-VM' { $script:vm.State='Running'; return $script:vm }
            default { throw ("Unexpected command: {0}" -f $CommandName) }
        }
    }

    $config = @{
        VmRoot = 'C:\EpicVM\VMs'
        GamingGpuDeviceIdentity = 'VEN_1002&DEV_73BF'
        DefaultMemoryBytes = 12884901888
        DefaultCpuCount = 6
        DefaultDiskSizeBytes = 137438953472
        MinMemoryBytes = 536870912
        MaxMemoryBytes = 17179869184
        MinCpuCount = 1
        MaxCpuCount = 16
        MaxDiskSizeBytes = 549755813888
    }
    $script:provider = New-EpicVMHyperVProvider -Config $config -CommandInvoker ${function:Invoke-GamingIntegrationMock}
}
}

Describe 'Gaming Hyper-V initialization contract' {
    BeforeEach { New-GamingIntegrationFixture }

    It 'keeps Gaming resource defaults explicit and allows initialization percentage override' {
        $options = Get-EpicVMHyperVCreateOptions -Provider $script:provider -Request @{
            Name = 'game'
            Profile = 'gaming'
            Gpu = $true
            GpuPartitionPercent = 62
        }

        Assert-GamingIntegrationEqual $options.profile 'gaming' 'profile'
        Assert-GamingIntegrationEqual $options.cpuCount 6 'cpuCount'
        Assert-GamingIntegrationEqual $options.memoryBytes 12884901888 'memoryBytes'
        Assert-GamingIntegrationEqual $options.diskSizeBytes 137438953472 'diskSizeBytes'
        Assert-GamingIntegrationEqual $options.gpuPartitionPercent 62 'gpuPartitionPercent'
    }

    It 'applies the testre VM settings and exactly one matching adapter with 50 percent quotas' {
        $settings = Set-EpicVMHyperVGamingVmProperties -Provider $script:provider -Name 'game'
        $adapterResult = Set-EpicVMHyperVGamingGpuPartitionAdapter -Provider $script:provider -Name 'game' -Percent 50

        Assert-GamingIntegrationEqual $settings.LowMemoryMappedIoSpace 3221225472 'LowMemoryMappedIoSpace'
        Assert-GamingIntegrationEqual $settings.HighMemoryMappedIoSpace 34359738368 'HighMemoryMappedIoSpace'
        Assert-GamingIntegrationEqual $settings.GuestControlledCacheTypes $true 'GuestControlledCacheTypes'
        Assert-GamingIntegrationEqual $settings.AutomaticStopAction 'ShutDown' 'AutomaticStopAction'
        Assert-GamingIntegrationEqual $settings.CheckpointType 'Disabled' 'CheckpointType'
        Assert-GamingIntegrationEqual $settings.AutomaticCheckpointsEnabled $false 'AutomaticCheckpointsEnabled'
        Assert-GamingIntegrationEqual $adapterResult.adapterCount 1 'adapterCount'
        Assert-GamingIntegrationEqual $adapterResult.plan.minPartitionVRAM 500000000 'VRAM quota'
        Assert-GamingIntegrationEqual $adapterResult.plan.minPartitionEncode ([long]::MaxValue) 'encode quota'
        $addCall = @($script:commandCalls | Where-Object Name -eq 'Add-VMGpuPartitionAdapter') | Select-Object -First 1
        Assert-GamingIntegrationContains $addCall.Parameters.InstancePath 'VEN_1002&DEV_73BF' 'Add adapter identity'
    }

    It 'enforces static memory for Gaming initialization' {
        $result = Set-EpicVMHyperVStaticMemory -Provider $script:provider -Name 'game' -MemoryBytes 8589934592

        Assert-GamingIntegrationEqual $result.ok $true 'static memory result'
        Assert-GamingIntegrationEqual $result.memoryBytes 8589934592 'static memory bytes'
        $memoryCall = @($script:commandCalls | Where-Object Name -eq 'Set-VMMemory') | Select-Object -Last 1
        Assert-GamingIntegrationEqual $memoryCall.Parameters.DynamicMemoryEnabled $false 'dynamic memory disabled'
        Assert-GamingIntegrationEqual $memoryCall.Parameters.StartupBytes 8589934592 'startup bytes'
    }
}

Describe 'Gaming Hyper-V post-provision percentage changes' {
    BeforeEach { New-GamingIntegrationFixture }

    It 'stops and starts a running Gaming VM while changing only GPU-P quotas' {
        $script:gpuAdapters = @([pscustomobject]@{ InstancePath='\\?\PCI#VEN_1002&DEV_73BF#rx6800\GPUPARAV' })
        $result = & $script:provider.SetGamingGpuPercent 'game' 75

        Assert-GamingIntegrationEqual $result.ok $true 'update result'
        Assert-GamingIntegrationEqual $result.percent 75 'updated percent'
        Assert-GamingIntegrationEqual $result.restarted $true 'restart marker'
        Assert-GamingIntegrationEqual $script:vm.State 'Running' 'final state'
        Assert-GamingIntegrationEqual (@($script:commandCalls | Where-Object Name -eq 'Stop-VM').Count) 1 'stop count'
        Assert-GamingIntegrationEqual (@($script:commandCalls | Where-Object Name -eq 'Start-VM').Count) 1 'start count'
        Assert-GamingIntegrationEqual (@($script:commandCalls | Where-Object Name -eq 'Set-VMProcessor').Count) 0 'processor mutation count'
        Assert-GamingIntegrationEqual (@($script:commandCalls | Where-Object Name -eq 'Resize-VHD').Count) 0 'storage mutation count'

        $setCall = @($script:commandCalls | Where-Object Name -eq 'Set-VMGpuPartitionAdapter') | Select-Object -Last 1
        Assert-GamingIntegrationEqual $setCall.Parameters.MinPartitionVRAM 750000000 'updated VRAM quota'
    }
}
