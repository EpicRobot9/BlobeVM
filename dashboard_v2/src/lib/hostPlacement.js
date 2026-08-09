const LOCAL_HOST_ID = 'local'

function hostIdentifier(host){
  return host?.id || host?.host_id || ''
}

function hostDisplayName(host){
  return host?.display_name || host?.displayName || host?.name || hostIdentifier(host) || 'Unnamed host'
}

function hostResourceValue(resources, ...keys){
  for(const key of keys){
    if(resources?.[key] !== undefined && resources?.[key] !== null && resources?.[key] !== '') return resources[key]
  }
  return null
}

function formatBytes(value){
  const bytes = Number(value)
  if(!Number.isFinite(bytes) || bytes < 0) return null
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = bytes
  let index = 0
  while(amount >= 1024 && index < units.length - 1){
    amount /= 1024
    index += 1
  }
  const rounded = amount >= 10 || index === 0 ? Math.round(amount) : amount.toFixed(1)
  return `${rounded} ${units[index]}`
}

function hostRecords(payload){
  if(Array.isArray(payload)) return payload
  if(Array.isArray(payload?.hosts)) return payload.hosts
  if(Array.isArray(payload?.data?.hosts)) return payload.data.hosts
  return []
}

export function normalizeHostInventory(payload){
  return hostRecords(payload).filter(Boolean).map(host => ({
    ...host,
    id: hostIdentifier(host),
    display_name: hostDisplayName(host)
  }))
}

export function getEligibleRemoteHosts(hosts){
  return normalizeHostInventory(hosts).filter(host => (
    host.id &&
    host.id !== LOCAL_HOST_ID &&
    String(host.kind || 'remote').toLowerCase() !== 'local' &&
    host.online === true &&
    host.capabilities?.create_vm === true &&
    host.capabilities?.provisioning === true
  ))
}

export function canUseRemotePlacement(hosts){
  return getEligibleRemoteHosts(hosts).length > 0
}

export function remotePlacementDisabledReason(hosts){
  return canUseRemotePlacement(hosts) ? '' : 'No remote hosts connected'
}

export function getPlacementValidationReason({ placement = 'local', hostId = LOCAL_HOST_ID, hosts = [] } = {}){
  if(placement !== 'remote') return ''
  if(!hostId) return 'Select a remote host'
  return getEligibleRemoteHosts(hosts).some(host => host.id === hostId)
    ? ''
    : 'Selected remote host is no longer available'
}

export function createPlacementPayload({ name = '', placement = 'local', hostId = LOCAL_HOST_ID } = {}){
  const selectedPlacement = placement === 'remote' ? 'remote' : 'local'
  return {
    name: String(name).trim().toLowerCase(),
    placement: selectedPlacement,
    host_id: selectedPlacement === 'remote' ? String(hostId || '') : LOCAL_HOST_ID
  }
}

export function hostOptionLabel(host){
  const resources = host?.resources || {}
  const resourceParts = [
    ['RAM', hostResourceValue(resources, 'memory_available_bytes', 'memoryAvailableBytes', 'memory_available')],
    ['disk', hostResourceValue(resources, 'storage_free_bytes', 'storageFreeBytes', 'storage_free')]
  ]
    .map(([label, value]) => {
      const formatted = formatBytes(value)
      return formatted ? `${label} ${formatted} free` : ''
    })
    .filter(Boolean)

  const resourceSummary = resourceParts.length ? resourceParts.join(' · ') : 'Resources unavailable'
  const platform = host?.platform || host?.os || 'unknown platform'
  const provider = host?.provider || 'unknown provider'
  return `${hostDisplayName(host)} — ${platform} / ${provider} · ${resourceSummary}`
}

export { LOCAL_HOST_ID }
