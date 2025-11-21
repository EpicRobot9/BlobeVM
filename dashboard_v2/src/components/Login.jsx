import React, { useState } from 'react'
import { login } from '../lib/fetchWrapper'

export default function Login({onLogin}){
  const [pw,setPw] = useState('')
  const [err,setErr] = useState('')
  const [loading,setLoading] = useState(false)

  async function submit(e){
    e && e.preventDefault()
    setErr('')
    setLoading(true)
    try{
      const ok = await login(pw)
      setLoading(false)
      if(ok){ onLogin && onLogin(); return }
      setErr('Invalid password')
    }catch(e){ setErr('Network error') }
    setLoading(false)
  }

  return (
    <div className="glass-card" style={{width:420,maxWidth:'94%'}}>
      <h2 style={{marginTop:0}}>Welcome back</h2>
      <p style={{color:'var(--muted)'}}>Sign in to access the BlobeVM Dashboard.</p>
      <form onSubmit={submit} style={{display:'flex',flexDirection:'column',gap:12}}>
        <input autoFocus placeholder="Admin password" type="password" value={pw} onChange={e=>setPw(e.target.value)} style={{padding:12,borderRadius:8,border:'1px solid rgba(255,255,255,0.04)',outline:'none'}} />
        <div style={{display:'flex',gap:10,alignItems:'center'}}>
          <button className="card-elev" style={{flex:1,background:'linear-gradient(90deg,var(--blue-500),var(--blue-600))',border:'none',padding:12,borderRadius:8,color:'#fff'}} disabled={loading}>{loading? 'Signing in...':'Sign in'}</button>
        </div>
        {err && <div style={{color:'#ffb4b4'}}>{err}</div>}
      </form>
    </div>
  )
}
