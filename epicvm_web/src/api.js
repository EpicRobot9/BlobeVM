// EpicVM web API client. All endpoints are same-origin under /EpicVM/api or /portal/api.
const PORTAL = '/portal'
const EPICVM = '/EpicVM/api'

async function apiFetch(path, opts = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  let body = {}
  try { body = await res.json() } catch { /* ignore */ }
  return { ok: res.ok, status: res.status, body }
}

export async function signup({ username, password, displayName, email, who }) {
  return apiFetch(`${EPICVM}/signup`, {
    method: 'POST',
    body: JSON.stringify({ username, password, displayName, email, who }),
  })
}

export async function login(username, password) {
  const res = await apiFetch(`${PORTAL}/api/auth/login`, {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  return res
}

export async function logout() {
  return apiFetch(`${PORTAL}/api/auth/logout`, { method: 'POST' })
}

export async function me() {
  const res = await apiFetch(`${EPICVM}/me`)
  return res.body && res.body.authenticated ? res.body : null
}

// Portal VM list for an approved user (used by the portal-redirect status view).
export async function myVms() {
  return apiFetch(`${PORTAL}/api/vms`)
}

export async function startVm(name) {
  return apiFetch(`${PORTAL}/api/start/${encodeURIComponent(name)}`, { method: 'POST' })
}
export async function stopVm(name) {
  return apiFetch(`${PORTAL}/api/stop/${encodeURIComponent(name)}`, { method: 'POST' })
}
export async function restartVm(name) {
  // No dedicated backend endpoint; restart = stop, wait, start.
  const s = await stopVm(name)
  if (!s.ok) return s
  await new Promise((r) => setTimeout(r, 2500))
  return startVm(name)
}
