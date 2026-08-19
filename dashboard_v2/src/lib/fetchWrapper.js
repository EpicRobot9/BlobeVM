const API_BASE = '/EpicVM/Dashboard/api'
const AUTH_BASE = '/EpicVM/Dashboard/api'
let csrfToken = ''
let csrfRequest = null

async function getCsrfToken(){
  if(csrfToken) return csrfToken
  if(!csrfRequest){
    csrfRequest = fetch(AUTH_BASE + '/auth/csrf', { credentials:'same-origin', cache:'no-store' })
      .then(async res => {
        const body = await res.json().catch(()=>({}))
        if(!res.ok || !body.csrfToken) throw new Error('CSRF token unavailable')
        csrfToken = String(body.csrfToken)
        return csrfToken
      })
      .finally(()=>{ csrfRequest = null })
  }
  return csrfRequest
}

export async function apiFetch(path, opts={}){
  const method = String(opts.method || 'GET').toUpperCase()
  const headers = new Headers(opts.headers || {})
  if(!['GET','HEAD','OPTIONS'].includes(method) && !headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', await getCsrfToken())
  const res = await fetch(API_BASE + path, { credentials:'same-origin', cache: opts.cache || 'no-store', ...opts, headers })
  if(res.status === 401){
    throw new Error('Unauthorized')
  }
  return res
}

export async function login(username, password){
  const res = await fetch(AUTH_BASE + '/auth/login', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username, password})})
  if(!res.ok) return false
  const j = await res.json().catch(()=>({}))
  if(j && j.ok) csrfToken = ''
  return !!(j && j.ok)
}

export async function authStatus(){
  const res = await fetch(AUTH_BASE + '/auth/status', {credentials:'same-origin'})
  const body = await res.json().catch(()=>({ok:false, authRequired:true}))
  return { httpOk: res.ok, ...body }
}

export async function logout(){
  await fetch(AUTH_BASE + '/auth/logout', {method:'POST', credentials:'same-origin'})
  csrfToken = ''
}

export default apiFetch
