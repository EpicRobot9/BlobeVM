const MIN_VISIBLE_DELAY_MS = 800
const DEFAULT_HIDDEN_DELAY_MS = 60_000

export function instanceNamesKey(instances = []) {
  return [...new Set(
    instances
      .map(instance => {
        const name = String(instance?.name ?? '')
        if(!name) return ''
        const hostId = String(instance?.host_id ?? 'local')
        return `${hostId}:${name}`
      })
      .filter(Boolean)
  )].sort().join('\u0000')
}

export function pollDelayMs({ visible = true, intervalMs = 3000, hiddenDelayMs = DEFAULT_HIDDEN_DELAY_MS } = {}) {
  if (!visible) return Math.max(0, Number(hiddenDelayMs) || DEFAULT_HIDDEN_DELAY_MS)
  return Math.max(MIN_VISIBLE_DELAY_MS, Number(intervalMs) || 3000)
}
