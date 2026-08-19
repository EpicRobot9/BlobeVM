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
