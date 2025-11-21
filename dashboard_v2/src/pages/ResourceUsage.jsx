import React, { useEffect, useState } from 'react'
import apiFetch from '../lib/fetchWrapper'

export default function ResourceUsage(){
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(()=>{
    let stopped = false
    async function tick(){
      try{
        const r = await apiFetch('/stats')
        const j = await r.json().catch(()=>null)
        if(!stopped) setStats(j)
      }catch(e){ console.error('load stats', e) }
      setLoading(false)
      const iv = parseInt(localStorage.getItem('nbv2_update_interval')||'3000',10)
      await new Promise(r=>setTimeout(r, Math.max(1000, iv)))
      if(!stopped) tick()
    }
    tick()
    return ()=>{ stopped=true }
  }, [])

  return (
    <div>
      <h1 style={{marginTop:0}}>Resource Usage</h1>
      <div className="glass-card">
        {loading ? <div className="skeleton" style={{height:160}} /> : (
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
            <div>
              <div style={{fontSize:12,color:'var(--muted)'}}>CPU</div>
              <div style={{fontSize:20,fontWeight:700}}>{stats && stats.cpu ? `${stats.cpu.usage}%` : '—'}</div>
              <div style={{fontSize:12,color:'var(--muted)'}}>Load: {stats && stats.loadavg ? stats.loadavg.join(', ') : '—'}</div>
            </div>
            <div>
              <div style={{fontSize:12,color:'var(--muted)'}}>Memory</div>
              <div style={{fontSize:20,fontWeight:700}}>{stats && stats.memory ? `${stats.memory.percent}% (${(stats.memory.used || 0)} / ${(stats.memory.total || 0)} MB)` : '—'}</div>
              <div style={{fontSize:12,color:'var(--muted)'}}>Swap: {stats && stats.swap ? `${stats.swap.used || 0} MB` : '—'}</div>
            </div>
            <div style={{gridColumn:'1 / -1',marginTop:8}}>
              <div style={{fontSize:12,color:'var(--muted)'}}>Disk</div>
              <div style={{marginTop:6}}>
                {stats && stats.disk ? Object.entries(stats.disk).map(([k,v])=> (
                  <div key={k} style={{display:'flex',justifyContent:'space-between',padding:'6px 0',borderBottom:'1px dashed rgba(255,255,255,0.02)'}}>
                    <div style={{color:'var(--muted)'}}>{k}</div>
                    <div>{v.used} / {v.total} MB ({v.percent}%)</div>
                  </div>
                )) : <div style={{color:'var(--muted)'}}>—</div>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
