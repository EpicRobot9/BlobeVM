const STATE_ALIASES = {
  running: 'Running',
  on: 'Running',
  poweredon: 'Running',
  started: 'Running',
  off: 'Off',
  stopped: 'Off',
  poweredoff: 'Off',
  paused: 'Paused',
  saved: 'Saved',
  starting: 'Starting',
  stopping: 'Stopping',
  unknown: 'Unknown'
}

const PROVIDER_HEALTH_STATES = new Set(['operatingnormally', 'healthy', 'ok', 'normal'])

function stateKey(value){
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

function canonicalState(value){
  const text = String(value || '').trim()
  if(!text) return 'Unknown'
  return STATE_ALIASES[stateKey(text)] || text
}

export function normalizeVmStatus(vm = {}){
  const item = { ...vm }
  const rawState = item.state ?? item.State
  const rawStatus = item.status ?? item.Status
  const providerStatus = item.provider_status ?? item.providerStatus ?? rawStatus
  let sourceState = rawState

  if(!String(sourceState || '').trim()){
    const fallback = String(rawStatus || '').trim()
    sourceState = PROVIDER_HEALTH_STATES.has(stateKey(fallback)) ? 'Unknown' : fallback
  }

  const state = canonicalState(sourceState)
  item.state = state
  item.status = state
  if(String(providerStatus || '').trim()) item.provider_status = String(providerStatus)
  item.running = state.toLowerCase() === 'running'
  if(item.exists === undefined) item.exists = true
  return item
}
