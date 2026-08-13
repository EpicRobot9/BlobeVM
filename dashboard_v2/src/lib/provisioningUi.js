export const PROVISIONING_STATES = ['queued','cloning','booting','awaiting_claim','configuring_guest','enrolling_tailscale','awaiting_console','verifying','ready']

export function canClaimProvisioningJob(job){
  return String(job?.state || '') === 'awaiting_claim'
}

export function canOpenProvisionedVm(job){
  return String(job?.state || '') === 'ready'
}

export function canRetryProvisioningConsole(job){
  return String(job?.state || '') === 'console_failed'
}

export function canOpenInventoryVm(vm){
  const state = String(vm?.provisioningState || '')
  return !state || state === 'ready'
}

export function provisioningProgress(job){
  const state = String(job?.state || 'queued')
  if(state === 'ready') return 100
  if(state === 'failed' || state === 'console_failed') return 75
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
