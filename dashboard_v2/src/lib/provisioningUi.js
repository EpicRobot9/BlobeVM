export const PROVISIONING_STATES = ['queued','cloning','booting','awaiting_claim','configuring_guest','enrolling_tailscale','configuring_console','verifying','ready','failed']

export function canClaimProvisioningJob(job){
  return String(job?.state || '') === 'awaiting_claim'
}

export function canOpenProvisionedVm(job){
  return String(job?.state || '') === 'ready'
}

export function canOpenInventoryVm(vm){
  const state = String(vm?.provisioningState || '')
  return !state || state === 'ready'
}

export function provisioningProgress(job){
  const state = String(job?.state || 'queued')
  if(state === 'ready' || state === 'failed') return 100
  const index = Math.max(0, PROVISIONING_STATES.indexOf(state))
  return Math.round((index / (PROVISIONING_STATES.length - 1)) * 100)
}

export function provisioningClaimPayload({ hostId, username, password, claimToken } = {}){
  return { host_id:String(hostId || ''), username:String(username || ''), password:String(password || ''), claimToken:String(claimToken || '') }
}

export function deprovisioningPayload({ hostId, name } = {}){
  const safeName = String(name || '').trim().toLowerCase()
  return { host_id:String(hostId || ''), name:safeName, confirmName:safeName }
}
