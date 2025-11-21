import React, { useEffect, useState, useRef } from 'react'
import Button from '../components/Button'
import apiFetch from '../lib/fetchWrapper'
import Modal from '../components/Modal'
import VmExec from '../components/VmExec'
import { useToasts } from '../components/ToastProvider'

function StatusBadge({status}){
  const s = (status||'').toLowerCase()
  const color = s.includes('up') || s.includes('running') || s.includes('healthy') ? '#10b981' : (s.includes('rebuild') || s.includes('update') ? '#f59e0b' : '#ef4444')
  return <div style={{display:'inline-flex',alignItems:'center',gap:8}}><span style={{width:10,height:10,background:color,borderRadius:8,display:'inline-block'}}></span><span style={{color:'var(--muted)'}}>{status}</span></div>
}

export default function VMManager(){
  const { addToast } = useToasts()
  const [instances, setInstances] = useState([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(null)
  const [logs, setLogs] = useState('')
  const [logLoading, setLogLoading] = useState(false)
  const [announcement, setAnnouncement] = useState('')
  const prevStatsRef = useRef({})
  const lastAnnounceRef = useRef({})

  async function load(){
    setLoading(true)
    try{
      const [rList, rStats] = await Promise.all([apiFetch('/list'), apiFetch('/vm/stats').catch(()=>({ok:false}))])
      const j = await rList.json().catch(()=>({instances:[]}))
      const statJ = rStats && rStats.ok ? await rStats.json().catch(()=>({vms:{}})) : (rStats && typeof rStats.json === 'function' ? await rStats.json().catch(()=>({vms:{}})) : {vms:{}})
      const statsMap = (statJ && statJ.vms) ? statJ.vms : {}
        // The above mapping falls back to matching by VM name; ensure CPU/mem props exist
        const insts = (j.instances || []).map(it => ({...it, _stats: statsMap[it.name] || statsMap[''+it.name] || statsMap[it.name]}))

        // Detect significant changes (announce via aria-live)
        try{
            const prev = prevStatsRef.current || {}
            const now = Date.now()
            // read adjustable thresholds from localStorage (fallbacks kept sensible)
            const cpuThresholdDelta = parseFloat(localStorage.getItem('nbv2_announce_cpu_delta') || '20')
            const memThresholdDelta = parseFloat(localStorage.getItem('nbv2_announce_mem_delta') || '25')
            const cpuAbsolute = parseFloat(localStorage.getItem('nbv2_announce_cpu_absolute') || '85')
            const memAbsolute = parseFloat(localStorage.getItem('nbv2_announce_mem_absolute') || '90')
            const announceCooldownMs = parseInt(localStorage.getItem('nbv2_announce_cooldown') || String(60*1000), 10)

            for(const [vm, s] of Object.entries(statsMap || {})){
              const cpu = (s && typeof s.cpu_percent === 'number') ? s.cpu_percent : null
              const mem = (s && typeof s.mem_percent === 'number') ? s.mem_percent : null
              const p = prev[vm] || {}
              const prevCpu = (p && typeof p.cpu_percent === 'number') ? p.cpu_percent : undefined
              const prevMem = (p && typeof p.mem_percent === 'number') ? p.mem_percent : undefined
              const lastAnn = lastAnnounceRef.current[vm] || 0

              if(prevCpu !== undefined){
                if(cpu !== null){
                  if(((cpu - prevCpu) >= cpuThresholdDelta && cpu >= 30) || (cpu >= cpuAbsolute && prevCpu < cpuAbsolute)){
                    if(now - lastAnn > announceCooldownMs){
                      const msg = `Alert: VM ${vm} CPU ${cpu}% (was ${prevCpu}%)`
                      setAnnouncement(msg)
                      addToast({title:`VM ${vm} CPU`, message: `${cpu}% (was ${prevCpu}%)`, type:'warn', timeout:8000})
                      lastAnnounceRef.current[vm] = now
                      setTimeout(()=>{ setAnnouncement('') }, 8000)
                    }
                  }
                }
              }
              if(prevMem !== undefined){
                if(mem !== null){
                  if(((mem - prevMem) >= memThresholdDelta && mem >= 40) || (mem >= memAbsolute && prevMem < memAbsolute)){
                    if(now - lastAnn > announceCooldownMs){
                      const msg = `Alert: VM ${vm} memory ${mem}% (was ${prevMem}%)`
                      setAnnouncement(msg)
                      addToast({title:`VM ${vm} Memory`, message: `${mem}% (was ${prevMem}%)`, type:'warn', timeout:8000})
                      lastAnnounceRef.current[vm] = now
                      setTimeout(()=>{ setAnnouncement('') }, 8000)
                    }
                  }
                }
              }
            }
        }catch(e){ /* no-op */ }

        // update prev snapshot
        prevStatsRef.current = statsMap || {}
        setInstances(insts)
    }catch(e){ console.error('load instances', e) }
    setLoading(false)
  }

  useEffect(()=>{
    let stopped = false
    async function tick(){
      if(stopped) return
      await load()
      const ivMs = parseInt(localStorage.getItem('nbv2_update_interval') || '3000', 10)
      await new Promise(r=>setTimeout(r, Math.max(800, ivMs)))
      if(!stopped) tick()
    }
    tick()
    return ()=>{ stopped=true }
  }, [])

  async function action(cmd, name){
    try{
      await apiFetch(`/${cmd}/${encodeURIComponent(name)}`, {method:'POST'})
    }catch(e){ console.error('action error', e) }
    // refresh list after short delay
    setTimeout(load, 800)
  }

  async function openDetails(name){
    setSelected(name)
    await fetchLogs(name)
  }

  async function fetchLogs(name){
    setLogLoading(true)
    try{
      const r = await apiFetch(`/vm/logs/${encodeURIComponent(name)}`)
      const j = await r.json().catch(()=>({ok:false, logs:''}))
      setLogs(j.logs || j.logs === '' ? (j.logs||'') : (j.error||''))
    }catch(e){ setLogs('Error loading logs: '+String(e)) }
    setLogLoading(false)
  }

  useEffect(()=>{
    let iv
    if(selected){
      iv = setInterval(()=>fetchLogs(selected), 2500)
    }
    return ()=>{ if(iv) clearInterval(iv) }
  }, [selected])

  return (
    <div>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
      <h1 style={{marginTop:0}}>VM Manager</h1>
      <div className="glass-card">
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div style={{color:'var(--muted)'}}>Manage your VMs — start/stop/restart and view details.</div>
          <div>
            <Button onClick={load} style={{marginLeft:8}}>Refresh</Button>
          </div>
        </div>
        <div style={{marginTop:12}}>
          {loading ? <div className="skeleton" style={{height:200}} /> : (
            <table style={{width:'100%',borderCollapse:'collapse'}}>
              <thead><tr style={{textAlign:'left'}}><th>Name</th><th>Status</th><th>Port</th><th>URL</th><th>Actions</th></tr></thead>
              <tbody>
                {instances.map(it=> (
                  <tr key={it.name} style={{borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                    <td style={{padding:'8px 6px'}}>{it.name}</td>
                    <td style={{padding:'8px 6px'}}><StatusBadge status={it.status||''} /></td>
                      <td style={{padding:'8px 6px'}}>{it.port || ''}</td>
                      <td style={{padding:'8px 6px',width:260}}>
                        {it._stats ? (
                          <div style={{display:'flex',flexDirection:'column',gap:8}}>
                            <div className="stat-row">
                              <div className="stat-bar" aria-hidden="true">
                                <div className="stat-fill" style={{width: `${Math.min(100, it._stats.cpu_percent || 0)}%`}} />
                                <div className="tooltip">CPU: {it._stats.cpu_percent}%</div>
                              </div>
                              <div className="stat-label">{it._stats.cpu_percent}%</div>
                            </div>
                            <div className="stat-row">
                              <div className="stat-bar" aria-hidden="true">
                                <div className="stat-fill ram" style={{width: `${Math.min(100, it._stats.mem_percent || 0)}%`}} />
                                <div className="tooltip">RAM: {it._stats.mem_percent}%</div>
                              </div>
                              <div className="stat-label">{it._stats.mem_percent}%</div>
                            </div>
                          </div>
                        ) : <div style={{color:'var(--muted)'}}>—</div>}
                      </td>
                    <td style={{padding:'8px 6px'}}><a href={it.url} target="_blank" rel="noreferrer" style={{color:'var(--blue-500)'}}>{it.url}</a></td>
                    <td style={{padding:'8px 6px',display:'flex',gap:8}}>
                      <Button onClick={()=>action('start', it.name)}>Start</Button>
                      <Button onClick={()=>action('stop', it.name)}>Stop</Button>
                      <Button onClick={()=>action('restart', it.name)}>Restart</Button>
                      <Button onClick={()=>openDetails(it.name)}>Details</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <Modal open={!!selected} title={`VM: ${selected}`} onClose={()=>setSelected(null)} width={1100}>
        <div style={{display:'flex',gap:12}}>
          <div style={{flex:1}}>
            <iframe src={`/dashboard/vm/${encodeURIComponent(selected)}/`} style={{width:'100%',height:320,border:'1px solid rgba(255,255,255,0.04)'}} />
            <div style={{marginTop:12}}>
              <VmExec vmName={selected} />
            </div>
          </div>
          <div style={{width:420,display:'flex',flexDirection:'column',gap:8}}>
            <div style={{fontSize:13,color:'var(--muted)'}}>Console / Logs</div>
            <div style={{background:'#000',color:'#0ff',padding:8,borderRadius:6,height:420,overflow:'auto',fontFamily:'monospace',fontSize:12}}>
              {logLoading ? <div>Loading logs…</div> : <pre style={{whiteSpace:'pre-wrap',margin:0}}>{logs}</pre>}
            </div>
            <div style={{display:'flex',gap:8}}>
              <Button onClick={()=>fetchLogs(selected)}>Refresh Logs</Button>
              <a href={`/dashboard/vm/${encodeURIComponent(selected)}/`} target="_blank" rel="noreferrer"><Button>Open in new tab</Button></a>
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}
