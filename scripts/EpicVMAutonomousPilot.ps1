# EpicVM autonomous remote-VM orchestration primitives.
# Secrets are accepted only in memory from encrypted PSCredential files and are
# never placed in reports, logs, command-line arguments, or returned objects.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-EpicVMAutonomousError {
    param(
        [Parameter(Mandatory)][string]$Code,
        [Parameter(Mandatory)][string]$Stage,
        [Parameter(Mandatory)][string]$Message
    )
    $exception = [System.InvalidOperationException]::new($Message)
    $exception | Add-Member -MemberType NoteProperty -Name ErrorCode -Value $Code -Force
    $exception | Add-Member -MemberType NoteProperty -Name Stage -Value $Stage -Force
    return $exception
}

function Get-EpicVMAutonomousProperty {
    param([AllowNull()][object]$Object,[Parameter(Mandatory)][string]$Name,[AllowNull()][object]$Default=$null)
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}

function Get-EpicVMAutonomousCredentialBundle {
    param(
        [Parameter(Mandatory)][string]$CredentialRoot,
        [string]$GuestCredentialPath,
        [string]$SunshineCredentialPath
    )
    $guestPath = if ([string]::IsNullOrWhiteSpace($GuestCredentialPath)) { Join-Path $CredentialRoot 'guest.xml' } else { $GuestCredentialPath }
    $sunshinePath = if ([string]::IsNullOrWhiteSpace($SunshineCredentialPath)) { Join-Path $CredentialRoot 'sunshine.xml' } else { $SunshineCredentialPath }
    try {
        $guest = Import-Clixml -LiteralPath $guestPath
        $sunshine = Import-Clixml -LiteralPath $sunshinePath
    }
    catch {
        throw (New-EpicVMAutonomousError -Code 'stored_credentials_unavailable' -Stage 'credentials' -Message 'The encrypted local credential bundle could not be loaded.')
    }
    if ($guest -isnot [PSCredential] -or $sunshine -isnot [PSCredential]) {
        throw (New-EpicVMAutonomousError -Code 'stored_credentials_invalid' -Stage 'credentials' -Message 'The encrypted local credential bundle is invalid.')
    }
    try {
        $guestPassword = [System.Net.NetworkCredential]::new('', $guest.Password).Password
        $sunshinePassword = [System.Net.NetworkCredential]::new('', $sunshine.Password).Password
        if ([string]::IsNullOrWhiteSpace([string]$guest.UserName) -or [string]::IsNullOrEmpty($guestPassword) -or
            [string]::IsNullOrWhiteSpace([string]$sunshine.UserName) -or [string]::IsNullOrEmpty($sunshinePassword)) {
            throw 'empty'
        }
        return [pscustomobject]@{
            GuestUsername = [string]$guest.UserName
            GuestPassword = $guestPassword
            SunshineUsername = [string]$sunshine.UserName
            SunshinePassword = $sunshinePassword
        }
    }
    catch {
        throw (New-EpicVMAutonomousError -Code 'stored_credentials_invalid' -Stage 'credentials' -Message 'The encrypted local credential bundle is empty or invalid.')
    }
    finally {
        $guest = $null
        $sunshine = $null
    }
}

function Get-EpicVMAutonomousConfig {
    param([Parameter(Mandatory)][string]$ConfigPath)
    try {
        $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $tokenFile = [string](Get-EpicVMAutonomousProperty -Object $config -Name 'TokenFile' -Default '')
        $token = (Get-Content -LiteralPath $tokenFile -Raw -Encoding UTF8).Trim()
        $bindAddress = [string](Get-EpicVMAutonomousProperty -Object $config -Name 'BindAddress' -Default '')
        $port = [int](Get-EpicVMAutonomousProperty -Object $config -Name 'Port' -Default 0)
        if ([string]::IsNullOrWhiteSpace($token) -or [string]::IsNullOrWhiteSpace($bindAddress) -or $port -le 0) { throw 'invalid_config' }
        return [pscustomobject]@{
            BaseUrl = 'http://{0}:{1}' -f $bindAddress, $port
            Token = $token
        }
    }
    catch {
        throw (New-EpicVMAutonomousError -Code 'agent_config_unavailable' -Stage 'config' -Message 'The local EpicVM agent configuration could not be loaded.')
    }
}

function ConvertTo-EpicVMAutonomousSafeJob {
    param([AllowNull()][object]$Job)
    if ($null -eq $Job) { return $null }
    $safe = [ordered]@{}
    foreach ($name in @('id','name','profile','state','errorCode','failureStage','tailnetIp','vmId','consoleRoutePrefix','completedStages','claimConsumed','updatedAt')) {
        $value = Get-EpicVMAutonomousProperty -Object $Job -Name $name -Default $null
        if ($null -ne $value) { $safe[$name] = $value }
    }
    return $safe
}

function Invoke-EpicVMAutonomousJson {
    param(
        [Parameter(Mandatory)][ValidateSet('GET','POST')][string]$Method,
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][hashtable]$Headers,
        [AllowNull()][object]$Body,
        [Parameter(Mandatory)][int]$TimeoutSec,
        [Parameter(Mandatory)][string]$Stage
    )
    try {
        $requestBody = if ($null -eq $Body) { $null } else { $Body | ConvertTo-Json -Compress }
        $response = Invoke-WebRequest -Uri $Uri -Method $Method -Headers $Headers -ContentType 'application/json' -Body $requestBody -TimeoutSec $TimeoutSec -SkipHttpErrorCheck
        $payload = $response.Content | ConvertFrom-Json
        $status = [int]$response.StatusCode
        if ($status -lt 200 -or $status -ge 300 -or [bool](Get-EpicVMAutonomousProperty -Object $payload -Name 'ok' -Default $true) -eq $false) {
            $errorObject = Get-EpicVMAutonomousProperty -Object $payload -Name 'error' -Default $null
            $code = [string](Get-EpicVMAutonomousProperty -Object $errorObject -Name 'code' -Default 'agent_request_rejected')
            throw (New-EpicVMAutonomousError -Code $code -Stage $Stage -Message 'The EpicVM agent rejected the autonomous operation.')
        }
        return [pscustomobject]@{ StatusCode = $status; Payload = $payload }
    }
    catch [System.InvalidOperationException] {
        throw
    }
    catch {
        throw (New-EpicVMAutonomousError -Code 'agent_transport_failed' -Stage $Stage -Message 'The EpicVM agent transport failed during the autonomous operation.')
    }
}

function ConvertFrom-EpicVMAutonomousMoonlightResult {
    param([Parameter(Mandatory)][string]$Stdout)
    $lines = @($Stdout.Trim().Split([Environment]::NewLine) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -eq 0) {
        throw (New-EpicVMAutonomousError -Code 'moonlight_worker_invalid_result' -Stage 'console' -Message 'The remote Moonlight worker returned no result.')
    }
    $outer = $lines[-1] | ConvertFrom-Json
    $inner = [string](Get-EpicVMAutonomousProperty -Object $outer -Name 'out' -Default '')
    # Direct ssh returns the worker JSON itself. A few diagnostic wrappers return
    # a process envelope with the JSON in `out`; accept both shapes.
    $result = if ([string]::IsNullOrWhiteSpace($inner)) { $outer } else { $inner | ConvertFrom-Json }
    if ($null -eq $result) {
        throw (New-EpicVMAutonomousError -Code 'moonlight_worker_invalid_result' -Stage 'console' -Message 'The remote Moonlight worker returned no result.')
    }
    if (-not [bool](Get-EpicVMAutonomousProperty -Object $result -Name 'ok' -Default $false)) {
        $code = [string](Get-EpicVMAutonomousProperty -Object $result -Name 'failureCode' -Default 'moonlight_setup_failed')
        $error = New-EpicVMAutonomousError -Code $code -Stage 'console' -Message 'Moonlight setup did not pass verification.'
        $operationId = [string](Get-EpicVMAutonomousProperty -Object $result -Name 'operationId' -Default '')
        if (-not [string]::IsNullOrWhiteSpace($operationId)) { $error | Add-Member -MemberType NoteProperty -Name OperationId -Value $operationId -Force }
        throw $error
    }
    return $result
}

function Invoke-EpicVMAutonomousMoonlight {
    param(
        [Parameter(Mandatory)][string]$DashboardHost,
        [Parameter(Mandatory)][string]$DashboardContainer,
        [Parameter(Mandatory)][string]$HostId,
        [Parameter(Mandatory)][string]$JobId,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$GuestIp,
        [Parameter(Mandatory)][string]$GuestUsername,
        [Parameter(Mandatory)][string]$GuestPassword,
        [Parameter(Mandatory)][string]$SunshineUsername,
        [Parameter(Mandatory)][string]$SunshinePassword
    )
    if ($DashboardHost -notmatch '^[A-Za-z0-9_.-]+$' -or $DashboardContainer -notmatch '^[A-Za-z0-9_.-]+$') {
        throw (New-EpicVMAutonomousError -Code 'dashboard_target_invalid' -Stage 'console' -Message 'The dashboard target is invalid.')
    }
    $payload = [ordered]@{
        jobId = $JobId
        hostId = $HostId
        name = $Name
        guestIp = $GuestIp
        guestUsername = $GuestUsername
        guestPassword = $GuestPassword
        sunshineUsername = $SunshineUsername
        sunshinePassword = $SunshinePassword
    } | ConvertTo-Json -Compress
    $remoteCode = @'
import json,secrets,time,app
p=json.load(__import__('sys').stdin)
key=(str(p['hostId']),str(p['jobId']))
op=secrets.token_hex(16)
app._CONSOLE_RETRY_TASKS[key]={'operationId':op,'startedAt':time.time(),'status':'pending','failureCode':'','routeReady':False}
host=app._vm_host(p['hostId'])
orch=app._console_orchestrator()
app._start_remote_moonlight_console_retry(host=host,host_id=p['hostId'],job_id=p['jobId'],name=p['name'],guest_ip=p['guestIp'],route_name=app._remote_console_route_name(p['name'],p['hostId']),guest_username=p['guestUsername'],guest_password=p['guestPassword'],sunshine_username=p['sunshineUsername'],sunshine_password=p['sunshinePassword'],orchestrator=orch,operation_id=op)
task=app._CONSOLE_RETRY_TASKS.get(key,{})
status=host.provisioning_status(p['jobId'])
job=status.get('job') if isinstance(status,dict) else {}
print(json.dumps({'ok':task.get('status')=='ready','operationId':op,'taskStatus':task.get('status'),'failureCode':task.get('failureCode',''),'routeReady':bool(task.get('routeReady')),'job':{'state':job.get('state'),'errorCode':job.get('errorCode'),'failureStage':job.get('failureStage'),'tailnetIp':job.get('tailnetIp'),'consoleRoutePrefix':job.get('consoleRoutePrefix'),'completedStages':job.get('completedStages')}},separators=(',',':')))
'@
    $encodedCode = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteCode))
    $remoteCommand = "docker exec -i $DashboardContainer python -c `"import base64;exec(compile(base64.b64decode('$encodedCode'),'autonomous.py','exec'))`""
    $psi = [Diagnostics.ProcessStartInfo]::new('ssh')
    [void]$psi.ArgumentList.Add($DashboardHost)
    [void]$psi.ArgumentList.Add($remoteCommand)
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $psi
    try {
        [void]$process.Start()
        $process.StandardInput.Write($payload)
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEnd()
        $null = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw (New-EpicVMAutonomousError -Code 'moonlight_worker_failed' -Stage 'console' -Message 'The remote Moonlight worker failed.')
        }
        return (ConvertFrom-EpicVMAutonomousMoonlightResult -Stdout $stdout)
    }
    catch [System.InvalidOperationException] {
        throw
    }
    catch {
        throw (New-EpicVMAutonomousError -Code 'moonlight_worker_failed' -Stage 'console' -Message 'The remote Moonlight worker returned an invalid result.')
    }
    finally {
        if ($null -ne $process) { $process.Dispose() }
        $payload = $null
        $remoteCode = $null
        $encodedCode = $null
        $GuestPassword = $null
        $SunshinePassword = $null
    }
}

function Invoke-EpicVMAutonomousPilot {
    param(
        [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9-]{2,31}$')][string]$Name,
        [string]$ConfigPath = 'C:\ProgramData\EpicVM\agent\config.json',
        [string]$CredentialRoot = (Join-Path $env:LOCALAPPDATA 'EpicVM\pilot14-probe'),
        [string]$GuestCredentialPath,
        [string]$SunshineCredentialPath,
        [ValidateSet('Auto','CreateOnly')][string]$Mode = 'Auto',
        [string]$HostId = 'epic-pc',
        [string]$DashboardHost = 'kvm2',
        [string]$DashboardContainer = 'blobedash',
        [Parameter(Mandatory)][string]$ReportPath
    )
    $report = [ordered]@{
        ok = $false
        mode = $Mode
        stage = 'starting'
        errorCode = $null
        job = $null
        operationId = $null
        routeReady = $false
    }
    $config = $null
    $token = $null
    $bundle = $null
    $claimToken = $null
    try {
        $config = Get-EpicVMAutonomousConfig -ConfigPath $ConfigPath
        $token = $config.Token
        $headers = @{ Authorization = 'Bearer ' + $token; 'X-Request-Id' = 'autonomous-' + [Guid]::NewGuid().ToString('N') }
        if ($Mode -eq 'Auto') {
            $report.stage = 'credentials'
            $bundle = Get-EpicVMAutonomousCredentialBundle -CredentialRoot $CredentialRoot -GuestCredentialPath $GuestCredentialPath -SunshineCredentialPath $SunshineCredentialPath
        }
        $report.stage = 'create'
        $create = Invoke-EpicVMAutonomousJson -Method POST -Uri ($config.BaseUrl + '/v1/provisioning-jobs') -Headers $headers -Body @{ name = $Name; profile = 'standard' } -TimeoutSec 1200 -Stage 'create'
        $job = Get-EpicVMAutonomousProperty -Object $create.Payload -Name 'job' -Default $null
        $report.job = ConvertTo-EpicVMAutonomousSafeJob -Job $job
        $claimToken = [string](Get-EpicVMAutonomousProperty -Object $create.Payload -Name 'claimToken' -Default '')
        if ($Mode -eq 'CreateOnly') {
            $report.ok = $true
            $report.stage = 'create_only'
            $report.claimAvailable = -not [string]::IsNullOrWhiteSpace($claimToken)
            return [pscustomobject]$report
        }
        if ([string]::IsNullOrWhiteSpace($claimToken)) {
            $report.stage = 'claim_reissue'
            $reissue = Invoke-EpicVMAutonomousJson -Method POST -Uri ($config.BaseUrl + '/v1/provisioning-jobs/' + [Uri]::EscapeDataString([string]$job.id) + '/claim-reissue') -Headers $headers -Body @{} -TimeoutSec 180 -Stage 'claim_reissue'
            $claimToken = [string](Get-EpicVMAutonomousProperty -Object $reissue.Payload -Name 'claimToken' -Default '')
            if ([string]::IsNullOrWhiteSpace($claimToken)) { throw (New-EpicVMAutonomousError -Code 'claim_token_missing' -Stage 'claim_reissue' -Message 'The agent did not return a one-time claim.') }
        }
        $report.stage = 'claim'
        $claim = Invoke-EpicVMAutonomousJson -Method POST -Uri ($config.BaseUrl + '/v1/provisioning-jobs/' + [Uri]::EscapeDataString([string]$job.id) + '/claim') -Headers $headers -Body @{ claimToken = $claimToken; username = $bundle.GuestUsername; password = $bundle.GuestPassword } -TimeoutSec 1500 -Stage 'claim'
        $job = Get-EpicVMAutonomousProperty -Object $claim.Payload -Name 'job' -Default $job
        $report.job = ConvertTo-EpicVMAutonomousSafeJob -Job $job
        $state = [string](Get-EpicVMAutonomousProperty -Object $job -Name 'state' -Default '')
        if ($state -ne 'streaming_setup') { throw (New-EpicVMAutonomousError -Code 'console_gate_missing' -Stage 'claim' -Message 'The agent did not reach the console gate.') }
        $guestIp = [string](Get-EpicVMAutonomousProperty -Object $job -Name 'tailnetIp' -Default '')
        if ([string]::IsNullOrWhiteSpace($guestIp)) { throw (New-EpicVMAutonomousError -Code 'tailnet_ip_missing' -Stage 'claim' -Message 'The agent did not return a verified Tailscale address.') }
        $report.stage = 'console'
        $moonlight = Invoke-EpicVMAutonomousMoonlight -DashboardHost $DashboardHost -DashboardContainer $DashboardContainer -HostId $HostId -JobId ([string]$job.id) -Name ([string]$job.name) -GuestIp $guestIp -GuestUsername $bundle.GuestUsername -GuestPassword $bundle.GuestPassword -SunshineUsername $bundle.SunshineUsername -SunshinePassword $bundle.SunshinePassword
        $report.operationId = [string](Get-EpicVMAutonomousProperty -Object $moonlight -Name 'operationId' -Default '')
        $report.routeReady = [bool](Get-EpicVMAutonomousProperty -Object $moonlight -Name 'routeReady' -Default $false)
        $report.stage = 'finalize'
        $final = $null
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            $status = Invoke-EpicVMAutonomousJson -Method GET -Uri ($config.BaseUrl + '/v1/provisioning-jobs/' + [Uri]::EscapeDataString([string]$job.id)) -Headers $headers -Body $null -TimeoutSec 60 -Stage 'finalize'
            $final = Get-EpicVMAutonomousProperty -Object $status.Payload -Name 'job' -Default $null
            $state = [string](Get-EpicVMAutonomousProperty -Object $final -Name 'state' -Default '')
            if ($state -eq 'ready') { break }
            if ($state -like 'setup_failed:*') {
                $code = [string](Get-EpicVMAutonomousProperty -Object $final -Name 'errorCode' -Default 'provisioning_failed')
                throw (New-EpicVMAutonomousError -Code $code -Stage 'finalize' -Message 'The autonomous VM did not reach ready state.')
            }
            Start-Sleep -Seconds 5
        }
        $report.job = ConvertTo-EpicVMAutonomousSafeJob -Job $final
        if ([string](Get-EpicVMAutonomousProperty -Object $final -Name 'state' -Default '') -ne 'ready') { throw (New-EpicVMAutonomousError -Code 'ready_timeout' -Stage 'finalize' -Message 'The autonomous VM did not reach ready state before the deadline.') }
        $report.ok = $true
        $report.stage = 'ready'
        return [pscustomobject]$report
    }
    catch {
        $report.ok = $false
        $report.errorCode = [string](Get-EpicVMAutonomousProperty -Object $_.Exception -Name 'ErrorCode' -Default 'autonomous_flow_failed')
        $report.stage = [string](Get-EpicVMAutonomousProperty -Object $_.Exception -Name 'Stage' -Default $report.stage)
        $operationId = [string](Get-EpicVMAutonomousProperty -Object $_.Exception -Name 'OperationId' -Default '')
        if (-not [string]::IsNullOrWhiteSpace($operationId)) { $report.operationId = $operationId }
        return [pscustomobject]$report
    }
    finally {
        $config = $null
        $token = $null
        $bundle = $null
        $claimToken = $null
        $headers = $null
        try { $report | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $ReportPath -Encoding UTF8 } catch { }
    }
}
