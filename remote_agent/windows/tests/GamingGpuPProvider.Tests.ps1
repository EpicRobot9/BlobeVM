# Requires -Version 7.0
# Requires -Modules Pester

BeforeAll {
    $windowsRoot = Split-Path -Parent $PSScriptRoot
    . (Join-Path $windowsRoot 'providers/GamingGpuPProvider.ps1')

    function Assert-GamingTestEqual {
        param([AllowNull()]$Actual,[AllowNull()]$Expected,[Parameter(Mandatory)][string]$Label)
        if ($Actual -ne $Expected) {
            throw ("{0}: expected '{1}', got '{2}'" -f $Label, $Expected, $Actual)
        }
    }

    function Assert-GamingTestThrows {
        param([Parameter(Mandatory)][scriptblock]$Script,[Parameter(Mandatory)][string]$MessagePattern)
        $caught = $false
        try {
            & $Script
        }
        catch {
            $caught = $true
            if ($_.Exception.Message -notlike $MessagePattern) {
                throw ("Expected error matching '{0}', got '{1}'" -f $MessagePattern, $_.Exception.Message)
            }
        }
        if (-not $caught) {
            throw ("Expected an error matching '{0}'" -f $MessagePattern)
        }
    }
}

Describe 'Gaming GPU-P identity resolution' {
    It 'selects the RX 6800 XT partitionable GPU by PCI identity instead of list order' {
        $gpus = @(
            [pscustomobject]@{
                Name = 'Other GPU'
                InstancePath = '\\?\PCI#VEN_10DE&DEV_1EB8#other'
                MaxPartitionVRAM = 1000000000
                MaxPartitionDecode = 1000000000
                MaxPartitionCompute = 1000000000
            }
            [pscustomobject]@{
                Name = 'AMD GPU VEN_1002&DEV_73BF'
                InstancePath = '\\?\PCI#VEN_1002&DEV_73BF#rx6800'
                MaxPartitionVRAM = 1000000000
                MaxPartitionDecode = 1000000000
                MaxPartitionCompute = 1000000000
            }
        )

        $selected = Resolve-EpicVMGamingPartitionableGpu -PartitionableGpus $gpus -DeviceIdentity 'VEN_1002&DEV_73BF'

        Assert-GamingTestEqual $selected.InstancePath '\\?\PCI#VEN_1002&DEV_73BF#rx6800' 'selected InstancePath'
    }

    It 'fails when the required GPU identity is missing or ambiguous' {
        $missing = [pscustomobject]@{ Name = 'Other GPU'; InstancePath = 'VEN_10DE&DEV_1EB8' }
        Assert-GamingTestThrows {
            Resolve-EpicVMGamingPartitionableGpu -PartitionableGpus @($missing) -DeviceIdentity 'VEN_1002&DEV_73BF'
        } '*device identity*was not found*'

        $duplicate = @(
            [pscustomobject]@{ Name = 'AMD VEN_1002&DEV_73BF A'; InstancePath = 'VEN_1002&DEV_73BF-A' }
            [pscustomobject]@{ Name = 'AMD VEN_1002&DEV_73BF B'; InstancePath = 'VEN_1002&DEV_73BF-B' }
        )
        Assert-GamingTestThrows {
            Resolve-EpicVMGamingPartitionableGpu -PartitionableGpus $duplicate -DeviceIdentity 'VEN_1002&DEV_73BF'
        } '*multiple matching partitionable GPUs*'
    }
}

Describe 'Gaming GPU-P quota planning' {
    BeforeEach {
        $script:rx6800 = [pscustomobject]@{
            Name = 'AMD GPU VEN_1002&DEV_73BF'
            InstancePath = '\\?\PCI#VEN_1002&DEV_73BF#rx6800'
            MaxPartitionVRAM = 1000000000
            MaxPartitionDecode = 1000000000
            MaxPartitionCompute = 1000000000
            MaxPartitionEncode = [uint64]::MaxValue
        }
    }

    It 'reproduces the testre 50 percent quota exactly' {
        $plan = Get-EpicVMGamingGpuPartitionPlan -PartitionableGpu $script:rx6800 -Percent 50

        Assert-GamingTestEqual $plan.percent 50 'percent'
        Assert-GamingTestEqual $plan.instancePath $script:rx6800.InstancePath 'instancePath'
        foreach ($field in @('minPartitionVRAM','maxPartitionVRAM','optimalPartitionVRAM','minPartitionDecode','maxPartitionDecode','optimalPartitionDecode','minPartitionCompute','maxPartitionCompute','optimalPartitionCompute')) {
            Assert-GamingTestEqual $plan[$field] 500000000 $field
        }
        foreach ($field in @('minPartitionEncode','maxPartitionEncode','optimalPartitionEncode')) {
            Assert-GamingTestEqual $plan[$field] ([long]::MaxValue) $field
        }
    }

    It 'accepts legacy Partition resource fields from the host GPU-P object' {
        $legacy = [pscustomobject]@{
            Name = 'AMD GPU VEN_1002&DEV_73BF'
            InstancePath = '\\\\?\\PCI#VEN_1002&DEV_73BF#rx6800'
            PartitionVRAM = 1000000000
            PartitionDecode = 1000000000
            PartitionCompute = 1000000000
        }
        $plan = Get-EpicVMGamingGpuPartitionPlan -PartitionableGpu $legacy -Percent 50
        Assert-GamingTestEqual $plan.minPartitionVRAM 500000000 'legacy VRAM quota'
        Assert-GamingTestEqual $plan.minPartitionDecode 500000000 'legacy decode quota'
        Assert-GamingTestEqual $plan.minPartitionCompute 500000000 'legacy compute quota'
    }

    It 'rejects partition percentages outside the supported range' {
        Assert-GamingTestThrows {
            Get-EpicVMGamingGpuPartitionPlan -PartitionableGpu $script:rx6800 -Percent 0
        } '*between 1 and 100*'
        Assert-GamingTestThrows {
            Get-EpicVMGamingGpuPartitionPlan -PartitionableGpu $script:rx6800 -Percent 101
        } '*between 1 and 100*'
    }
}

Describe 'Gaming GPU-P driver source resolution' {
    It 'treats an empty configured source list as a scan request under strict mode' {
        $root = Join-Path ([IO.Path]::GetTempPath()) ('epicvm-gpup-driver-root-' + [guid]::NewGuid().ToString('N'))
        $package = Join-Path $root 'amduw23.inf_amd64_test'
        New-Item -ItemType Directory -Path $package -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $package 'amduw23.inf') -Value '; VEN_1002&DEV_73BF' -Encoding ASCII
        try {
            $sources = @(Resolve-EpicVMGamingGpuDriverSourcePaths `
                -Config ([pscustomobject]@{ GamingDriverSourcePaths = @(); GamingDriverStoreRoot = $root }) `
                -DeviceIdentity 'VEN_1002&DEV_73BF')
            Assert-GamingTestEqual $sources.Count 1 'resolved source count'
            Assert-GamingTestEqual $sources[0] $package 'resolved source path'
        }
        finally {
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Gaming GPU-P mounted volume selection' {
    It 'selects the volume containing Windows System32 instead of the EFI volume' {
        Mock Get-Disk { [pscustomobject]@{ Number = 17 } }
        Mock Get-Partition {
            @(
                [pscustomobject]@{ DriveLetter = 'F' }
                [pscustomobject]@{ DriveLetter = 'G' }
            )
        }
        Mock Join-Path {
            param($Path, $ChildPath)
            return ($Path + $ChildPath)
        }
        Mock Test-Path {
            param($LiteralPath, $PathType)
            return ([string]$LiteralPath -match '^G:')
        }

        $root = Get-EpicVMGamingMountedDriveRoot -MountedDisk ([pscustomobject]@{ Number = 17 })

        Assert-GamingTestEqual $root 'G:' 'Windows volume root'
    }

    It 'keeps the Edge executable collection indexed as an array under strict mode' {
        $text = (Get-EpicVMGamingGuestValidationScript).ToString()
        $text | Should -Match '\$edgePaths\s*=\s*@\(\s*@\('
        $text | Should -Match "'--enable-gpu'"
        $text | Should -Match "'--use-angle=d3d11'"
        $text | Should -Match 'WEBGL_CONTEXT'
        $text | Should -Match 'WEBGL_RENDERER'
        $text | Should -Match 'AMD Radeon RX 6800 XT'
        $text | Should -Match '\$webglAttempts\s*=\s*3'
        $text | Should -Match 'WaitForExit\(15000\)'
    }

    It 'uses the AMD-compatible WebGL path without forcing the GPU blocklist bypass' {
        $text = (Get-EpicVMGamingGuestValidationScript).ToString()
        $text | Should -Not -Match '--ignore-gpu-blocklist'
        $text | Should -Match '--disable-features=CalculateNativeWinOcclusion'
    }
}

Describe 'Gaming GPU-P driver file mapping' {
    It 'maps DriverStore packages below HostDriverStore and other files to their Windows-relative paths' {
        $root = Join-Path ([IO.Path]::GetTempPath()) ('epicvm-gpup-map-' + [guid]::NewGuid().ToString('N'))
        $driverStore = Join-Path $root 'System32\DriverStore\FileRepository\u0196411.inf_amd64_test\B025498'
        $outside = Join-Path $root 'System32\libamdsmi_guest.dll'
        New-Item -ItemType Directory -Path $driverStore -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $driverStore 'amdkmdag.sys') -Value 'driver' -Encoding ASCII
        Set-Content -LiteralPath $outside -Value 'amd' -Encoding ASCII
        try {
            $mappings = @(ConvertTo-EpicVMGamingDriverFileMappings `
                -SourcePaths @((Join-Path $driverStore 'amdkmdag.sys'), $outside) `
                -HostWindowsRoot $root)
            $package = @($mappings | Where-Object IsDirectory | Select-Object -First 1)
            $file = @($mappings | Where-Object { -not $_.IsDirectory } | Select-Object -First 1)
            Assert-GamingTestEqual $package.RelativeDestination 'Windows\System32\HostDriverStore\FileRepository\u0196411.inf_amd64_test' 'DriverStore destination'
            Assert-GamingTestEqual $file.RelativeDestination 'Windows\System32\libamdsmi_guest.dll' 'Windows-relative destination'
        }
        finally {
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Gaming GPU-P driver injection cleanup' {
    It 'dismounts the guest VHDX when driver copying fails' {
        $script:mounted = $false
        $script:dismounted = $false
        $sourceRoot = Join-Path ([IO.Path]::GetTempPath()) ('epicvm-gpup-source-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null
        try {
            $mount = {
                param($Path)
                $script:mounted = $true
                [pscustomobject]@{ Path = $Path; RootPath = (Join-Path ([IO.Path]::GetTempPath()) ('epicvm-gpup-mounted-' + [guid]::NewGuid().ToString('N'))) }
            }
            $dismount = {
                param($Path)
                $script:dismounted = $true
            }
            $copy = {
                param($SourcePath, $DestinationPath)
                throw 'simulated driver copy failure'
            }

            Assert-GamingTestThrows {
                Invoke-EpicVMGamingGpuDriverInjection `
                    -DiskPath 'E:\EpicVM\vms\pilot\pilot.vhdx' `
                    -DriverSourcePaths @($sourceRoot) `
                    -MountInvoker $mount `
                    -DismountInvoker $dismount `
                    -CopyInvoker $copy
            } '*simulated driver copy failure*'

            Assert-GamingTestEqual $script:mounted $true 'mounted'
            Assert-GamingTestEqual $script:dismounted $true 'dismounted'
        }
        finally {
            Remove-Item -LiteralPath $sourceRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'copies packages below HostDriverStore FileRepository like the proven helper' {
        $sourceRoot = Join-Path ([IO.Path]::GetTempPath()) ('epicvm-gpup-source-' + [guid]::NewGuid().ToString('N'))
        $mountedRoot = Join-Path ([IO.Path]::GetTempPath()) ('epicvm-gpup-mounted-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $mountedRoot 'Windows\System32') -Force | Out-Null
        $script:copyDestinations = @()
        try {
            $mount = { param($Path) [pscustomobject]@{ Path = $Path; RootPath = $mountedRoot } }
            $dismount = { param($Path) }
            $copy = {
                param($SourcePath, $DestinationPath)
                $script:copyDestinations += $DestinationPath
            }

            $result = Invoke-EpicVMGamingGpuDriverInjection `
                -DiskPath 'E:\EpicVM\vms\pilot\pilot.vhdx' `
                -DriverSourcePaths @($sourceRoot) `
                -MountInvoker $mount `
                -DismountInvoker $dismount `
                -CopyInvoker $copy

            $expected = Join-Path $mountedRoot 'Windows\System32\HostDriverStore\FileRepository'
            Assert-GamingTestEqual $result.destination $expected 'driver destination'
            Assert-GamingTestEqual $script:copyDestinations[0] $expected 'copy destination'
        }
        finally {
            Remove-Item -LiteralPath $sourceRoot -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $mountedRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
