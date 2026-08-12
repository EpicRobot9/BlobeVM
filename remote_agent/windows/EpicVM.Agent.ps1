# Requires -Version 7.0
<#
    EpicVM RemoteVM host agent.

    The script is both a small HTTP service and a dot-sourceable request
    dispatcher.  Pester tests use -NoStart and call Invoke-EpicVMApiRequest
    directly, while the Windows service calls Start-EpicVMAgent.
#>

[CmdletBinding()]
param(
    [switch] $NoStart,
    [string] $ConfigPath = (Join-Path $PSScriptRoot 'config.json'),
    [string] $BindAddress,
    [int] $Port = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$providerPath = Join-Path $PSScriptRoot 'providers/HyperVProvider.ps1'
if (Test-Path -LiteralPath $providerPath) {
    . $providerPath
}
$provisioningPath = Join-Path $PSScriptRoot 'Provisioning.ps1'
if (Test-Path -LiteralPath $provisioningPath) {
    . $provisioningPath
}
foreach ($providerExtension in @('GuestProvider.ps1','TailscaleProvider.ps1')) {
    $extensionPath = Join-Path $PSScriptRoot ('providers/' + $providerExtension)
    if (Test-Path -LiteralPath $extensionPath) { . $extensionPath }
}

function Get-EpicVMDefaultConfig {
    return [pscustomobject]@{
        BindAddress = '127.0.0.1'
        Port = 8765
        Provider = 'HyperV'
        TokenFile = 'C:\ProgramData\EpicVM\agent\agent.txt'
        ConfigFile = 'C:\ProgramData\EpicVM\agent\config.json'
        VmRoot = 'E:\EpicVM\vms'
        SwitchName = ''
        DefaultMemoryBytes = 4294967296
        DefaultCpuCount = 2
        DefaultDiskSizeBytes = 68719476736
        MinMemoryBytes = 536870912
        MaxMemoryBytes = 17179869184
        MinCpuCount = 1
        MaxCpuCount = 16
        MaxDiskSizeBytes = 549755813888
        Generation = 2
        TemplateManifestPath = 'E:\EpicVM\templates\win11-25h2\manifest.json'
        ProvisioningStatePath = 'E:\EpicVM\provisioning-jobs.json'
        GamingVMNames = @('testre')
        BootstrapUser = 'EpicVMBootstrap'
        BootstrapCredentialPath = 'C:\ProgramData\EpicVM\agent\bootstrap.dpapi'
        TailscaleOAuthClientId = ''
        TailscaleOAuthSecretPath = 'C:\ProgramData\EpicVM\agent\tailscale-oauth.dpapi'
        TailscaleTailnet = ''
        TailscaleGuestTag = 'tag:epicvm-guest'
        TailscaleExecutable = 'C:\Program Files\Tailscale\tailscale.exe'
        SunshineServiceName = 'SunshineService'
        SunshineVersion = '2026.516.143833'
        SunshineStatePaths = @(
            'C:\Program Files\Sunshine\config\sunshine_state.json',
            'C:\ProgramData\Sunshine\config\sunshine_state.json'
        )
        EnableGamingProvisioning = $false
    }
}

function Get-EpicVMProperty {
    param(
        [AllowNull()] [object] $Object,
        [Parameter(Mandatory)] [string] $Name,
        [AllowNull()] [object] $Default = $null
    )
    if ($null -eq $Object) { return $Default }
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

function Read-EpicVMConfig {
    param([Parameter(Mandatory)] [string] $Path)
    $defaults = Get-EpicVMDefaultConfig
    if (-not (Test-Path -LiteralPath $Path)) { return $defaults }
    try {
        $loaded = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw 'The EpicVM agent configuration is invalid JSON.'
    }
    foreach ($property in $defaults.PSObject.Properties) {
        $value = Get-EpicVMProperty -Object $loaded -Name $property.Name -Default $property.Value
        $defaults.$($property.Name) = $value
    }
    return $defaults
}

function Get-EpicVMAgentToken {
    param([Parameter(Mandatory)] [object] $Config)
    $token = [string](Get-EpicVMProperty -Object $Config -Name 'Token' -Default '')
    if ($token) { return $token.Trim() }
    $tokenPath = [string](Get-EpicVMProperty -Object $Config -Name 'TokenFile' -Default '')
    if (-not $tokenPath -or -not (Test-Path -LiteralPath $tokenPath)) {
        throw 'The EpicVM agent token file is missing.'
    }
    $token = (Get-Content -LiteralPath $tokenPath -Raw -Encoding UTF8).Trim()
    if (-not $token) { throw 'The EpicVM agent token file is empty.' }
    return $token
}

function Test-EpicVMBearerToken {
    param(
        [AllowNull()] [string] $ProvidedToken,
        [AllowNull()] [string] $ExpectedToken
    )
    if ([string]::IsNullOrEmpty($ProvidedToken) -or [string]::IsNullOrEmpty($ExpectedToken)) { return $false }
    $left = [Text.Encoding]::UTF8.GetBytes($ProvidedToken)
    $right = [Text.Encoding]::UTF8.GetBytes($ExpectedToken)
    if ($left.Length -ne $right.Length) { return $false }
    return [Security.Cryptography.CryptographicOperations]::FixedTimeEquals($left, $right)
}

function Test-EpicVMName {
    param([AllowNull()] [string] $Name)
    if ($null -eq $Name) { return $false }
    return [regex]::IsMatch($Name, '\A[a-z0-9][a-z0-9._-]{0,62}\z', [Text.RegularExpressions.RegexOptions]::CultureInvariant)
}

function Test-EpicVMBindAddress {
    param([AllowNull()] [string] $Address)
    if ([string]::IsNullOrWhiteSpace($Address) -or $Address -in @('0.0.0.0', '::', '[::]')) {
        return $false
    }
    try {
        $normalizedAddress = $Address.Trim()
        if ($normalizedAddress.StartsWith('[') -and $normalizedAddress.EndsWith(']')) {
            $normalizedAddress = $normalizedAddress.Substring(1, $normalizedAddress.Length - 2)
        }
        $ip = [System.Net.IPAddress]::Parse($normalizedAddress)
    }
    catch {
        return $false
    }
    if ([System.Net.IPAddress]::IsLoopback($ip)) {
        return $true
    }
    $bytes = $ip.GetAddressBytes()
    return (
        $ip.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
        $bytes.Length -eq 4 -and $bytes[0] -eq 100 -and $bytes[1] -ge 64 -and $bytes[1] -le 127
    )
}

function New-EpicVMAgentState {
    param(
        [Parameter(Mandatory)] [object] $Config,
        [Parameter(Mandatory)] [string] $Token,
        [Parameter(Mandatory)] [object] $Provider
    )
    $agentState = [pscustomobject]@{
        Config = $Config
        Token = $Token
        Provider = $Provider
        StartedAt = [DateTime]::UtcNow
        SyncRoot = [object]::new()
        CompletedOperations = @{}
    }
    if (Get-Command -Name New-EpicVMProvisioningStore -ErrorAction SilentlyContinue) {
        $agentState | Add-Member -MemberType NoteProperty -Name Provisioning -Value (New-EpicVMProvisioningStore -Config $Config)
        if (Get-Command -Name Invoke-EpicVMProvisioningRecovery -ErrorAction SilentlyContinue) {
            Invoke-EpicVMProvisioningRecovery -State $agentState
        }
    }
    return $agentState
}

function New-EpicVMApiError {
    param([Parameter(Mandatory)] [string] $Code, [Parameter(Mandatory)] [string] $Message)
    return [ordered]@{ ok = $false; error = [ordered]@{ code = $Code; message = $Message } }
}

function ConvertTo-EpicVMJsonResponse {
    param(
        [Parameter(Mandatory)] [int] $StatusCode,
        [Parameter(Mandatory)] [object] $Body
    )
    return [pscustomobject]@{
        StatusCode = $StatusCode
        Body = $Body
        Json = ($Body | ConvertTo-Json -Depth 16 -Compress)
        Headers = @{ 'Cache-Control' = 'no-store'; 'Pragma' = 'no-cache' }
    }
}

function Get-EpicVMHeader {
    param([AllowNull()] [object] $Headers, [Parameter(Mandatory)] [string] $Name)
    if ($null -eq $Headers) { return '' }
    if ($Headers -is [System.Collections.IDictionary]) {
        foreach ($key in $Headers.Keys) {
            if ([string]::Equals([string]$key, $Name, [System.StringComparison]::OrdinalIgnoreCase)) { return [string]$Headers[$key] }
        }
    }
    return [string](Get-EpicVMProperty -Object $Headers -Name $Name -Default '')
}

function Get-EpicVMRequestBody {
    param([AllowNull()] [object] $Body)
    if ($null -eq $Body -or $Body -eq '') { return @{} }
    if ($Body -is [string]) {
        try { return ($Body | ConvertFrom-Json -AsHashtable) } catch { throw 'Request body is not valid JSON.' }
    }
    if ($Body -is [System.Collections.IDictionary]) { return $Body }
    return $Body
}

function New-EpicVMProvider {
    param(
        [Parameter(Mandatory)] [object] $Config,
        [AllowNull()] [scriptblock] $CommandInvoker = $null
    )
    switch ([string](Get-EpicVMProperty -Object $Config -Name 'Provider' -Default 'HyperV')) {
        'HyperV' {
            return New-EpicVMHyperVProvider -Config $Config -CommandInvoker $CommandInvoker
        }
        default {
            throw ("Unsupported provider: {0}" -f (Get-EpicVMProperty -Object $Config -Name 'Provider' -Default ''))
        }
    }
}

function Get-EpicVMProviderCapabilities {
    param([Parameter(Mandatory)] [object] $Provider)
    try {
        $capabilities = & $Provider.GetCapabilities
        if ($capabilities -is [System.Collections.IDictionary]) { return $capabilities }
        return [ordered]@{ provider = [string]$Provider.Name; available = $false; features = @() }
    }
    catch {
        return [ordered]@{ provider = [string]$Provider.Name; available = $false; features = @(); error = 'provider_unavailable' }
    }
}

function Add-EpicVMProvisioningInventoryState {
    param([Parameter(Mandatory)] [object] $State, [Parameter(Mandatory)] [object[]] $Vms)
    foreach ($vm in $Vms) {
        $name = [string](Get-EpicVMProperty -Object $vm -Name 'name' -Default '')
        $job = @($State.Provisioning.Jobs.Values | Where-Object { [string]$_.name -ceq $name } | Sort-Object updatedAt -Descending | Select-Object -First 1)
        if ($job.Count -eq 0) { continue }
        if ($vm -is [System.Collections.IDictionary]) {
            $vm['provisioningState'] = [string]$job[0].state
            $vm['consoleReady'] = ([string]$job[0].state -ceq 'ready')
        }
        else {
            $vm | Add-Member -MemberType NoteProperty -Name provisioningState -Value ([string]$job[0].state) -Force
            $vm | Add-Member -MemberType NoteProperty -Name consoleReady -Value ([string]$job[0].state -ceq 'ready') -Force
        }
    }
    return $Vms
}

function Invoke-EpicVMProviderAction {
    param(
        [Parameter(Mandatory)] [object] $Provider,
        [Parameter(Mandatory)] [string] $Action,
        [Parameter(Mandatory)] [string] $Name,
        [AllowNull()] [object] $Request = @{}
    )
    switch ($Action.ToLowerInvariant()) {
        'start' { return (& $Provider.StartVM $Name) }
        'stop' { return (& $Provider.StopVM $Name) }
        'restart' { return (& $Provider.RestartVM $Name) }
        'delete' { return (& $Provider.DeleteVM $Name) }
        default { throw "Unsupported VM action: $Action" }
    }
}

function Invoke-EpicVMApiRequest {
    param(
        [Parameter(Mandatory)] [object] $State,
        [Parameter(Mandatory)] [ValidateSet('GET', 'POST', 'DELETE')] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [AllowNull()] [object] $Headers = @{},
        [AllowNull()] [object] $Body = $null
    )
    $authorization = Get-EpicVMHeader -Headers $Headers -Name 'Authorization'
    $provided = ''
    if ($authorization -match '^Bearer\s+(.+)$') { $provided = $Matches[1].Trim() }
    if (-not (Test-EpicVMBearerToken -ProvidedToken $provided -ExpectedToken ([string]$State.Token))) {
        return ConvertTo-EpicVMJsonResponse -StatusCode 401 -Body (New-EpicVMApiError -Code 'unauthorized' -Message 'Authentication required.')
    }

    $normalizedPath = '/' + $Path.Trim('/')
    $segments = @($normalizedPath.Trim('/').Split('/') | Where-Object { $_ -ne '' } | ForEach-Object { [Uri]::UnescapeDataString($_) })
    try {
        if ($Method -eq 'GET' -and $normalizedPath -eq '/v1/health') {
            return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; status = 'ok'; agent = 'EpicVM'; provider = [string]$State.Provider.Name })
        }
        if ($Method -eq 'GET' -and $normalizedPath -eq '/v1/capabilities') {
            $caps = Get-EpicVMProviderCapabilities -Provider $State.Provider
            $caps.ok = $true
            return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body $caps
        }
        if ($Method -eq 'GET' -and $segments.Count -eq 2 -and $segments[0] -eq 'v1' -and $segments[1] -eq 'vms') {
            $vms = @(& $State.Provider.GetVMs)
            if ($null -ne $State.Provisioning) { $vms = @(Add-EpicVMProvisioningInventoryState -State $State -Vms $vms) }
            return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; vms = $vms })
        }
        if ($null -ne $State.Provisioning -and $Method -eq 'POST' -and $normalizedPath -eq '/v1/provisioning-jobs') {
            $request = Get-EpicVMRequestBody -Body $Body
            try { $job = New-EpicVMProvisioningJob -State $State -Request $request }
            catch {
                $code = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'invalid_request')
                $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 422)
                return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body (New-EpicVMApiError -Code $code -Message ([string]$_.Exception.Message))
            }
            try { $claim = Start-EpicVMProvisioningJob -State $State -Job $job }
            catch {
                $code = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'provisioning_failed')
                $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 422)
                return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body ([ordered]@{ ok=$false; error=[ordered]@{code=$code;message='Provisioning failed.'}; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
            }
            return ConvertTo-EpicVMJsonResponse -StatusCode 202 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job); claimToken=$claim })
        }
        if ($null -ne $State.Provisioning -and $segments.Count -ge 3 -and $segments[0] -eq 'v1' -and $segments[1] -eq 'provisioning-jobs') {
            $job = $State.Provisioning.Jobs[$segments[2]]
            if ($null -eq $job) { return ConvertTo-EpicVMJsonResponse -StatusCode 404 -Body (New-EpicVMApiError -Code 'not_found' -Message 'The provisioning job was not found.') }
            if ($Method -eq 'GET') { return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) }) }
            if ($Method -eq 'POST' -and $segments.Count -eq 4 -and $segments[3] -eq 'claim') {
                try { Invoke-EpicVMProvisioningClaim -State $State -Job $job -Request (Get-EpicVMRequestBody -Body $Body) }
                catch {
                    $code = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'claim_failed')
                    $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 422)
                    if ($job.state -ne 'failed' -and $code -notin @('invalid_claim','invalid_credentials','claim_not_allowed')) { $job.state='failed'; $job.errorCode=$code; $job.errorMessage='Guest claim or readiness verification failed.'; Save-EpicVMProvisioningStore -Store $State.Provisioning }
                    return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body ([ordered]@{ ok=$false; error=[ordered]@{code=$code;message=if($code -in @('invalid_claim','invalid_credentials','claim_not_allowed')){[string]$_.Exception.Message}else{'Guest claim failed.'}}; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
                }
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
            }
            if ($Method -eq 'POST' -and $segments.Count -eq 4 -and $segments[3] -eq 'console-credentials') {
                try { Set-EpicVMProvisioningConsoleCredentials -State $State -Job $job -Request (Get-EpicVMRequestBody -Body $Body) }
                catch {
                    $code = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'console_credentials_failed')
                    $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 422)
                    return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body ([ordered]@{ ok=$false; error=[ordered]@{code=$code;message=[string]$_.Exception.Message}; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
                }
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
            }
            if ($Method -eq 'POST' -and $segments.Count -eq 4 -and $segments[3] -eq 'console-complete') {
                try { Complete-EpicVMProvisioningConsole -State $State -Job $job -Request (Get-EpicVMRequestBody -Body $Body) }
                catch {
                    $code = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'console_verification_failed')
                    $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 422)
                    return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body ([ordered]@{ ok=$false; error=[ordered]@{code=$code;message=[string]$_.Exception.Message}; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
                }
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
            }
            if ($Method -eq 'POST' -and $segments.Count -eq 4 -and $segments[3] -eq 'console-failed') {
                try { Set-EpicVMProvisioningConsoleFailed -State $State -Job $job -Request (Get-EpicVMRequestBody -Body $Body) }
                catch {
                    $code = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'console_failure_not_allowed')
                    $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 409)
                    return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body (New-EpicVMApiError -Code $code -Message ([string]$_.Exception.Message))
                }
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
            }
        }
        if ($null -ne $State.Provisioning -and $Method -eq 'POST' -and $normalizedPath -eq '/v1/deprovisioning-jobs') {
            try { $job=New-EpicVMDeprovisioningJob -State $State -Request (Get-EpicVMRequestBody -Body $Body) }
            catch { $code=[string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'teardown_rejected');$status=[int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default 422);return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body (New-EpicVMApiError -Code $code -Message ([string]$_.Exception.Message)) }
            return ConvertTo-EpicVMJsonResponse -StatusCode 202 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
        }
        if ($null -ne $State.Provisioning -and $segments.Count -eq 3 -and $segments[0] -eq 'v1' -and $segments[1] -eq 'deprovisioning-jobs' -and $Method -eq 'GET') {
            $job=$State.Provisioning.Deprovisioning[$segments[2]]
            if ($null -eq $job) { return ConvertTo-EpicVMJsonResponse -StatusCode 404 -Body (New-EpicVMApiError -Code 'not_found' -Message 'The deprovisioning job was not found.') }
            return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok=$true; job=(ConvertTo-EpicVMRedactedJob -Job $job) })
        }
        if ($Method -eq 'POST' -and $segments.Count -eq 2 -and $segments[0] -eq 'v1' -and $segments[1] -eq 'vms') {
            $request = Get-EpicVMRequestBody -Body $Body
            $name = [string](Get-EpicVMProperty -Object $request -Name 'name' -Default (Get-EpicVMProperty -Object $request -Name 'Name' -Default ''))
            if (-not (Test-EpicVMName -Name $name)) {
                return ConvertTo-EpicVMJsonResponse -StatusCode 400 -Body (New-EpicVMApiError -Code 'invalid_name' -Message 'The VM name is invalid.')
            }
            $vm = & $State.Provider.CreateVM $request
            return ConvertTo-EpicVMJsonResponse -StatusCode 201 -Body ([ordered]@{ ok = $true; vm = $vm })
        }
        if ($segments.Count -ge 3 -and $segments[0] -eq 'v1' -and $segments[1] -eq 'vms') {
            $name = $segments[2]
            if (-not (Test-EpicVMName -Name $name)) {
                return ConvertTo-EpicVMJsonResponse -StatusCode 400 -Body (New-EpicVMApiError -Code 'invalid_name' -Message 'The VM name is invalid.')
            }
            if ($Method -eq 'GET' -and $segments.Count -eq 3) {
                $vms = @(& $State.Provider.GetVMs)
                $vm = @($vms | Where-Object { [string](Get-EpicVMProperty -Object $_ -Name 'name' -Default '') -eq $name }) | Select-Object -First 1
                if ($null -eq $vm) { return ConvertTo-EpicVMJsonResponse -StatusCode 404 -Body (New-EpicVMApiError -Code 'not_found' -Message 'The requested VM was not found.') }
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; vm = $vm })
            }
            if ($Method -eq 'GET' -and $segments.Count -eq 4 -and $segments[3] -eq 'logs') {
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; logs = ''; supported = $false })
            }
            if ($Method -eq 'POST' -and $segments.Count -eq 4 -and $segments[3] -in @('start', 'stop', 'restart')) {
                $result = Invoke-EpicVMProviderAction -Provider $State.Provider -Action $segments[3] -Name $name
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; vm = $result })
            }
            # Keep the first draft's action route as a compatibility alias for
            # already-installed clients; new clients use the documented REST
            # lifecycle paths above and DELETE /v1/vms/{name}.
            if ($Method -eq 'POST' -and $segments.Count -eq 5 -and $segments[3] -eq 'actions') {
                $result = Invoke-EpicVMProviderAction -Provider $State.Provider -Action $segments[4] -Name $name
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; vm = $result })
            }
            if ($Method -eq 'DELETE' -and $segments.Count -eq 3) {
                $result = Invoke-EpicVMProviderAction -Provider $State.Provider -Action 'delete' -Name $name
                return ConvertTo-EpicVMJsonResponse -StatusCode 200 -Body ([ordered]@{ ok = $true; vm = $result })
            }
        }
        return ConvertTo-EpicVMJsonResponse -StatusCode 404 -Body (New-EpicVMApiError -Code 'not_found' -Message 'Route not found.')
    }
    catch {
        $errorCode = [string](Get-EpicVMProperty -Object $_.Exception -Name 'ErrorCode' -Default 'provider_error')
        $message = [string]$_.Exception.Message
        if ($message -match 'token|authorization|secret|password') { $message = 'The provider request failed.' }
        $status = [int](Get-EpicVMProperty -Object $_.Exception -Name 'HttpStatus' -Default (if ($errorCode -eq 'NotFound') { 404 } elseif ($errorCode -eq 'InvalidInput') { 400 } elseif ($errorCode -eq 'Conflict') { 409 } elseif ($errorCode -eq 'UnmanagedVM') { 403 } else { 502 }))
        return ConvertTo-EpicVMJsonResponse -StatusCode $status -Body (New-EpicVMApiError -Code $errorCode.ToLowerInvariant() -Message $message)
    }
}

function Test-EpicVMMutationRequest {
    param(
        [Parameter(Mandatory)] [string] $Method,
        [Parameter(Mandatory)] [string] $Path
    )
    if ($Method -eq 'DELETE' -and $Path -match '^/v1/vms/[^/]+$') { return $true }
    if ($Method -eq 'POST' -and ($Path -eq '/v1/provisioning-jobs' -or $Path -eq '/v1/deprovisioning-jobs' -or $Path -match '^/v1/provisioning-jobs/[^/]+/(claim|console-credentials|console-complete|console-failed)$')) { return $true }
    if ($Method -eq 'POST' -and ($Path -eq '/v1/vms' -or $Path -match '^/v1/vms/[^/]+/(start|stop|restart)$' -or $Path -match '^/v1/vms/[^/]+/actions/(start|stop|restart|delete)$')) { return $true }
    return $false
}

function Read-EpicVMBoundedBody {
    param(
        [Parameter(Mandatory)] [System.IO.Stream] $Stream,
        [int] $MaxBytes = 1048576
    )
    $buffer = [byte[]]::new(8192)
    $memory = [System.IO.MemoryStream]::new()
    try {
        while (($read = $Stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($memory.Length + $read -gt $MaxBytes) {
                return [pscustomobject]@{ TooLarge = $true; Body = $null }
            }
            $memory.Write($buffer, 0, $read)
        }
        return [pscustomobject]@{
            TooLarge = $false
            Body = [Text.Encoding]::UTF8.GetString($memory.ToArray())
        }
    }
    finally {
        $memory.Dispose()
    }
}

function Start-EpicVMAgent {
    param(
        [Parameter(Mandatory)] [object] $State,
        [string] $Bind = '127.0.0.1',
        [int] $ListenPort = 8765
    )
    $listener = [Net.HttpListener]::new()
    if ([string]::IsNullOrWhiteSpace($Bind) -or $Bind -in @('0.0.0.0', '::', '[::]')) {
        throw 'BindAddress must be a specific Tailscale or loopback address; wildcard binding is refused.'
    }
    if (-not (Test-EpicVMBindAddress -Address $Bind)) {
        throw 'BindAddress must be a Tailscale 100.64.0.0/10 address or loopback.'
    }
    $prefixAddress = [string]$Bind
    if ($prefixAddress.Contains(':') -and -not $prefixAddress.StartsWith('[')) {
        $prefixAddress = "[$prefixAddress]"
    }
    $listener.Prefixes.Add("http://$prefixAddress`:$ListenPort/")
    try { $listener.Start() } catch { throw 'Unable to start the EpicVM agent listener. Run the installer as an administrator.' }
    Write-Verbose ("EpicVM remote agent listening on {0}:{1}" -f $Bind, $ListenPort)
    try {
        while ($listener.IsListening) {
            $context = $listener.GetContext()
            try {
                $requestId = [Guid]::NewGuid().ToString('N')
                $response = $null
                $headers = @{}
                foreach ($key in $context.Request.Headers.AllKeys) { $headers[$key] = $context.Request.Headers[$key] }
                $normalizedPath = '/' + $context.Request.Url.AbsolutePath.Trim('/')
                $isMutation = Test-EpicVMMutationRequest -Method $context.Request.HttpMethod -Path $normalizedPath
                $idempotencyKey = Get-EpicVMHeader -Headers $headers -Name 'Idempotency-Key'
                $cacheKey = if ($isMutation -and $idempotencyKey -and $idempotencyKey.Length -le 128) {
                    '{0}|{1}|{2}' -f $context.Request.HttpMethod, $normalizedPath, $idempotencyKey
                } else { '' }
                if ($cacheKey -and $State.CompletedOperations.ContainsKey($cacheKey)) {
                    $response = $State.CompletedOperations[$cacheKey]
                }
                else {
                    $body = $null
                    if ($context.Request.ContentLength64 -gt 1048576) {
                        $response = ConvertTo-EpicVMJsonResponse -StatusCode 413 -Body (New-EpicVMApiError -Code 'body_too_large' -Message 'Request body exceeds the 1 MiB limit.')
                    }
                    else {
                        if ($context.Request.HasEntityBody) {
                            $readResult = Read-EpicVMBoundedBody -Stream $context.Request.InputStream
                            if ($readResult.TooLarge) {
                                $response = ConvertTo-EpicVMJsonResponse -StatusCode 413 -Body (New-EpicVMApiError -Code 'body_too_large' -Message 'Request body exceeds the 1 MiB limit.')
                            }
                            else {
                                $body = $readResult.Body
                            }
                        }
                        if ($null -eq $response) {
                            [System.Threading.Monitor]::Enter($State.SyncRoot)
                            try {
                                $response = Invoke-EpicVMApiRequest -State $State -Method $context.Request.HttpMethod -Path $context.Request.Url.AbsolutePath -Headers $headers -Body $body
                            }
                            finally { [System.Threading.Monitor]::Exit($State.SyncRoot) }
                        }
                    }
                    if ($cacheKey -and $response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                        $State.CompletedOperations[$cacheKey] = $response
                        if ($State.CompletedOperations.Count -gt 256) {
                            $State.CompletedOperations.Remove(@($State.CompletedOperations.Keys)[0])
                        }
                    }
                }
                $response | Add-Member -MemberType NoteProperty -Name RequestId -Value $requestId -Force
                $bytes = [Text.Encoding]::UTF8.GetBytes($response.Json)
                $context.Response.StatusCode = $response.StatusCode
                $context.Response.Headers['X-Request-Id'] = $requestId
                $context.Response.ContentType = 'application/json; charset=utf-8'
                foreach ($headerName in $response.Headers.Keys) { $context.Response.Headers[$headerName] = [string]$response.Headers[$headerName] }
                $context.Response.ContentLength64 = $bytes.Length
                $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            }
            catch {
                $body = (New-EpicVMApiError -Code 'internal_error' -Message 'The agent could not process the request.') | ConvertTo-Json -Depth 10 -Compress
                $bytes = [Text.Encoding]::UTF8.GetBytes($body)
                $context.Response.StatusCode = 500
                $context.Response.Headers['X-Request-Id'] = $requestId
                $context.Response.ContentType = 'application/json; charset=utf-8'
                $context.Response.ContentLength64 = $bytes.Length
                $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            }
            finally { $context.Response.Close() }
        }
    }
    finally { $listener.Stop(); $listener.Close() }
}

if (-not $NoStart) {
    $config = Read-EpicVMConfig -Path $ConfigPath
    if ($BindAddress) { $config.BindAddress = $BindAddress }
    if ($Port -gt 0) { $config.Port = $Port }
    $token = Get-EpicVMAgentToken -Config $config
    $providerConfig = @{}
    foreach ($property in $config.PSObject.Properties) { $providerConfig[$property.Name] = $property.Value }
    switch ([string]$config.Provider) {
        'HyperV' { $provider = New-EpicVMHyperVProvider -Config $providerConfig }
        default { throw ("Unsupported provider: {0}" -f $config.Provider) }
    }
    $state = New-EpicVMAgentState -Config $config -Token $token -Provider $provider
    Start-EpicVMAgent -State $state -Bind ([string]$config.BindAddress) -ListenPort ([int]$config.Port)
}
