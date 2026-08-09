# Requires -Version 7.0
<#
    Dependency-free Hyper-V adapter for the EpicVM remote agent.

    The adapter deliberately keeps all Hyper-V command calls behind
    Invoke-EpicVMHyperVCmdlet.  The optional CommandInvoker argument is used by
    the Pester suite and by integrators that provide a controlled command
    boundary; production uses the native Hyper-V cmdlets.
#>

Set-StrictMode -Version Latest

$script:EpicVMHyperVOwnershipMarker = 'EpicVM-Managed: true'

function Get-EpicVMHyperVValue {
    param(
        [AllowNull()] [object] $Object,
        [Parameter(Mandatory)] [string] $Name,
        [AllowNull()] [object] $Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }

    if ($Object -is [System.Collections.IDictionary]) {
        foreach ($key in $Object.Keys) {
            if ([string]::Equals([string]$key, $Name, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $Object[$key]
            }
        }
        return $Default
    }

    foreach ($property in $Object.PSObject.Properties) {
        if ([string]::Equals($property.Name, $Name, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $property.Value
        }
    }

    return $Default
}

function New-EpicVMHyperVError {
    param(
        [Parameter(Mandatory)] [string] $Code,
        [Parameter(Mandatory)] [string] $Message
    )

    $exception = [System.InvalidOperationException]::new($Message)
    $exception | Add-Member -MemberType NoteProperty -Name ErrorCode -Value $Code -Force
    return $exception
}

function Test-EpicVMHyperVName {
    param([AllowNull()] [string] $Name)

    if ($null -eq $Name) {
        return $false
    }

    return [regex]::IsMatch(
        $Name,
        '\A[a-z0-9][a-z0-9._-]{0,62}\z',
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
}

function Get-EpicVMHyperVRequiredCmdlets {
    return @(
        'Get-VM',
        'Get-VMHost',
        'Get-VMSwitch',
        'New-VM',
        'New-VHD',
        'Set-VM',
        'Set-VMProcessor',
        'Start-VM',
        'Stop-VM',
        'Restart-VM',
        'Remove-VM'
    )
}

function Invoke-EpicVMHyperVCmdlet {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [string] $CommandName,
        [AllowNull()] [hashtable] $Parameters = $null
    )

    $parametersToUse = if ($null -eq $Parameters) { @{} } else { $Parameters }
    $invoker = Get-EpicVMHyperVValue -Object $Provider -Name 'CommandInvoker'

    if ($null -ne $invoker) {
        return & $invoker $CommandName $parametersToUse
    }

    $command = Get-Command -Name $CommandName -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw (New-EpicVMHyperVError -Code 'ProviderUnavailable' -Message 'The configured Hyper-V provider is unavailable.')
    }

    return & $CommandName @parametersToUse
}

function Get-EpicVMHyperVVM {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [ValidateNotNullOrEmpty()] [string] $Name
    )

    try {
        $items = @(Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Get-VM' -Parameters @{
                Name = $Name
                ErrorAction = 'Stop'
            })
    }
    catch {
        throw (New-EpicVMHyperVError -Code 'NotFound' -Message 'The requested VM was not found.')
    }

    if ($items.Count -eq 0 -or $null -eq $items[0]) {
        throw (New-EpicVMHyperVError -Code 'NotFound' -Message 'The requested VM was not found.')
    }

    return $items[0]
}

function Test-EpicVMHyperVOwned {
    param([AllowNull()] [object] $VM)

    $notes = [string](Get-EpicVMHyperVValue -Object $VM -Name 'Notes' -Default '')
    foreach ($line in ($notes -split '[\r\n]+')) {
        if ($line.Trim() -ceq $script:EpicVMHyperVOwnershipMarker) {
            return $true
        }
    }
    return $false
}

function Test-EpicVMHyperVManagedRoot {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [AllowNull()] [object] $VM
    )

    $root = [string](Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'VmRoot' -Default '')
    if ([string]::IsNullOrWhiteSpace($root) -or $null -eq $VM) { return $false }
    try {
        $rootFull = [System.IO.Path]::GetFullPath($root).TrimEnd([char[]]@([char]92, [char]47))
    }
    catch { return $false }

    $candidatePaths = @()
    foreach ($propertyName in @('Path', 'ConfigurationLocation')) {
        $candidate = [string](Get-EpicVMHyperVValue -Object $VM -Name $propertyName -Default '')
        if ($candidate) { $candidatePaths += $candidate }
    }
    if ($candidatePaths.Count -eq 0) {
        try {
            $name = [string](Get-EpicVMHyperVValue -Object $VM -Name 'Name' -Default '')
            $disks = @(Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Get-VMHardDiskDrive' -Parameters @{ VMName = $name; ErrorAction = 'Stop' })
            foreach ($disk in $disks) {
                $candidate = [string](Get-EpicVMHyperVValue -Object $disk -Name 'Path' -Default '')
                if ($candidate) { $candidatePaths += $candidate }
            }
        }
        catch { return $false }
    }
    foreach ($candidate in $candidatePaths) {
        try {
            $candidateFull = [System.IO.Path]::GetFullPath($candidate).TrimEnd([char[]]@([char]92, [char]47))
            if ($candidateFull.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase) -or
                $candidateFull.StartsWith($rootFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
        catch { }
    }
    return $false
}

function ConvertTo-EpicVMHyperVVMInfo {
    param([Parameter(Mandatory)] [object] $VM)

    $name = [string](Get-EpicVMHyperVValue -Object $VM -Name 'Name' -Default '')
    $state = [string](Get-EpicVMHyperVValue -Object $VM -Name 'State' -Default 'Unknown')
    $status = Get-EpicVMHyperVValue -Object $VM -Name 'Status' -Default $null
    $memory = Get-EpicVMHyperVValue -Object $VM -Name 'MemoryAssigned' -Default $null
    $cpuUsage = Get-EpicVMHyperVValue -Object $VM -Name 'CPUUsage' -Default $null
    $uptime = Get-EpicVMHyperVValue -Object $VM -Name 'Uptime' -Default $null
    $uptimeSeconds = $null
    if ($null -ne $uptime) {
        try { $uptimeSeconds = [long]([System.TimeSpan]$uptime).TotalSeconds } catch { $uptimeSeconds = $null }
    }

    return [ordered]@{
        name = $name
        state = $state
        status = $status
        managed = Test-EpicVMHyperVOwned -VM $VM
        cpuUsagePercent = $cpuUsage
        memoryAssignedBytes = $memory
        uptimeSeconds = $uptimeSeconds
    }
}

function Get-EpicVMHyperVOption {
    param(
        [AllowNull()] [object] $Config,
        [Parameter(Mandatory)] [string] $Name,
        [AllowNull()] [object] $Default = $null
    )

    return Get-EpicVMHyperVValue -Object $Config -Name $Name -Default $Default
}

function ConvertTo-EpicVMHyperVInt64 {
    param(
        [Parameter(Mandatory)] [object] $Value,
        [Parameter(Mandatory)] [string] $FieldName
    )

    try {
        return [System.Convert]::ToInt64($Value)
    }
    catch {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message ("The {0} value is invalid." -f $FieldName))
    }
}

function Get-EpicVMHyperVCreateOptions {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [object] $Request
    )

    $config = Get-EpicVMHyperVValue -Object $Provider -Name 'Config'
    $name = [string](Get-EpicVMHyperVValue -Object $Request -Name 'Name' -Default '')
    if (-not (Test-EpicVMHyperVName -Name $name)) {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The VM name is invalid.')
    }

    $memoryRaw = Get-EpicVMHyperVValue -Object $Request -Name 'MemoryBytes' -Default $null
    $memory = if ($null -eq $memoryRaw) {
        ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'DefaultMemoryBytes' -Default 4294967296) -FieldName 'memoryBytes'
    }
    else {
        ConvertTo-EpicVMHyperVInt64 -Value $memoryRaw -FieldName 'memoryBytes'
    }

    $cpuRaw = Get-EpicVMHyperVValue -Object $Request -Name 'CpuCount' -Default $null
    $cpu = if ($null -eq $cpuRaw) {
        ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'DefaultCpuCount' -Default 2) -FieldName 'cpuCount'
    }
    else {
        ConvertTo-EpicVMHyperVInt64 -Value $cpuRaw -FieldName 'cpuCount'
    }

    $diskRaw = Get-EpicVMHyperVValue -Object $Request -Name 'DiskSizeBytes' -Default $null
    $disk = if ($null -eq $diskRaw) {
        ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'DefaultDiskSizeBytes' -Default 68719476736) -FieldName 'diskSizeBytes'
    }
    else {
        ConvertTo-EpicVMHyperVInt64 -Value $diskRaw -FieldName 'diskSizeBytes'
    }

    $minMemory = ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'MinMemoryBytes' -Default 536870912) -FieldName 'minMemoryBytes'
    $maxMemory = ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'MaxMemoryBytes' -Default 17179869184) -FieldName 'maxMemoryBytes'
    $minCpu = ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'MinCpuCount' -Default 1) -FieldName 'minCpuCount'
    $maxCpu = ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'MaxCpuCount' -Default 16) -FieldName 'maxCpuCount'
    $maxDisk = ConvertTo-EpicVMHyperVInt64 -Value (Get-EpicVMHyperVOption -Config $config -Name 'MaxDiskSizeBytes' -Default 549755813888) -FieldName 'maxDiskSizeBytes'

    if ($memory -lt $minMemory -or $memory -gt $maxMemory) {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The requested memory is outside the configured limit.')
    }
    if ($cpu -lt $minCpu -or $cpu -gt $maxCpu) {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The requested CPU count is outside the configured limit.')
    }
    if ($disk -le 0 -or $disk -gt $maxDisk) {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The requested disk size is outside the configured limit.')
    }

    $generationRaw = Get-EpicVMHyperVValue -Object $Request -Name 'Generation' -Default (Get-EpicVMHyperVOption -Config $config -Name 'Generation' -Default 2)
    $generation = ConvertTo-EpicVMHyperVInt64 -Value $generationRaw -FieldName 'generation'
    if ($generation -ne 1 -and $generation -ne 2) {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The Hyper-V generation must be 1 or 2.')
    }

    $switchName = [string](Get-EpicVMHyperVValue -Object $Request -Name 'SwitchName' -Default (Get-EpicVMHyperVOption -Config $config -Name 'SwitchName' -Default ''))
    $vmRoot = [string](Get-EpicVMHyperVOption -Config $config -Name 'VmRoot' -Default 'C:\ProgramData\EpicVM\vms')
    if ([string]::IsNullOrWhiteSpace($vmRoot)) {
        throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The Hyper-V VM root is not configured.')
    }

    return [ordered]@{
        name = $name
        memoryBytes = $memory
        cpuCount = $cpu
        diskSizeBytes = $disk
        generation = $generation
        switchName = $switchName
        vmRoot = $vmRoot
    }
}

function Get-EpicVMHyperVCapabilities {
    param([Parameter(Mandatory)] [object] $Provider)

    $available = [bool](Get-EpicVMHyperVValue -Object $Provider -Name 'Available' -Default $false)
    $missing = @(Get-EpicVMHyperVValue -Object $Provider -Name 'MissingCmdlets' -Default @())
    $hostInfo = $null
    $switches = @()

    if ($available) {
        try {
            $hostResults = @(Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Get-VMHost' -Parameters @{ ErrorAction = 'Stop' })
            if ($hostResults.Count -gt 0) { $hostInfo = $hostResults[0] }
        }
        catch {
            $hostInfo = $null
        }
        try {
            $switches = @(Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Get-VMSwitch' -Parameters @{ ErrorAction = 'Stop' })
        }
        catch {
            $switches = @()
        }
    }

    $logicalProcessors = Get-EpicVMHyperVValue -Object $hostInfo -Name 'LogicalProcessorCount' -Default $null
    $memoryCapacity = Get-EpicVMHyperVValue -Object $hostInfo -Name 'MemoryCapacity' -Default $null
    $defaultSwitch = Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'SwitchName' -Default ''

    return [ordered]@{
        provider = 'HyperV'
        available = $available
        missingCmdlets = $missing
        ownershipMarker = $script:EpicVMHyperVOwnershipMarker
        # Emit the normalized control-plane contract as well as the human-readable
        # feature list. The dashboard must not have to infer lifecycle support
        # from provider-specific feature names.
        create_vm = $available
        start = $available
        stop = $available
        restart = $available
        delete = $available
        console = $false
        features = @('capabilities', 'list', 'create', 'lifecycle', 'delete-owned')
        resources = [ordered]@{
            logicalProcessorCount = $logicalProcessors
            memoryCapacityBytes = $memoryCapacity
            switchCount = $switches.Count
            configuredSwitch = $defaultSwitch
        }
        limits = [ordered]@{
            minMemoryBytes = Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'MinMemoryBytes' -Default 536870912
            maxMemoryBytes = Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'MaxMemoryBytes' -Default 17179869184
            minCpuCount = Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'MinCpuCount' -Default 1
            maxCpuCount = Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'MaxCpuCount' -Default 16
            maxDiskSizeBytes = Get-EpicVMHyperVOption -Config (Get-EpicVMHyperVValue -Object $Provider -Name 'Config') -Name 'MaxDiskSizeBytes' -Default 549755813888
        }
    }
}

function Get-EpicVMHyperVVMs {
    param([Parameter(Mandatory)] [object] $Provider)

    if (-not [bool](Get-EpicVMHyperVValue -Object $Provider -Name 'Available' -Default $false)) {
        throw (New-EpicVMHyperVError -Code 'ProviderUnavailable' -Message 'The configured Hyper-V provider is unavailable.')
    }

    $items = @(Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Get-VM' -Parameters @{ ErrorAction = 'Stop' })
    return @($items | ForEach-Object { ConvertTo-EpicVMHyperVVMInfo -VM $_ })
}

function New-EpicVMHyperVVM {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [object] $Request
    )

    if (-not [bool](Get-EpicVMHyperVValue -Object $Provider -Name 'Available' -Default $false)) {
        throw (New-EpicVMHyperVError -Code 'ProviderUnavailable' -Message 'The configured Hyper-V provider is unavailable.')
    }

    $options = Get-EpicVMHyperVCreateOptions -Provider $Provider -Request $Request
    $name = [string]$options.name

    try {
        $existing = Get-EpicVMHyperVVM -Provider $Provider -Name $name
        if ($null -ne $existing) {
            throw (New-EpicVMHyperVError -Code 'Conflict' -Message 'A VM with that name already exists.')
        }
    }
    catch {
        if ($_.Exception.PSObject.Properties['ErrorCode'] -and $_.Exception.ErrorCode -eq 'Conflict') {
            throw
        }
        # Get-VM reports a missing VM as an error on native Hyper-V.  That is
        # the expected path while creating a new VM.
    }

    $vmPath = Join-Path -Path ([string]$options.vmRoot) -ChildPath $name
    $diskPath = Join-Path -Path $vmPath -ChildPath ("{0}.vhdx" -f $name)
    if (Test-Path -LiteralPath $diskPath) {
        throw (New-EpicVMHyperVError -Code 'Conflict' -Message 'The VM disk path already exists.')
    }

    $switchName = [string]$options.switchName
    if (-not [string]::IsNullOrWhiteSpace($switchName)) {
        $switches = @(Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Get-VMSwitch' -Parameters @{
                Name = $switchName
                ErrorAction = 'Stop'
            })
        if ($switches.Count -eq 0) {
            throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The configured Hyper-V switch was not found.')
        }
    }

    $createdVm = $false
    $createdDisk = $false
    try {
        New-Item -ItemType Directory -Path $vmPath -Force -ErrorAction Stop | Out-Null
        $templateDiskPath = [string](Get-EpicVMHyperVValue -Object $Request -Name 'templateDiskPath' -Default '')
        $templateRequired = [bool](Get-EpicVMHyperVValue -Object $Request -Name 'templateRequired' -Default $false)
        if ($templateRequired) {
            if ([string]::IsNullOrWhiteSpace($templateDiskPath) -or -not (Test-Path -LiteralPath $templateDiskPath -PathType Leaf)) {
                throw (New-EpicVMHyperVError -Code 'InvalidInput' -Message 'The validated template disk is unavailable.')
            }
            # Full independent copy.  Differencing disks are intentionally not
            # used because template identity and recovery depend on isolation.
            Copy-Item -LiteralPath $templateDiskPath -Destination $diskPath -Force -ErrorAction Stop
        }
        else {
            Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'New-VHD' -Parameters @{
                Path = $diskPath
                SizeBytes = [long]$options.diskSizeBytes
                Dynamic = $true
                ErrorAction = 'Stop'
            } | Out-Null
        }
        $createdDisk = $true

        $newVmParameters = @{
            Name = $name
            MemoryStartupBytes = [long]$options.memoryBytes
            Generation = [long]$options.generation
            VHDPath = $diskPath
            Path = $vmPath
            ErrorAction = 'Stop'
        }
        if (-not [string]::IsNullOrWhiteSpace($switchName)) {
            $newVmParameters.SwitchName = $switchName
        }
        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'New-VM' -Parameters $newVmParameters | Out-Null
        $createdVm = $true

        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Set-VM' -Parameters @{
            Name = $name
            Notes = $script:EpicVMHyperVOwnershipMarker
            ErrorAction = 'Stop'
        } | Out-Null
        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Set-VMProcessor' -Parameters @{
            VMName = $name
            Count = [long]$options.cpuCount
            ErrorAction = 'Stop'
        } | Out-Null

        return ConvertTo-EpicVMHyperVVMInfo -VM (Get-EpicVMHyperVVM -Provider $Provider -Name $name)
    }
    catch {
        if ($createdVm) {
            try {
                Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Remove-VM' -Parameters @{
                    Name = $name
                    Force = $true
                    ErrorAction = 'SilentlyContinue'
                } | Out-Null
            }
            catch { }
        }
        if ($createdDisk -and (Test-Path -LiteralPath $diskPath)) {
            try { Remove-Item -LiteralPath $diskPath -Force -ErrorAction SilentlyContinue } catch { }
        }
        throw
    }
}

function Invoke-EpicVMHyperVLifecycle {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [ValidateNotNullOrEmpty()] [string] $Name,
        [Parameter(Mandatory)] [ValidateSet('Start', 'Stop', 'Restart')] [string] $Action
    )

    if (-not [bool](Get-EpicVMHyperVValue -Object $Provider -Name 'Available' -Default $false)) {
        throw (New-EpicVMHyperVError -Code 'ProviderUnavailable' -Message 'The configured Hyper-V provider is unavailable.')
    }

    $vm = Get-EpicVMHyperVVM -Provider $Provider -Name $Name
    if (-not (Test-EpicVMHyperVOwned -VM $vm)) {
        throw (New-EpicVMHyperVError -Code 'UnmanagedVM' -Message ("VM '{0}' is not owned by EpicVM and cannot be changed." -f $Name))
    }
    if (-not (Test-EpicVMHyperVManagedRoot -Provider $Provider -VM $vm)) {
        throw (New-EpicVMHyperVError -Code 'UnmanagedVM' -Message ("VM '{0}' is outside the EpicVM managed root and cannot be changed." -f $Name))
    }

    $state = [string](Get-EpicVMHyperVValue -Object $vm -Name 'State' -Default 'Unknown')
    if ($Action -eq 'Start' -and $state -ieq 'Running') {
        return ConvertTo-EpicVMHyperVVMInfo -VM $vm
    }
    if ($Action -eq 'Stop' -and $state -ieq 'Off') {
        return ConvertTo-EpicVMHyperVVMInfo -VM $vm
    }
    if ($Action -eq 'Restart' -and $state -ieq 'Off') {
        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Start-VM' -Parameters @{ Name = $Name; ErrorAction = 'Stop' } | Out-Null
    }
    elseif ($Action -eq 'Start') {
        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Start-VM' -Parameters @{ Name = $Name; ErrorAction = 'Stop' } | Out-Null
    }
    elseif ($Action -eq 'Stop') {
        # Do not use -Force here: a normal stop lets the guest shut down cleanly.
        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Stop-VM' -Parameters @{ Name = $Name; ErrorAction = 'Stop' } | Out-Null
    }
    elseif ($Action -eq 'Restart') {
        Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Restart-VM' -Parameters @{ Name = $Name; ErrorAction = 'Stop' } | Out-Null
    }

    return ConvertTo-EpicVMHyperVVMInfo -VM (Get-EpicVMHyperVVM -Provider $Provider -Name $Name)
}

function Remove-EpicVMHyperVVM {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [ValidateNotNullOrEmpty()] [string] $Name
    )

    if (-not [bool](Get-EpicVMHyperVValue -Object $Provider -Name 'Available' -Default $false)) {
        throw (New-EpicVMHyperVError -Code 'ProviderUnavailable' -Message 'The configured Hyper-V provider is unavailable.')
    }

    $vm = Get-EpicVMHyperVVM -Provider $Provider -Name $Name
    if (-not (Test-EpicVMHyperVOwned -VM $vm)) {
        throw (New-EpicVMHyperVError -Code 'UnmanagedVM' -Message ("VM '{0}' is not owned by EpicVM and cannot be changed." -f $Name))
    }
    if (-not (Test-EpicVMHyperVManagedRoot -Provider $Provider -VM $vm)) {
        throw (New-EpicVMHyperVError -Code 'UnmanagedVM' -Message ("VM '{0}' is outside the EpicVM managed root and cannot be changed." -f $Name))
    }
    if ([string](Get-EpicVMHyperVValue -Object $vm -Name 'State' -Default '') -ieq 'Running') {
        throw (New-EpicVMHyperVError -Code 'Conflict' -Message 'Stop the VM before deleting it.')
    }

    # Remove-VM unregisters the VM.  It intentionally does not remove the VHDX.
    Invoke-EpicVMHyperVCmdlet -Provider $Provider -CommandName 'Remove-VM' -Parameters @{
        Name = $Name
        Force = $true
        ErrorAction = 'Stop'
    } | Out-Null

    return [ordered]@{
        name = $Name
        deleted = $true
        disksPreserved = $true
    }
}

function New-EpicVMHyperVProvider {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Config = @{},
        [AllowNull()] [scriptblock] $CommandInvoker = $null
    )

    $missing = @()
    if ($null -eq $CommandInvoker) {
        foreach ($commandName in (Get-EpicVMHyperVRequiredCmdlets)) {
            if ($null -eq (Get-Command -Name $commandName -ErrorAction SilentlyContinue)) {
                $missing += $commandName
            }
        }
    }

    $provider = [pscustomobject]@{
        Name = 'HyperV'
        Config = $Config
        Available = ($null -ne $CommandInvoker -or $missing.Count -eq 0)
        MissingCmdlets = $missing
        CommandInvoker = $CommandInvoker
        GetCapabilities = $null
        GetVMs = $null
        CreateVM = $null
        StartVM = $null
        StopVM = $null
        RestartVM = $null
        DeleteVM = $null
    }

    $getCapabilities = ${function:Get-EpicVMHyperVCapabilities}
    $getVMs = ${function:Get-EpicVMHyperVVMs}
    $createVM = ${function:New-EpicVMHyperVVM}
    $startVM = ${function:Invoke-EpicVMHyperVLifecycle}
    $stopVM = ${function:Invoke-EpicVMHyperVLifecycle}
    $restartVM = ${function:Invoke-EpicVMHyperVLifecycle}
    $deleteVM = ${function:Remove-EpicVMHyperVVM}
    $provider.GetCapabilities = ({ & $getCapabilities -Provider $provider }.GetNewClosure())
    $provider.GetVMs = ({ & $getVMs -Provider $provider }.GetNewClosure())
    $provider.CreateVM = ({ param($Request) & $createVM -Provider $provider -Request $Request }.GetNewClosure())
    $provider.StartVM = ({ param($Name) & $startVM -Provider $provider -Name $Name -Action Start }.GetNewClosure())
    $provider.StopVM = ({ param($Name) & $stopVM -Provider $provider -Name $Name -Action Stop }.GetNewClosure())
    $provider.RestartVM = ({ param($Name) & $restartVM -Provider $provider -Name $Name -Action Restart }.GetNewClosure())
    $provider.DeleteVM = ({ param($Name) & $deleteVM -Provider $provider -Name $Name }.GetNewClosure())

    return $provider
}
