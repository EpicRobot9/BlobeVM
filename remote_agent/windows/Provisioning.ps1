# EpicVM provisioning primitives. Persisted records are deliberately redacted;
# claim passwords and the one-time claim token exist only in process memory.

Set-StrictMode -Version Latest
$script:EpicVMProvisioningStates = @(
    'queued', 'cloning', 'booting', 'awaiting_claim', 'configuring_guest',
    'enrolling_tailscale', 'awaiting_console', 'console_failed', 'verifying',
    'ready', 'failed'
)

function New-EpicVMProvisioningError {
    param(
        [Parameter(Mandatory)] [string] $Code,
        [Parameter(Mandatory)] [string] $Message,
        [int] $Status = 422
    )
    $exception = [System.InvalidOperationException]::new($Message)
    $exception | Add-Member -MemberType NoteProperty -Name ErrorCode -Value $Code -Force
    $exception | Add-Member -MemberType NoteProperty -Name HttpStatus -Value $Status -Force
    return $exception
}

function Get-EpicVMProvisioningProfile {
    param([Parameter(Mandatory)] [string] $Profile)
    switch ($Profile.ToLowerInvariant()) {
        'standard' {
            return [ordered]@{
                profile = 'standard'
                cpuCount = 4
                memoryBytes = 8589934592
                diskSizeBytes = 103079215104
                gpu = $false
            }
        }
        'gaming' {
            return [ordered]@{
                profile = 'gaming'
                cpuCount = 6
                memoryBytes = 12884901888
                diskSizeBytes = 137438953472
                gpu = $true
                gpuPartition = '50%'
            }
        }
        default { throw 'Unsupported EpicVM profile.' }
    }
}

function ConvertTo-EpicVMRedactedJob {
    param([Parameter(Mandatory)] [object] $Job)
    $safe = [ordered]@{}
    foreach ($name in @(
        'id', 'name', 'profile', 'state', 'createdAt', 'updatedAt', 'errorCode',
        'errorMessage', 'templateVersion', 'vmId', 'tailnetIp',
        'tailnetDeviceId', 'consoleRoutePrefix', 'consoleVerifiedAt',
        'quarantineUntil'
    )) {
        $value = Get-EpicVMProperty -Object $Job -Name $name -Default $null
        if ($null -ne $value) { $safe[$name] = $value }
    }
    return $safe
}

function New-EpicVMProvisioningJobObject {
    param(
        [Parameter(Mandatory)] [string] $Id,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Profile,
        [string] $State = 'queued'
    )
    $now = [DateTime]::UtcNow.ToString('o')
    return [pscustomobject]@{
        id = $Id
        name = $Name
        profile = $Profile
        state = $State
        createdAt = $now
        updatedAt = $now
        templateVersion = $null
        vmId = $null
        tailnetIp = $null
        tailnetDeviceId = $null
        consoleRoutePrefix = $null
        consoleVerifiedAt = $null
        quarantineUntil = $null
        errorCode = $null
        errorMessage = $null
        # Only the one-way verifier is persisted. The claim itself never is.
        claimHash = $null
        claimExpires = $null
        claimUsed = $false
    }
}

function New-EpicVMProvisioningStore {
    param([Parameter(Mandatory)] [object] $Config)
    $path = [string](Get-EpicVMProperty -Object $Config -Name 'ProvisioningStatePath' -Default '')
    if ([string]::IsNullOrWhiteSpace($path)) {
        $root = [string](Get-EpicVMProperty -Object $Config -Name 'VmRoot' -Default 'E:\EpicVM\vms')
        $path = Join-Path $root '..\provisioning-jobs.json'
    }
    $store = [pscustomobject]@{
        Path = [IO.Path]::GetFullPath($path)
        Jobs = @{}
        Deprovisioning = @{}
        Claims = @{}
        SyncRoot = [object]::new()
    }
    if (-not (Test-Path -LiteralPath $store.Path -PathType Leaf)) { return $store }

    try {
        $raw = Get-Content -LiteralPath $store.Path -Raw -Encoding UTF8
        $records = @($raw | ConvertFrom-Json)
        foreach ($record in $records) {
            $kind = [string](Get-EpicVMProperty -Object $record -Name 'kind' -Default 'provisioning')
            $job = New-EpicVMProvisioningJobObject `
                -Id ([string](Get-EpicVMProperty -Object $record -Name 'id' -Default '')) `
                -Name ([string](Get-EpicVMProperty -Object $record -Name 'name' -Default '')) `
                -Profile ([string](Get-EpicVMProperty -Object $record -Name 'profile' -Default 'standard')) `
                -State ([string](Get-EpicVMProperty -Object $record -Name 'state' -Default 'failed'))
            foreach ($name in @(
                'createdAt', 'updatedAt', 'templateVersion', 'vmId', 'tailnetIp',
                'tailnetDeviceId', 'consoleRoutePrefix', 'consoleVerifiedAt',
                'quarantineUntil', 'errorCode', 'errorMessage',
                'claimHash', 'claimExpires', 'claimUsed'
            )) {
                $job.$name = Get-EpicVMProperty -Object $record -Name $name -Default $job.$name
            }
            if ([string]::IsNullOrWhiteSpace($job.id) -or -not (Test-EpicVMName $job.name)) { continue }
            if ($kind -eq 'deprovisioning') {
                $store.Deprovisioning[$job.id] = $job
                continue
            }
            $store.Jobs[$job.id] = $job
            $claimHashValid = [string]$job.claimHash -match '^[0-9a-fA-F]{64}$'
            if ($job.state -eq 'awaiting_claim' -and $claimHashValid -and -not [bool]$job.claimUsed) {
                try {
                    [void][DateTime]::Parse($job.claimExpires)
                    $store.Claims[$job.id] = $job
                }
                catch {
                    $job.state = 'failed'
                    $job.errorCode = 'claim_state_invalid'
                    $job.errorMessage = 'The persisted claim state was invalid.'
                }
            }
        }
    }
    catch {
        throw (New-EpicVMProvisioningError -Code 'state_store_invalid' -Message 'The provisioning state store is invalid.' -Status 503)
    }
    return $store
}

function Save-EpicVMProvisioningStore {
    param([Parameter(Mandatory)] [object] $Store)
    $parent = Split-Path -Parent $Store.Path
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $records = @(
        $Store.Jobs.Values | ForEach-Object {
            $safe = ConvertTo-EpicVMRedactedJob -Job $_
            $safe.kind = 'provisioning'
            # A verifier is not a credential and is needed to survive restart;
            # the raw claim token is intentionally absent from this record.
            $safe.claimHash = [string](Get-EpicVMProperty -Object $_ -Name 'claimHash' -Default '')
            $safe.claimExpires = [string](Get-EpicVMProperty -Object $_ -Name 'claimExpires' -Default '')
            $safe.claimUsed = [bool](Get-EpicVMProperty -Object $_ -Name 'claimUsed' -Default $false)
            $safe
        }
        $Store.Deprovisioning.Values | ForEach-Object {
            $safe = ConvertTo-EpicVMRedactedJob -Job $_
            $safe.kind = 'deprovisioning'
            $safe
        }
    )
    $tmp = "$($Store.Path).tmp"
    try {
        $records | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $tmp -Encoding UTF8 -NoNewline
        Move-Item -LiteralPath $tmp -Destination $Store.Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
}

function Test-EpicVMTemplateManifest {
    param([Parameter(Mandatory)] [object] $Config, [switch] $SkipContentHash)
    $manifestPath = [string](Get-EpicVMProperty -Object $Config -Name 'TemplateManifestPath' -Default '')
    if ([string]::IsNullOrWhiteSpace($manifestPath) -or -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($field in @('templateVersion', 'build', 'sha256', 'bootstrap', 'gpu', 'network', 'sysprep', 'immutable', 'fullCopy', 'diskType', 'imagePath')) {
            if ([string]::IsNullOrWhiteSpace([string](Get-EpicVMProperty -Object $manifest -Name $field -Default ''))) { return $false }
        }
        if (-not [bool]$manifest.immutable -or -not [bool]$manifest.fullCopy) { return $false }
        if ([string]$manifest.network -ne 'private-switch' -or [string]$manifest.diskType -ine 'Dynamic') { return $false }
        if ([string]$manifest.sysprep -notmatch '(?i)/generalize') { return $false }
        if ([string]$manifest.sha256 -notmatch '^[0-9a-fA-F]{64}$') { return $false }

        $manifestDirectory = [IO.Path]::GetFullPath((Split-Path -Parent $manifestPath)).TrimEnd([char[]]@([char]92, [char]47))
        $imagePath = [IO.Path]::GetFullPath([string]$manifest.imagePath)
        if (-not ($imagePath.Equals($manifestDirectory, [StringComparison]::OrdinalIgnoreCase) -or
                $imagePath.StartsWith($manifestDirectory + '\', [StringComparison]::OrdinalIgnoreCase))) { return $false }
        $image = Get-Item -LiteralPath $imagePath -ErrorAction Stop
        if (($image.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
        # Capability discovery runs frequently and must not hash a multi-GB
        # template on every request. The provisioning mutation path omits this
        # switch and performs the full SHA-256 gate immediately before cloning.
        if (-not $SkipContentHash) {
            $actual = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne ([string]$manifest.sha256).ToLowerInvariant()) { return $false }
        }
        if (Get-Command -Name Get-VHD -ErrorAction SilentlyContinue) {
            $vhd = Get-VHD -Path $imagePath -ErrorAction Stop
            if (-not [string]::IsNullOrWhiteSpace([string]$vhd.ParentPath) -or [string]$vhd.VhdType -ine 'Dynamic') { return $false }
        }
        return $true
    }
    catch { return $false }
}

function Test-EpicVMProvisioningPrerequisites {
    param([Parameter(Mandatory)] [object] $Config)

    if (-not (Test-EpicVMTemplateManifest -Config $Config -SkipContentHash)) { return $false }
    foreach ($pathField in @('BootstrapCredentialPath', 'TailscaleOAuthSecretPath')) {
        $path = [string](Get-EpicVMProperty -Object $Config -Name $pathField -Default '')
        if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    }
    foreach ($valueField in @('BootstrapUser', 'TailscaleOAuthClientId', 'TailscaleTailnet')) {
        if ([string]::IsNullOrWhiteSpace([string](Get-EpicVMProperty -Object $Config -Name $valueField -Default ''))) { return $false }
    }
    return $true
}

function ConvertTo-EpicVMClaimHash {
    param([Parameter(Mandatory)] [string] $Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($bytes)
        return (([BitConverter]::ToString($hash)) -replace '-', '').ToLowerInvariant()
    }
    finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $algorithm.Dispose()
    }
}

function Test-EpicVMClaim {
    param([Parameter(Mandatory)] [object] $Job, [Parameter(Mandatory)] [string] $Claim)
    if ([string]::IsNullOrWhiteSpace([string]$Job.claimHash) -or [bool]$Job.claimUsed) { return $false }
    try { $expires = [DateTime]::Parse([string]$Job.claimExpires) } catch { return $false }
    if ([DateTime]::UtcNow -gt $expires.ToUniversalTime()) { return $false }
    $actual = [Text.Encoding]::UTF8.GetBytes((ConvertTo-EpicVMClaimHash -Value $Claim))
    $expected = [Text.Encoding]::UTF8.GetBytes(([string]$Job.claimHash).ToLowerInvariant())
    try {
        return $actual.Length -eq $expected.Length -and [Security.Cryptography.CryptographicOperations]::FixedTimeEquals($actual, $expected)
    }
    finally {
        [Array]::Clear($actual, 0, $actual.Length)
        [Array]::Clear($expected, 0, $expected.Length)
    }
}

function Invoke-EpicVMProvisioningFailedCleanup {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Job)
    if ([string]::IsNullOrWhiteSpace([string]$Job.vmId)) { return }
    try {
        $vm = @(& $State.Provider.GetVMs | Where-Object { [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '') -ceq $Job.name }) | Select-Object -First 1
        if ($null -eq $vm) { return }
        $teardown = Get-EpicVMProperty -Object $State.Provider -Name 'TeardownConsole' -Default $null
        if ($null -ne $teardown) { & $teardown $Job.name $Job.name ([string]$Job.tailnetDeviceId) | Out-Null }
    }
    catch { }
    try {
        $revoke = Get-EpicVMProperty -Object $State.Provider -Name 'RevokeTailscale' -Default $null
        if ($null -ne $revoke) { & $revoke ([string]$Job.tailnetDeviceId) | Out-Null }
    }
    catch { }
    try {
        if ([string](Get-EpicVMProperty -Object $vm -Name 'state' -Default '') -ieq 'Running') { & $State.Provider.StopVM $Job.name | Out-Null }
        & $State.Provider.DeleteVM $Job.name | Out-Null
    }
    catch { return }
    try {
        $quarantine = Get-EpicVMProperty -Object $State.Provider -Name 'QuarantineVM' -Default $null
        if ($null -ne $quarantine) { & $quarantine $Job.name | Out-Null }
    }
    catch { }
}

function Invoke-EpicVMProvisioningRecovery {
    param([Parameter(Mandatory)] [object] $State)
    $changed = $false
    foreach ($job in @($State.Provisioning.Jobs.Values)) {
        if ($job.state -in @('cloning', 'booting', 'configuring_guest', 'enrolling_tailscale', 'verifying')) {
            $job.state = 'failed'
            $job.errorCode = 'agent_restarted'
            $job.errorMessage = 'The worker restarted before verification completed.'
            $job.updatedAt = [DateTime]::UtcNow.ToString('o')
            $changed = $true
            continue
        }
        if ($job.state -eq 'ready') {
            $verify = Get-EpicVMProperty -Object $State.Provider -Name 'VerifyGuest' -Default $null
            $verified = $false
            if ($null -ne $verify) { try { $verified = [bool](& $verify $job.name $job.tailnetIp) } catch { $verified = $false } }
            if (-not $verified) {
                $job.state = 'failed'
                $job.errorCode = 'reverification_failed'
                $job.errorMessage = 'Readiness verification is required after agent restart.'
            }
            $job.updatedAt = [DateTime]::UtcNow.ToString('o')
            $changed = $true
        }
    }
    if ($changed) { Save-EpicVMProvisioningStore -Store $State.Provisioning }
}

function New-EpicVMProvisioningJob {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Request)
    $name = [string](Get-EpicVMProperty -Object $Request -Name 'name' -Default '')
    $profileName = [string](Get-EpicVMProperty -Object $Request -Name 'profile' -Default 'standard')
    if (-not (Test-EpicVMName $name)) { throw (New-EpicVMProvisioningError -Code 'invalid_name' -Message 'The VM name is invalid.' -Status 400) }
    try { $profile = Get-EpicVMProvisioningProfile -Profile $profileName }
    catch { throw (New-EpicVMProvisioningError -Code 'invalid_profile' -Message 'The VM profile is invalid.' -Status 400) }
    if ($profile.profile -eq 'gaming' -and -not [bool](Get-EpicVMProperty -Object $State.Config -Name 'EnableGamingProvisioning' -Default $false)) {
        throw (New-EpicVMProvisioningError -Code 'gaming_not_validated' -Message 'Gaming provisioning remains disabled until a GPU-P pilot passes.' -Status 409)
    }

    $existingJob = @($State.Provisioning.Jobs.Values | Where-Object {
        [string]$_.name -ceq $name -and $_.state -ne 'failed'
    }) | Select-Object -First 1
    if ($null -ne $existingJob) { throw (New-EpicVMProvisioningError -Code 'conflict' -Message 'The requested VM name already exists.' -Status 409) }
    $existing = @(& $State.Provider.GetVMs | Where-Object {
        [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '') -ceq $name
    })
    if ($existing.Count -gt 0) { throw (New-EpicVMProvisioningError -Code 'conflict' -Message 'The requested VM name already exists.' -Status 409) }

    if ($profile.profile -eq 'gaming') {
        $gamingNames = @('testre') + @((Get-EpicVMProperty -Object $State.Config -Name 'GamingVMNames' -Default @()))
        $running = @(& $State.Provider.GetVMs | Where-Object {
            $candidateName = [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '')
            $candidateState = [string](Get-EpicVMProperty -Object $_ -Name 'state' -Default '')
            ($gamingNames -contains $candidateName) -and $candidateState -ieq 'Running'
        })
        if ($running.Count -gt 0) { throw (New-EpicVMProvisioningError -Code 'gaming_capacity' -Message 'Only one Gaming VM may be running.' -Status 409) }
    }

    $job = New-EpicVMProvisioningJobObject -Id ([guid]::NewGuid().ToString('N')) -Name $name -Profile $profile.profile
    $State.Provisioning.Jobs[$job.id] = $job
    Save-EpicVMProvisioningStore -Store $State.Provisioning
    return $job
}

function Start-EpicVMProvisioningJob {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Job)
    try {
        if ($Job.state -ne 'queued') { return $null }
        if (-not (Test-EpicVMTemplateManifest -Config $State.Config)) {
            throw (New-EpicVMProvisioningError -Code 'template_invalid' -Message 'The EpicVM template manifest is missing or invalid.' -Status 503)
        }
        $Job.state = 'cloning'
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        $profile = Get-EpicVMProvisioningProfile -Profile $Job.profile
        $manifestPath = [string](Get-EpicVMProperty -Object $State.Config -Name 'TemplateManifestPath' -Default '')
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $Job.templateVersion = [string]$manifest.templateVersion
        $vm = & $State.Provider.CreateVM ([ordered]@{
            name = $Job.name; profile = $profile.profile; cpuCount = $profile.cpuCount
            memoryBytes = $profile.memoryBytes; diskSizeBytes = $profile.diskSizeBytes
            fullCopy = $true; templateRequired = $true; templateDiskPath = [string]$manifest.imagePath
        })
        $Job.vmId = [string](Get-EpicVMProperty -Object $vm -Name 'name' -Default $Job.name)
        $Job.state = 'booting'
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        & $State.Provider.StartVM $Job.name | Out-Null

        $claim = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
        $Job.claimHash = ConvertTo-EpicVMClaimHash -Value $claim
        $Job.claimExpires = [DateTime]::UtcNow.AddMinutes(30).ToString('o')
        $Job.claimUsed = $false
        $State.Provisioning.Claims[$Job.id] = $Job
        $Job.state = 'awaiting_claim'
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        return $claim
    }
    catch {
        Invoke-EpicVMProvisioningFailedCleanup -State $State -Job $Job
        $Job.state = 'failed'
        $Job.errorCode = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'provisioning_failed')
        $Job.errorMessage = 'Provisioning failed before guest configuration.'
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        throw
    }
}

function Invoke-EpicVMProvisioningClaim {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Job, [Parameter(Mandatory)] [object] $Request)
    $claim = [string](Get-EpicVMProperty -Object $Request -Name 'claimToken' -Default '')
    if ($Job.state -ne 'awaiting_claim') { throw (New-EpicVMProvisioningError -Code 'claim_not_allowed' -Message 'The VM is not awaiting a claim.' -Status 409) }
    if (-not (Test-EpicVMClaim -Job $Job -Claim $claim)) {
        throw (New-EpicVMProvisioningError -Code 'invalid_claim' -Message 'The claim is invalid or expired.' -Status 409)
    }
    $username = [string](Get-EpicVMProperty -Object $Request -Name 'username' -Default '')
    $password = [string](Get-EpicVMProperty -Object $Request -Name 'password' -Default '')
    if ($username -notmatch '^[A-Za-z][A-Za-z0-9._-]{2,31}$' -or [string]::IsNullOrEmpty($password)) {
        throw (New-EpicVMProvisioningError -Code 'invalid_credentials' -Message 'The claim credentials do not meet policy.' -Status 400)
    }
    $configure = Get-EpicVMProperty -Object $State.Provider -Name 'ConfigureGuest' -Default $null
    if ($null -eq $configure) { throw (New-EpicVMProvisioningError -Code 'guest_configuration_unavailable' -Message 'PowerShell Direct guest configuration is unavailable.' -Status 503) }

    # Consume the verifier before touching the guest. A failed worker can never
    # retry the same claim, and no plaintext claim value is persisted.
    $Job.claimUsed = $true
    $Job.claimHash = $null
    $Job.claimExpires = $null
    [void]$State.Provisioning.Claims.Remove($Job.id)
    Save-EpicVMProvisioningStore -Store $State.Provisioning
    try {
        $Job.state = 'configuring_guest'; $Job.updatedAt = [DateTime]::UtcNow.ToString('o'); Save-EpicVMProvisioningStore -Store $State.Provisioning
        & $configure $Job.name $username $password | Out-Null
        $Job.state = 'enrolling_tailscale'; $Job.updatedAt = [DateTime]::UtcNow.ToString('o'); Save-EpicVMProvisioningStore -Store $State.Provisioning
        $enroll = Get-EpicVMProperty -Object $State.Provider -Name 'EnrollTailscale' -Default $null
        if ($null -eq $enroll) { throw (New-EpicVMProvisioningError -Code 'tailscale_unavailable' -Message 'Tailscale enrollment is unavailable.' -Status 503) }
        $tailnet = & $enroll $Job.name $username $password
        $Job.tailnetIp = [string](Get-EpicVMProperty -Object $tailnet -Name 'ip' -Default '')
        $Job.tailnetDeviceId = [string](Get-EpicVMProperty -Object $tailnet -Name 'deviceId' -Default '')
        if ($Job.tailnetIp -notmatch '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}$' -or [string]::IsNullOrWhiteSpace($Job.tailnetDeviceId)) {
            throw (New-EpicVMProvisioningError -Code 'tailscale_verification_failed' -Message 'Tailscale enrollment did not return a verified device.' -Status 422)
        }
        # The Windows trust boundary ends here. The HTTPS dashboard retains the
        # request credentials only long enough to build the isolated console on
        # kvm2, then calls console-complete without any credential material.
        $Job.state = 'awaiting_console'
        $Job.errorCode = $null
        $Job.errorMessage = $null
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
    }
    catch {
        # Guest mutation may already have happened. Retain the exact owned VM
        # for diagnosis instead of deleting it automatically.
        $Job.state = 'failed'
        $Job.errorCode = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'claim_failed')
        $Job.errorMessage = 'Guest configuration or Tailscale enrollment failed; the VM was retained.'
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        throw
    }
    finally {
        $claim = $null; $username = $null; $password = $null
    }
}

function Set-EpicVMProvisioningConsoleFailed {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Job, [Parameter(Mandatory)] [object] $Request)
    if ($Job.state -notin @('awaiting_console', 'console_failed')) {
        throw (New-EpicVMProvisioningError -Code 'console_failure_not_allowed' -Message 'The job is not awaiting console configuration.' -Status 409)
    }
    $code = [string](Get-EpicVMProperty -Object $Request -Name 'code' -Default 'console_failed')
    if ($code -notmatch '^[a-z][a-z0-9_]{2,63}$') { $code = 'console_failed' }
    $Job.state = 'console_failed'
    $Job.errorCode = $code
    $Job.errorMessage = 'Console configuration failed; the VM and stopped console data were retained.'
    $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
    Save-EpicVMProvisioningStore -Store $State.Provisioning
}

function Complete-EpicVMProvisioningConsole {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Job, [Parameter(Mandatory)] [object] $Request)
    if ($Job.state -notin @('awaiting_console', 'console_failed')) {
        throw (New-EpicVMProvisioningError -Code 'console_complete_not_allowed' -Message 'The job is not awaiting console verification.' -Status 409)
    }
    $expectedRoute = '/vm/' + $Job.name + '/'
    $route = [string](Get-EpicVMProperty -Object $Request -Name 'routePrefix' -Default '')
    $serverVerified = [bool](Get-EpicVMProperty -Object $Request -Name 'guestTcpVerified' -Default $false)
    if ($route -cne $expectedRoute -or -not $serverVerified) {
        throw (New-EpicVMProvisioningError -Code 'console_verification_failed' -Message 'The kvm2 console evidence is incomplete.' -Status 422)
    }
    $Job.state = 'verifying'
    $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
    Save-EpicVMProvisioningStore -Store $State.Provisioning
    $verify = Get-EpicVMProperty -Object $State.Provider -Name 'VerifyGuest' -Default $null
    if ($null -eq $verify -or -not [bool](& $verify $Job.name $Job.tailnetIp)) {
        $Job.state = 'console_failed'
        $Job.errorCode = 'guest_reverification_failed'
        $Job.errorMessage = 'The guest did not pass credential-free RDP reachability verification.'
        $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        throw (New-EpicVMProvisioningError -Code 'guest_reverification_failed' -Message 'Guest verification failed.' -Status 422)
    }
    $Job.consoleRoutePrefix = $route
    $Job.consoleVerifiedAt = [DateTime]::UtcNow.ToString('o')
    $Job.state = 'ready'
    $Job.errorCode = $null
    $Job.errorMessage = $null
    $Job.updatedAt = [DateTime]::UtcNow.ToString('o')
    Save-EpicVMProvisioningStore -Store $State.Provisioning
}

function New-EpicVMDeprovisioningJob {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object] $Request)
    $name = [string](Get-EpicVMProperty -Object $Request -Name 'name' -Default '')
    $confirm = [string](Get-EpicVMProperty -Object $Request -Name 'confirmName' -Default '')
    if (-not (Test-EpicVMName $name) -or $confirm -cne $name) {
        throw (New-EpicVMProvisioningError -Code 'confirmation_required' -Message 'Exact VM name confirmation is required.' -Status 400)
    }
    $vm = @(& $State.Provider.GetVMs | Where-Object {
        [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '') -ceq $name
    }) | Select-Object -First 1
    if ($null -eq $vm -or -not [bool](Get-EpicVMProperty -Object $vm -Name 'managed' -Default $false)) {
        throw (New-EpicVMProvisioningError -Code 'ownership_required' -Message 'The VM is not an EpicVM-managed resource.' -Status 403)
    }
    $job = New-EpicVMProvisioningJobObject -Id ([guid]::NewGuid().ToString('N')) -Name $name -Profile 'standard'
    $State.Provisioning.Deprovisioning[$job.id] = $job
    Save-EpicVMProvisioningStore -Store $State.Provisioning
    try {
        $provisioned = @($State.Provisioning.Jobs.Values | Where-Object { [string]$_.name -ceq $name } | Select-Object -First 1)
        $deviceId = [string](Get-EpicVMProperty -Object $vm -Name 'tailnetDeviceId' -Default '')
        if (-not $deviceId -and $provisioned.Count -gt 0) { $deviceId = [string](Get-EpicVMProperty -Object $provisioned[0] -Name 'tailnetDeviceId' -Default '') }
        $console = Get-EpicVMProperty -Object $State.Provider -Name 'TeardownConsole' -Default $null
        if ($null -ne $console) { & $console $name $confirm $deviceId | Out-Null }
        $revoke = Get-EpicVMProperty -Object $State.Provider -Name 'RevokeTailscale' -Default $null
        if ($null -ne $revoke) { & $revoke $deviceId | Out-Null }
        if ([string](Get-EpicVMProperty -Object $vm -Name 'state' -Default '') -ieq 'Running') { & $State.Provider.StopVM $name | Out-Null }
        & $State.Provider.DeleteVM $name | Out-Null
        $quarantine = Get-EpicVMProperty -Object $State.Provider -Name 'QuarantineVM' -Default $null
        if ($null -eq $quarantine) { throw (New-EpicVMProvisioningError -Code 'quarantine_unavailable' -Message 'Quarantine is unavailable.' -Status 503) }
        $quarantineResult = & $quarantine $name
        $job.state = 'ready'
        $job.quarantineUntil = [string](Get-EpicVMProperty -Object $quarantineResult -Name 'quarantineUntil' -Default ([DateTime]::UtcNow.AddDays(7).ToString('o')))
        $job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
    }
    catch {
        $job.state = 'failed'
        $job.errorCode = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'deprovisioning_failed')
        $job.errorMessage = 'Teardown was not completed.'
        $job.updatedAt = [DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        throw (New-EpicVMProvisioningError -Code $job.errorCode -Message 'Teardown was not completed.' -Status 422)
    }
    return $job
}
