# EpicVM provisioning primitives.  This file deliberately persists only a
# redacted job record; claim credentials and one-use material remain in memory.

Set-StrictMode -Version Latest

$script:EpicVMProvisioningStates = @('queued','cloning','booting','awaiting_claim','configuring_guest','enrolling_tailscale','configuring_console','verifying','ready','failed')

function Get-EpicVMProvisioningProfile {
    param([Parameter(Mandatory)][string]$Profile)
    switch ($Profile.ToLowerInvariant()) {
        'standard' { return [ordered]@{ profile='standard'; cpuCount=4; memoryBytes=8589934592; diskSizeBytes=103079215104; gpu=$false } }
        'gaming'  { return [ordered]@{ profile='gaming'; cpuCount=6; memoryBytes=12884901888; diskSizeBytes=137438953472; gpu=$true; gpuPartition='50%' } }
        default { throw 'Unsupported EpicVM profile.' }
    }
}

function New-EpicVMProvisioningStore {
    param([Parameter(Mandatory)][object]$Config)
    $path = [string](Get-EpicVMProperty -Object $Config -Name 'ProvisioningStatePath' -Default '')
    if ([string]::IsNullOrWhiteSpace($path)) {
        $root = [string](Get-EpicVMProperty -Object $Config -Name 'VmRoot' -Default 'C:\ProgramData\EpicVM\vms')
        $path = Join-Path $root '..\provisioning-jobs.json'
    }
    return [pscustomobject]@{ Path=[IO.Path]::GetFullPath($path); Jobs=@{}; Deprovisioning=@{}; Claims=@{}; SyncRoot=[object]::new() }
}

function ConvertTo-EpicVMRedactedJob {
    param([Parameter(Mandatory)][object]$Job)
    $safe = [ordered]@{}
    foreach ($name in @('id','name','profile','state','createdAt','updatedAt','errorCode','errorMessage','templateVersion','vmId','quarantineUntil')) {
        $value = Get-EpicVMProperty -Object $Job -Name $name -Default $null
        if ($null -ne $value) { $safe[$name] = $value }
    }
    return $safe
}

function Save-EpicVMProvisioningStore {
    param([Parameter(Mandatory)][object]$Store)
    $parent = Split-Path -Parent $Store.Path
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $records = @($Store.Jobs.Values + $Store.Deprovisioning.Values | ForEach-Object { ConvertTo-EpicVMRedactedJob -Job $_ })
    $tmp = "$($Store.Path).tmp"
    $records | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $tmp -Encoding UTF8 -NoNewline
    Move-Item -LiteralPath $tmp -Destination $Store.Path -Force
}

function Test-EpicVMTemplateManifest {
    param([Parameter(Mandatory)][object]$Config)
    $manifestPath = [string](Get-EpicVMProperty -Object $Config -Name 'TemplateManifestPath' -Default '')
    if ([string]::IsNullOrWhiteSpace($manifestPath) -or -not (Test-Path -LiteralPath $manifestPath)) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($field in @('templateVersion','build','sha256','bootstrap','gpu')) {
            if ([string]::IsNullOrWhiteSpace([string](Get-EpicVMProperty -Object $manifest -Name $field -Default ''))) { return $false }
        }
        $imagePath = [string](Get-EpicVMProperty -Object $manifest -Name 'imagePath' -Default '')
        if ([string]::IsNullOrWhiteSpace($imagePath) -or -not (Test-Path -LiteralPath $imagePath)) { return $false }
        $actual = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash.ToLowerInvariant()
        return $actual -eq ([string]$manifest.sha256).ToLowerInvariant()
    } catch { return $false }
}

function New-EpicVMProvisioningJob {
    param([Parameter(Mandatory)][object]$State,[Parameter(Mandatory)][object]$Request)
    $name = [string](Get-EpicVMProperty -Object $Request -Name 'name' -Default '')
    $profileName = [string](Get-EpicVMProperty -Object $Request -Name 'profile' -Default 'standard')
    if (-not (Test-EpicVMName $name)) { throw 'The VM name is invalid.' }
    $profile = Get-EpicVMProvisioningProfile -Profile $profileName
    $existing = @(& $State.Provider.GetVMs | Where-Object { [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '') -eq $name })
    if ($existing.Count -gt 0) { throw 'The requested VM name already exists.' }
    if ($profile.profile -eq 'gaming') {
        $gamingNames = @('testre') + @((Get-EpicVMProperty -Object $State.Config -Name 'GamingVMNames' -Default @()))
        $runningGaming = @(& $State.Provider.GetVMs | Where-Object {
            $n=[string](Get-EpicVMProperty -Object $_ -Name 'name' -Default ''); $s=[string](Get-EpicVMProperty -Object $_ -Name 'state' -Default '')
            ($gamingNames -contains $n) -and $s -ieq 'Running'
        })
        if ($runningGaming.Count -gt 0) { throw 'Only one Gaming VM may be running.' }
    }
    $id = [guid]::NewGuid().ToString('N')
    $now = [DateTime]::UtcNow.ToString('o')
    $job = [pscustomobject]@{ id=$id; name=$name; profile=$profile.profile; state='queued'; createdAt=$now; updatedAt=$now; templateVersion=$null; vmId=$null; errorCode=$null; errorMessage=$null }
    $State.Provisioning.Jobs[$id] = $job
    Save-EpicVMProvisioningStore -Store $State.Provisioning
    return $job
}

function Start-EpicVMProvisioningJob {
    param([Parameter(Mandatory)][object]$State,[Parameter(Mandatory)][object]$Job)
    try {
        if (-not (Test-EpicVMTemplateManifest -Config $State.Config)) { throw 'The EpicVM template manifest is missing or invalid.' }
        $Job.state='cloning'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
        $profile = Get-EpicVMProvisioningProfile -Profile $Job.profile
        $manifestPath = [string](Get-EpicVMProperty -Object $State.Config -Name 'TemplateManifestPath' -Default '')
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $vm = & $State.Provider.CreateVM ([ordered]@{ name=$Job.name; profile=$profile.profile; cpuCount=$profile.cpuCount; memoryBytes=$profile.memoryBytes; diskSizeBytes=$profile.diskSizeBytes; fullCopy=$true; templateRequired=$true; templateDiskPath=[string]$manifest.imagePath })
        $Job.vmId = [string](Get-EpicVMProperty -Object $vm -Name 'name' -Default $Job.name)
        $Job.state='booting'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
        & $State.Provider.StartVM $Job.name | Out-Null
        $claim = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
        $State.Provisioning.Claims[$Job.id] = [pscustomobject]@{ Hash=(ConvertTo-EpicVMClaimHash $claim); Expires=[DateTime]::UtcNow.AddMinutes(30); Used=$false; Token=$claim }
        $Job.state='awaiting_claim'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        return $claim
    } catch {
        $Job.state='failed'; $Job.errorCode='provisioning_failed'; $Job.errorMessage='Provisioning failed before guest configuration.'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
        throw
    }
}

function ConvertTo-EpicVMClaimHash { param([Parameter(Mandatory)][string]$Value)
    $bytes=[Text.Encoding]::UTF8.GetBytes($Value); $hash=[Security.Cryptography.SHA256]::HashData($bytes); return ([Convert]::ToHexString($hash)).ToLowerInvariant()
}

function Invoke-EpicVMProvisioningClaim {
    param([Parameter(Mandatory)][object]$State,[Parameter(Mandatory)][object]$Job,[Parameter(Mandatory)][object]$Request)
    $claim=[string](Get-EpicVMProperty -Object $Request -Name 'claimToken' -Default '')
    $entry=$State.Provisioning.Claims[$Job.id]
    if ($null -eq $entry -or $entry.Used -or [DateTime]::UtcNow -gt $entry.Expires -or (ConvertTo-EpicVMClaimHash $claim) -ne $entry.Hash) { throw 'The claim is invalid or expired.' }
    $username=[string](Get-EpicVMProperty -Object $Request -Name 'username' -Default '')
    $password=[string](Get-EpicVMProperty -Object $Request -Name 'password' -Default '')
    if ($username -notmatch '^[A-Za-z][A-Za-z0-9._-]{2,31}$' -or $password.Length -lt 12) { throw 'The claim credentials do not meet policy.' }
    $configure = Get-EpicVMProperty -Object $State.Provider -Name 'ConfigureGuest' -Default $null
    if ($null -eq $configure) { throw 'PowerShell Direct guest configuration is unavailable.' }
    $Job.state='configuring_guest'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
    & $configure $Job.name $username $password | Out-Null
    $entry.Used=$true; $entry.Token=$null
    $Job.state='enrolling_tailscale'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
    $enroll = Get-EpicVMProperty -Object $State.Provider -Name 'EnrollTailscale' -Default $null
    if ($null -eq $enroll) { throw 'Tailscale enrollment is unavailable.' }
    & $enroll $Job.name | Out-Null
    $Job.state='configuring_console'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
    $console = Get-EpicVMProperty -Object $State.Provider -Name 'ConfigureConsole' -Default $null
    if ($null -eq $console) { throw 'Console configuration is unavailable.' }
    & $console $Job.name | Out-Null
    $Job.state='verifying'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
    $verify = Get-EpicVMProperty -Object $State.Provider -Name 'VerifyGuest' -Default $null
    if ($null -eq $verify -or -not (& $verify $Job.name)) { throw 'Guest verification failed.' }
    $Job.state='ready'; $Job.updatedAt=[DateTime]::UtcNow.ToString('o')
    Save-EpicVMProvisioningStore -Store $State.Provisioning
}

function New-EpicVMDeprovisioningJob {
    param([Parameter(Mandatory)][object]$State,[Parameter(Mandatory)][object]$Request)
    $name=[string](Get-EpicVMProperty -Object $Request -Name 'name' -Default '')
    $confirm=[string](Get-EpicVMProperty -Object $Request -Name 'confirmName' -Default '')
    if (-not (Test-EpicVMName $name) -or $confirm -cne $name) { throw 'Exact VM name confirmation is required.' }
    $vm=@(& $State.Provider.GetVMs | Where-Object { [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '') -ceq $name }) | Select-Object -First 1
    if ($null -eq $vm -or -not [bool](Get-EpicVMProperty -Object $vm -Name 'managed' -Default $false)) { throw 'The VM is not an EpicVM-managed resource.' }
    $id=[guid]::NewGuid().ToString('N'); $now=[DateTime]::UtcNow.ToString('o')
    $job=[pscustomobject]@{ id=$id; name=$name; state='queued'; createdAt=$now; updatedAt=$now; quarantineUntil=$null; errorCode=$null; errorMessage=$null }
    $State.Provisioning.Deprovisioning[$id]=$job; Save-EpicVMProvisioningStore -Store $State.Provisioning
    try {
        if ([string](Get-EpicVMProperty -Object $vm -Name 'state' -Default '') -ieq 'Running') { & $State.Provider.StopVM $name | Out-Null }
        & $State.Provider.DeleteVM $name | Out-Null
        $job.state='ready'; $job.quarantineUntil=[DateTime]::UtcNow.AddDays(7).ToString('o'); $job.updatedAt=[DateTime]::UtcNow.ToString('o')
        Save-EpicVMProvisioningStore -Store $State.Provisioning
    } catch {
        $job.state='failed'; $job.errorCode='deprovisioning_failed'; $job.errorMessage='Teardown was not completed.'; $job.updatedAt=[DateTime]::UtcNow.ToString('o'); Save-EpicVMProvisioningStore -Store $State.Provisioning; throw
    }
    return $job
}
