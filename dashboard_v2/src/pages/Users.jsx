import React, { useEffect, useMemo, useState } from 'react'
import apiFetch from '../lib/fetchWrapper'
import Button from '../components/Button'

export default function Users(){
  const [users, setUsers] = useState([])
  const [vms, setVms] = useState([])
  const [requests, setRequests] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [form, setForm] = useState({ username:'', password:'', assignedVms:[] })

  async function load(){
    setLoading(true)
    try{
      const r = await apiFetch('/users')
      const j = await r.json().catch(()=>({}))
      setUsers(j.users || [])
      setVms(j.vms || [])
      setRequests(j.requests || [])
    }catch(e){
      alert(String(e))
    }
    setLoading(false)
  }

  useEffect(()=>{ load() },[])

  async function createUser(e){
    e?.preventDefault?.()
    setBusy('create')
    try{
      const r = await apiFetch('/users', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(form) })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed creating user')
      setForm({ username:'', password:'', assignedVms:[] })
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  async function saveUser(user){
    setBusy('save:'+user.username)
    try{
      const r = await apiFetch(`/users/${encodeURIComponent(user.username)}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ assignedVms: user.assignedVms || [], disabled: !!user.disabled }) })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed saving user')
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  async function resetPassword(user){
    const password = window.prompt(`Set a new password for ${user.username}`)
    if(!password) return
    setBusy('pw:'+user.username)
    try{
      const r = await apiFetch(`/users/${encodeURIComponent(user.username)}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ password }) })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed resetting password')
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  async function deleteUser(user){
    if(window.prompt(`Delete ${user.username}? Type DELETE to confirm.`) !== 'DELETE') return
    setBusy('delete:'+user.username)
    try{
      const r = await apiFetch(`/users/${encodeURIComponent(user.username)}/delete`, { method:'POST' })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed deleting user')
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  async function handleRequestAction(req, action){
    setBusy(`request:${req.id}:${action}`)
    try{
      const r = await apiFetch(`/access-requests/${req.id}/action`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ action })
      })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || `Failed to ${action} request`)
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  async function approveAccount(user){
    if(window.prompt(`Approve ${user.username} and auto-provision their Linux VM? Type APPROVE to confirm.`) !== 'APPROVE') return
    setBusy('approve:'+user.username)
    try{
      const r = await apiFetch(`/accounts/${encodeURIComponent(user.username)}/approve`, { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}' })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed to approve account')
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  async function rejectAccount(user){
    if(window.prompt(`Reject ${user.username}? Type REJECT to confirm.`) !== 'REJECT') return
    setBusy('reject:'+user.username)
    try{
      const r = await apiFetch(`/accounts/${encodeURIComponent(user.username)}/reject`, { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}' })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed to reject account')
      await load()
    }catch(e){ alert(String(e)) }
    setBusy('')
  }

  const vmNames = useMemo(()=>vms.map(v=>v.name), [vms])

  return (
    <div>
      <h1 style={{marginTop:0}}>Users & VM Access</h1>
      <div className="glass-card" style={{marginBottom:16}}>
        <div style={{color:'var(--muted)', marginBottom:14}}>Create VM users, assign them to one or more VMs, and manage access requests from the new user portal.</div>
        <form onSubmit={createUser} style={{display:'grid', gap:12}}>
          <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))', gap:12}}>
            <input value={form.username} onChange={e=>setForm(s=>({...s, username:e.target.value}))} placeholder="username" style={fieldStyle} />
            <input value={form.password} onChange={e=>setForm(s=>({...s, password:e.target.value}))} placeholder="password" type="password" style={fieldStyle} />
          </div>
          <label style={{display:'grid', gap:8}}>
            <span>Assigned VMs</span>
            <select multiple value={form.assignedVms} onChange={e=>setForm(s=>({...s, assignedVms:Array.from(e.target.selectedOptions).map(o=>o.value)}))} style={{...fieldStyle, minHeight:140}}>
              {vmNames.map(name => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <div><Button type="submit" disabled={busy==='create'}>{busy==='create' ? 'Creating…' : 'Create user'}</Button></div>
        </form>
      </div>

      <div className="glass-card" style={{marginBottom:16}}>
        <h2 style={{marginTop:0}}>Users</h2>
        {loading ? <div>Loading…</div> : users.length === 0 ? <div style={{color:'var(--muted)'}}>No users yet.</div> : (
          <div style={{display:'grid', gap:14}}>
            {users.map(user => (
              <div key={user.username} style={{border:'1px solid rgba(255,255,255,.08)', borderRadius:16, padding:16}}>
                <div style={{display:'flex', justifyContent:'space-between', gap:12, flexWrap:'wrap', alignItems:'center'}}>
                  <div>
                    <div style={{fontWeight:700}}>{user.username}</div>
                    <div style={{color:'var(--muted)', fontSize:13}}>{(user.assignedVms || []).length} assigned VM(s)</div>
                    <div style={{color:'var(--muted)', fontSize:13, marginTop:4}}>
                      Account: <span style={{fontWeight:700, color:(user.accountStatus==='approved'?'#36d399':user.accountStatus==='rejected'?'#ff7a8a':'#f7c948')}}>{user.accountStatus || 'pending'}</span>
                      {user.provisioningState ? ` · VM: ${user.provisioningState}` : ''}
                    </div>
                  </div>
                  <label style={{display:'flex', alignItems:'center', gap:8, color:'var(--muted)'}}>
                    <input type="checkbox" checked={!!user.disabled} onChange={e=>setUsers(items => items.map(it => it.username === user.username ? { ...it, disabled:e.target.checked } : it))} />
                    Disabled
                  </label>
                </div>
                <div style={{marginTop:12}}>
                  <select multiple value={user.assignedVms || []} onChange={e=>setUsers(items => items.map(it => it.username === user.username ? { ...it, assignedVms:Array.from(e.target.selectedOptions).map(o=>o.value) } : it))} style={{...fieldStyle, minHeight:140, width:'100%'}}>
                    {vmNames.map(name => <option key={name} value={name}>{name}</option>)}
                  </select>
                </div>
                <div style={{display:'flex', gap:10, flexWrap:'wrap', marginTop:12}}>
                  <Button onClick={()=>saveUser(user)} disabled={busy === 'save:'+user.username}>{busy === 'save:'+user.username ? 'Saving…' : 'Save access'}</Button>
                  <Button onClick={()=>resetPassword(user)} disabled={busy === 'pw:'+user.username}>Reset password</Button>
                  <Button onClick={()=>deleteUser(user)} disabled={busy === 'delete:'+user.username} style={{background:'linear-gradient(135deg,#ef4444,#b91c1c)', color:'#fff'}}>Delete user</Button>
                  {user.accountStatus !== 'approved' ? (
                    <Button onClick={()=>approveAccount(user)} disabled={busy === 'approve:'+user.username} style={{background:'linear-gradient(135deg,#10b981,#047857)', color:'#fff'}}>{busy === 'approve:'+user.username ? 'Approving…' : 'Approve + provision VM'}</Button>
                  ) : null}
                  {user.accountStatus !== 'rejected' && user.accountStatus !== 'approved' ? (
                    <Button onClick={()=>rejectAccount(user)} disabled={busy === 'reject:'+user.username} style={{background:'linear-gradient(135deg,#f59e0b,#b45309)', color:'#fff'}}>{busy === 'reject:'+user.username ? 'Rejecting…' : 'Reject'}</Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="glass-card">
        <h2 style={{marginTop:0}}>Access requests</h2>
        {requests.length === 0 ? <div style={{color:'var(--muted)'}}>No requests yet.</div> : (
          <div style={{display:'grid', gap:10}}>
            {requests.map(req => {
              const requestBusyPrefix = `request:${req.id}:`
              return (
                <div key={req.id} style={{border:'1px solid rgba(255,255,255,.08)', borderRadius:14, padding:14}}>
                  <div style={{display:'flex', justifyContent:'space-between', gap:12, flexWrap:'wrap', alignItems:'center'}}>
                    <div>
                      <strong>{req.username}</strong> requested <strong>{req.vm_name}</strong>
                      <div style={{color:'var(--muted)', marginTop:6}}>Status: {req.status || 'pending'}</div>
                    </div>
                  </div>
                  <div style={{color:'var(--muted)', marginTop:6}}>{req.note || 'No note provided.'}</div>
                  {req.status === 'pending' ? (
                    <div style={{display:'flex', gap:10, flexWrap:'wrap', marginTop:12}}>
                      <Button onClick={()=>handleRequestAction(req, 'approve')} disabled={busy === requestBusyPrefix + 'approve'}>{busy === requestBusyPrefix + 'approve' ? 'Approving…' : 'Approve & grant access'}</Button>
                      <Button onClick={()=>handleRequestAction(req, 'deny')} disabled={busy === requestBusyPrefix + 'deny'} style={{background:'linear-gradient(135deg,#f59e0b,#b45309)', color:'#fff'}}>{busy === requestBusyPrefix + 'deny' ? 'Denying…' : 'Deny'}</Button>
                      <Button onClick={()=>handleRequestAction(req, 'dismiss')} disabled={busy === requestBusyPrefix + 'dismiss'} style={{background:'linear-gradient(135deg,#475569,#334155)', color:'#fff'}}>{busy === requestBusyPrefix + 'dismiss' ? 'Dismissing…' : 'Dismiss'}</Button>
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

const fieldStyle = {
  background:'rgba(2,6,23,.7)',
  color:'#fff',
  border:'1px solid rgba(255,255,255,.12)',
  borderRadius:12,
  padding:'12px 14px'
}
