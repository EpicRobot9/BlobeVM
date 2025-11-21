const TOKEN_KEY = 'nbv2_token'

export function setToken(t){
  if(!t){ localStorage.removeItem(TOKEN_KEY); return }
  localStorage.setItem(TOKEN_KEY, t)
}
export function getToken(){ return localStorage.getItem(TOKEN_KEY) }
export function isAuthenticated(){ return !!getToken() }
