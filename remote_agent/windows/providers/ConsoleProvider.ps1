#Requires -Version 7.0
<# Boundary between the Windows agent and the source-only kvm2 orchestrator. #>

Set-StrictMode -Version Latest

function Invoke-EpicVMConsoleConfiguration {
    param(
        [Parameter(Mandatory)][object]$Provider,
        [Parameter(Mandatory)][string]$VmName,
        [Parameter(Mandatory)][string]$Username,
        [Parameter(Mandatory)][string]$Password,
        [Parameter(Mandatory)][string]$GuestIp,
        [AllowNull()][string]$DeviceId
    )
    $invoker=Get-EpicVMHyperVValue -Object $Provider -Name 'ConsoleOrchestratorInvoker' -Default $null
    if($null -eq $invoker){throw (New-EpicVMHyperVError -Code 'ConsoleUnavailable' -Message 'The kvm2 console orchestrator is not configured.')}
    try {
        $result=& $invoker $VmName $GuestIp $Username $Password $DeviceId
        $ok=[bool](Get-EpicVMHyperVValue -Object $result -Name 'ok' -Default $false)
        if(-not $ok){throw 'Console plan was not accepted.'}
        return [ordered]@{ok=$true;routePrefix=[string](Get-EpicVMHyperVValue -Object $result -Name 'routePrefix' -Default ('/vm/' + $VmName + '/'));guestTcpVerified=$true}
    } catch { throw (New-EpicVMHyperVError -Code 'ConsoleConfigurationFailed' -Message 'The kvm2 console configuration failed.') }
    finally {$Password=$null}
}

function Remove-EpicVMConsoleConfiguration {
    param([Parameter(Mandatory)][object]$Provider,[Parameter(Mandatory)][string]$VmName,[Parameter(Mandatory)][string]$ConfirmName,[AllowNull()][string]$DeviceId)
    if($VmName -cne $ConfirmName){throw (New-EpicVMHyperVError -Code 'ConfirmationRequired' -Message 'Exact VM name confirmation is required.')}
    $invoker=Get-EpicVMHyperVValue -Object $Provider -Name 'ConsoleTeardownInvoker' -Default $null
    if($null -eq $invoker){throw (New-EpicVMHyperVError -Code 'ConsoleUnavailable' -Message 'The kvm2 console orchestrator is not configured.')}
    try { return & $invoker $VmName $ConfirmName $DeviceId } catch { throw (New-EpicVMHyperVError -Code 'ConsoleTeardownFailed' -Message 'The kvm2 console teardown failed.') }
}
