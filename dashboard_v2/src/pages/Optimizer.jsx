import React, { useEffect, useState } from 'react'
import apiFetch from '../lib/fetchWrapper'
import Button from '../components/Button'

export default function Optimizer(){
  const [status, setStatus] = useState(null)
  const [logs, setLogs] = useState('')
  const [running, setRunning] = useState(false)

  async function loadStatus(){
    try{
      const r = await apiFetch('/optimizer/status')
      const j = await r.json().catch(()=>({}))
      setStatus(j)
    }catch(e){ console.error('optimizer status', e) }
  }

  async function runOnce(){
    try{
      setRunning(true)
      const r = await apiFetch('/optimizer/run-once', {method:'POST'})
      if(r.ok) alert('Optimizer run started')
    }catch(e){ console.error('run once', e) }
    setRunning(false)
    loadStatus()
  }

  async function tailLogs(){
    try{
      const r = await apiFetch('/optimizer/logs')
      const j = await r.json().catch(()=>({logs:''}))
      setLogs(j.logs || '')
    }catch(e){ setLogs('Error: '+String(e)) }
  }

  useEffect(()=>{ loadStatus() }, [])

  return (
    <div>
      <h1 style={{marginTop:0}}>Optimizer Control</h1>
      <div className="glass-card">
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div>
            <div style={{fontSize:13,color:'var(--muted)'}}>Status</div>
            <div style={{fontSize:18,fontWeight:700}}>{status ? (status.enabled ? 'Enabled' : 'Disabled') : '—'}</div>
          </div>
          <div style={{display:'flex',gap:8}}>
            <Button onClick={runOnce} disabled={running}>{running ? 'Running…' : 'Run once'}</Button>
            <Button onClick={tailLogs}>Fetch Logs</Button>
          </div>
        </div>
        <div style={{marginTop:12}}>
          <div style={{fontSize:13,color:'var(--muted)'}}>Recent logs</div>
          <div style={{background:'#02040a',color:'#dff',padding:8,borderRadius:8,height:260,overflow:'auto',fontFamily:'monospace',fontSize:12}}>
            <pre style={{whiteSpace:'pre-wrap',margin:0}}>{logs}</pre>
          </div>
        </div>
      </div>
    </div>
  )
}
