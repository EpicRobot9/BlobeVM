export const PROVISIONING_STATES = ['queued','cloning','booting','unclaimed','claim_in_progress','guest_setup','network_setup','streaming_setup','ready']

export function canClaimProvisioningJob(job){
  return String(job?.state || '') === 'unclaimed'
}

export function canOpenProvisionedVm(job){
  return String(job?.state || '') === 'ready'
}

export function canRetryProvisioningConsole(job){
  return String(job?.state || '') === 'setup_failed:streaming'
}

export function canOpenInventoryVm(vm){
  const state = String(vm?.provisioningState || '')
  return !state || state === 'ready'
}

export function provisioningProgress(job){
  const state = String(job?.state || 'queued')
  if(state === 'ready') return 100
  if(state.startsWith('setup_failed:')) return 75
  const index = Math.max(0, PROVISIONING_STATES.indexOf(state))
  return Math.round((index / (PROVISIONING_STATES.length - 1)) * 100)
}

export function provisioningClaimPayload({ hostId, username, password, claimToken, sunshineUsername, sunshinePassword } = {}){
  const payload = { host_id:String(hostId || ''), username:String(username || ''), password:String(password || ''), claimToken:String(claimToken || '') }
  if(sunshineUsername || sunshinePassword) { payload.sunshineUsername = String(sunshineUsername || ''); payload.sunshinePassword = String(sunshinePassword || '') }
  return payload
}

export function provisioningFailureReason(job){
  const code = String(job?.errorCode || '').trim().toLowerCase()
  const reasons = {
    bootstrap_credential_unavailable: 'The machine bootstrap channel was unavailable.',
    bootstrap_readiness_unavailable: 'The host lacks the secure guest-readiness check.',
    guest_bootstrap_not_ready: 'The cloned guest did not become ready for secure setup.',
    powershell_direct_failed: 'PowerShell Direct could not open the cloned guest.',
    rdp_verification_failed: 'Guest RDP/NLA/firewall verification failed.',
    guest_configuration_failed: 'Guest configuration failed at the secure setup gate.',
    invalid_credential_input: 'The credential input is empty or does not meet the request policy.',
    claim_in_progress: 'Another request already owns this claim.',
    claim_atomic_commit_failed: 'The claim could not be committed safely; no guest work was started.',
    guest_configuration_unavailable: 'The secure guest configuration channel is unavailable; no claim was consumed.',
    guest_account_failed: 'Windows guest-account setup failed after the claim was consumed.',
    guest_account_readiness_failed: 'The desired Windows account did not pass readiness verification.',
    bootstrap_cleanup_failed: 'Guest bootstrap cleanup did not verify.',
    bootstrap_cleanup_transport_failed: 'The guest bootstrap cleanup channel failed.',
    tailscale_enrollment_failed: 'Tailscale guest enrollment failed after guest setup.',
    streaming_setup_failed: 'Moonlight/Sunshine setup failed after guest and network setup.',
    legacy_state_uncertain: 'Persisted provisioning checkpoints are inconsistent; the VM was retained for diagnosis.',
  }
  return reasons[code] || ''
}

export function provisioningConsoleRetryPayload({ hostId, username, password, sunshineUsername, sunshinePassword } = {}){
  const payload = { host_id:String(hostId || ''), username:String(username || ''), password:String(password || '') }
  if(sunshineUsername || sunshinePassword) { payload.sunshineUsername = String(sunshineUsername || ''); payload.sunshinePassword = String(sunshinePassword || '') }
  return payload
}

export function deprovisioningPayload({ hostId, name } = {}){
  const safeName = String(name || '').trim().toLowerCase()
  return { host_id:String(hostId || ''), name:safeName, confirmName:safeName }
}
