import React, { useEffect, useState, useRef } from 'react'
import apiFetch from '../lib/fetchWrapper'
import { Line, Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Tooltip } from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Tooltip)

export default function Home(){
  const [stats, setStats] = useState(null)
  const [instancesCount, setInstancesCount] = useState(0)
  const [history, setHistory] = useState({cpu:[], mem:[], net:[]})
  const prevNetRef = useRef(null)
  const [loading, setLoading] = useState(true)
  const intervalRef = useRef(parseInt(localStorage.getItem('nbv2_update_interval') || '3000', 10))

  useEffect(()=>{
    let mounted = true
    async function loadOnce(){
      try{
        const [r1, r2] = await Promise.all([apiFetch('/stats'), apiFetch('/list')])
        const s = await r1.json().catch(()=>null)
        const l = await r2.json().catch(()=>({instances:[]}))
        if(!mounted) return
        setStats(s)
        setInstancesCount((l && l.instances) ? l.instances.length : 0)
        const netNow = (s && s.network) ? ( (s.network.rx_bytes||0) + (s.network.tx_bytes||0) ) : 0
        const now = Date.now()
        const prev = prevNetRef.current
        let netRate = 0
        if(prev && prev.ts){
          const dt = (now - prev.ts)/1000
          netRate = dt>0 ? Math.max(0, Math.round(((netNow - prev.total)/dt)/1024)) : 0 // KB/s
        }
        prevNetRef.current = {ts: now, total: netNow}
        setHistory(h=>{
          const c = (s && s.cpu && typeof s.cpu.usage === 'number') ? s.cpu.usage : 0
          const m = (s && s.memory && typeof s.memory.percent === 'number') ? s.memory.percent : 0
          const n = netRate
          const max = 40
          return {cpu: [...(h.cpu||[]), c].slice(-max), mem: [...(h.mem||[]), m].slice(-max), net: [...(h.net||[]), n].slice(-max)}
        })
      }catch(e){ console.error('stats load error', e) }
      setLoading(false)
    }
    let stopped = false
    async function tick(){
      if(stopped) return
      await loadOnce()
      const ivMs = parseInt(localStorage.getItem('nbv2_update_interval') || '3000', 10)
      await new Promise(r=>setTimeout(r, Math.max(800, ivMs)))
      if(!stopped) tick()
    }
    tick()
    return ()=>{ mounted=false; stopped=true }
  }, [])

  const cpuData = {
    labels: history.cpu.map((_,i)=>i),
    datasets: [{ label: 'CPU %', data: history.cpu, borderColor: 'rgba(30,144,255,0.9)', backgroundColor: 'rgba(30,144,255,0.15)', fill: true }]
  }
  const memData = {
    labels: history.mem.map((_,i)=>i),
    datasets: [{ label: 'Memory %', data: history.mem, borderColor: 'rgba(0,105,255,0.9)', backgroundColor: 'rgba(0,105,255,0.12)', fill: true }]
  }
  const netData = {
    labels: history.net.map((_,i)=>i),
    datasets: [{ label: 'Net KB/s', data: history.net, borderColor: 'rgba(3,169,244,0.9)', backgroundColor: 'rgba(3,169,244,0.18)', fill: true }]
  }

  return (
    <div>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12}}>
        <div>
          <h1 style={{marginTop:0}}>Home Overview</h1>
          <div style={{color:'var(--muted)'}}>Live system metrics and VM summary.</div>
        </div>
        <div style={{textAlign:'right'}}>
          <div className="glass-card" style={{display:'inline-block',padding:'8px 12px'}}>
            <div style={{fontSize:12,color:'var(--muted)'}}>VMs</div>
            <div style={{fontSize:20,fontWeight:700}}>{instancesCount}</div>
          </div>
        </div>
      </div>

      <div style={{marginTop:16,display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:14}}>
        <div className="glass-card card-elev">
          <div style={{fontSize:12,color:'var(--muted)'}}>CPU Usage</div>
          <div style={{fontSize:18,fontWeight:700}}>{loading ? 'Loading…' : (stats && stats.cpu ? `${stats.cpu.usage}%` : '—')}</div>
          <div style={{height:120,marginTop:8}}>{loading ? <div className="skeleton" style={{height:120,borderRadius:8}} /> : <Line data={cpuData} options={{plugins:{legend:{display:false}},elements:{point:{radius:0}},scales:{x:{display:false}},animation:{duration:400}}} />}</div>
        </div>

        <div className="glass-card card-elev">
          <div style={{fontSize:12,color:'var(--muted)'}}>Memory Usage</div>
          <div style={{fontSize:18,fontWeight:700}}>{loading ? 'Loading…' : (stats && stats.memory ? `${stats.memory.percent}%` : '—')}</div>
          <div style={{height:120,marginTop:8}}>{loading ? <div className="skeleton" style={{height:120,borderRadius:8}} /> : <Line data={memData} options={{plugins:{legend:{display:false}},elements:{point:{radius:0}},scales:{x:{display:false}},animation:{duration:400}}} />}</div>
        </div>

        <div className="glass-card card-elev">
          <div style={{fontSize:12,color:'var(--muted)'}}>Network KB/s</div>
          <div style={{fontSize:18,fontWeight:700}}>{loading ? 'Loading…' : (history.net.length? `${history.net[history.net.length-1]} KB/s` : '—')}</div>
          <div style={{height:120,marginTop:8}}>{loading ? <div className="skeleton" style={{height:120,borderRadius:8}} /> : <Line data={netData} options={{plugins:{legend:{display:false}},elements:{point:{radius:0}},scales:{x:{display:false}},animation:{duration:400}}} />}</div>
        </div>
      </div>

      <div style={{marginTop:18}}>
        <div className="glass-card">
          <h3 style={{marginTop:0}}>VM Status</h3>
          <div style={{color:'var(--muted)'}}>Use the VM Manager page for detailed table and actions.</div>
        </div>
      </div>
    </div>
  )
}
import React from 'react'

function StatCard({title,value}){
  return <div className="glass-card card-elev" style={{padding:16}}>
    <div style={{fontSize:12,color:'var(--muted)'}}>{title}</div>
    <div style={{fontSize:22,fontWeight:700}}>{value}</div>
  </div>
}

export default function Home(){
  return (
    <div>
      <h1 style={{marginTop:0}}>Overview</h1>
      <div className="stat-grid">
        <StatCard title="VMs" value="12" />
        <StatCard title="CPU" value="23%" />
        <StatCard title="RAM" value="6.4GB" />
        <StatCard title="Disk" value="120GB" />
      </div>
      <div style={{height:20}} />
      <div className="glass-card">
        <h3>Live charts (demo)</h3>
        <div style={{height:260,display:'flex',alignItems:'center',justifyContent:'center',color:'var(--muted)'}}>Charts placeholder</div>
      </div>
    </div>
  )
}
