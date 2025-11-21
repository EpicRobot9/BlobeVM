import React, { useEffect, useState } from 'react'
import Button from '../components/Button'

function readInt(key, fallback){
  const v = localStorage.getItem(key)
  if(!v) return fallback
  const n = parseInt(v,10)
  return Number.isFinite(n) ? n : fallback
}

export default function Settings(){
  const [updateInterval, setUpdateInterval] = useState(readInt('nbv2_update_interval', 3000))
  const [animations, setAnimations] = useState(localStorage.getItem('nbv2_animations') !== '0')
  const [cpuDelta, setCpuDelta] = useState(parseFloat(localStorage.getItem('nbv2_announce_cpu_delta') || '20'))
  const [memDelta, setMemDelta] = useState(parseFloat(localStorage.getItem('nbv2_announce_mem_delta') || '25'))
  const [cpuAbs, setCpuAbs] = useState(parseFloat(localStorage.getItem('nbv2_announce_cpu_absolute') || '85'))
  const [memAbs, setMemAbs] = useState(parseFloat(localStorage.getItem('nbv2_announce_mem_absolute') || '90'))
  const [cooldown, setCooldown] = useState(readInt('nbv2_announce_cooldown', 60000))
  const [feedback, setFeedback] = useState('')

  useEffect(()=>{
    const t = feedback && setTimeout(()=>setFeedback(''), 3000)
    return ()=>{ if(t) clearTimeout(t) }
  }, [feedback])

  function save(){
    localStorage.setItem('nbv2_update_interval', String(updateInterval))
    localStorage.setItem('nbv2_animations', animations ? '1' : '0')
    localStorage.setItem('nbv2_announce_cpu_delta', String(cpuDelta))
    localStorage.setItem('nbv2_announce_mem_delta', String(memDelta))
    localStorage.setItem('nbv2_announce_cpu_absolute', String(cpuAbs))
    localStorage.setItem('nbv2_announce_mem_absolute', String(memAbs))
    localStorage.setItem('nbv2_announce_cooldown', String(cooldown))
    setFeedback('Saved')
    // hint to consumer components to re-read (they read from localStorage on each poll)
  }

  return (
    <div>
      <h1 style={{marginTop:0}}>Settings</h1>
      <div className="glass-card">
        <div style={{display:'grid',gridTemplateColumns:'1fr 320px',gap:16,alignItems:'start'}}>
          <div>
            <div style={{fontSize:13,color:'var(--muted)',marginBottom:8}}>Update & behavior</div>
            <div style={{display:'flex',gap:8,alignItems:'center',marginBottom:8}}>
              <label style={{minWidth:160,color:'var(--muted)'}}>Stats update interval (ms)</label>
              <input type="number" value={updateInterval} onChange={e=>setUpdateInterval(Number(e.target.value||0))} style={{width:120,padding:6,borderRadius:6,border:'1px solid rgba(255,255,255,0.04)'}} />
            </div>
            <div style={{display:'flex',gap:8,alignItems:'center',marginBottom:8}}>
              <label style={{minWidth:160,color:'var(--muted)'}}>Enable animations</label>
              <input type="checkbox" checked={animations} onChange={e=>setAnimations(e.target.checked)} />
            </div>

            <div style={{marginTop:12,fontSize:13,color:'var(--muted)'}}>Announcements thresholds</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginTop:8}}>
              <div>
                <div style={{fontSize:12,color:'var(--muted)'}}>CPU delta (%)</div>
                <input type="number" value={cpuDelta} onChange={e=>setCpuDelta(Number(e.target.value||0))} style={{width:120,padding:6,borderRadius:6,border:'1px solid rgba(255,255,255,0.04)'}} />
              </div>
              <div>
                <div style={{fontSize:12,color:'var(--muted)'}}>Memory delta (%)</div>
                <input type="number" value={memDelta} onChange={e=>setMemDelta(Number(e.target.value||0))} style={{width:120,padding:6,borderRadius:6,border:'1px solid rgba(255,255,255,0.04)'}} />
              </div>
              <div>
                <div style={{fontSize:12,color:'var(--muted)'}}>CPU absolute alert (%)</div>
                <input type="number" value={cpuAbs} onChange={e=>setCpuAbs(Number(e.target.value||0))} style={{width:120,padding:6,borderRadius:6,border:'1px solid rgba(255,255,255,0.04)'}} />
              </div>
              <div>
                <div style={{fontSize:12,color:'var(--muted)'}}>Memory absolute alert (%)</div>
                <input type="number" value={memAbs} onChange={e=>setMemAbs(Number(e.target.value||0))} style={{width:120,padding:6,borderRadius:6,border:'1px solid rgba(255,255,255,0.04)'}} />
              </div>
            </div>

            <div style={{display:'flex',gap:8,alignItems:'center',marginTop:12}}>
              <label style={{minWidth:160,color:'var(--muted)'}}>Announcement cooldown (ms)</label>
              <input type="number" value={cooldown} onChange={e=>setCooldown(Number(e.target.value||0))} style={{width:140,padding:6,borderRadius:6,border:'1px solid rgba(255,255,255,0.04)'}} />
            </div>

            <div style={{marginTop:14,display:'flex',gap:8}}>
              <Button onClick={save}>Save Settings</Button>
              <Button onClick={()=>{ setUpdateInterval(3000); setAnimations(true); setCpuDelta(20); setMemDelta(25); setCpuAbs(85); setMemAbs(90); setCooldown(60000); }}>Reset</Button>
            </div>
            {feedback ? <div style={{marginTop:8,color:'var(--green)'}}>{feedback}</div> : null}
          </div>

          <div>
            <div style={{fontSize:13,color:'var(--muted)',marginBottom:8}}>Admin password</div>
            <div style={{background:'rgba(255,255,255,0.02)',padding:12,borderRadius:8,color:'var(--muted)'}}>
              The admin password for this new dashboard is stored and managed by the original dashboard. To change it, open the original dashboard settings page.
            </div>
            <div style={{marginTop:12}}>
              <a href="/dashboard/settings" target="_blank" rel="noreferrer"><Button>Open old dashboard settings</Button></a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
