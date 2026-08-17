import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cpu, Memory, HardDrives, Network, Plus, ArrowRight, PlayCircle, CheckCircle, Warning, StopCircle } from '@phosphor-icons/react'
import apiFetch from '../lib/fetchWrapper'
import Button from '../components/Button'
import { formatBytes, formatDuration } from '../lib/formatters.js'
import { normalizeVmStatus } from '../lib/vmStatus.js'

function Metric({icon:Icon,label,value,detail,tone='ok'}){
  return <div className="host-metric"><div className="metric-label"><Icon size={21}/><span>{label}</span></div><strong>{value}</strong><div className="metric-track"><i style={{width:`${Math.min(Number.parseFloat(value)||0,100)}%`}}/></div><div className="metric-foot"><span>{detail}</span><em className={`tone-${tone}`}>{tone === 'ok' ? 'Healthy' : 'Attention'}</em></div></div>
}

function VmRow({vm}){
  const status = String(vm.status || '').toLowerCase(); const running = status.includes('running')
  return <div className="fleet-row"><div className="vm-identity"><div><strong>{vm.name}</strong><span>{vm.os || 'OS unavailable'}</span><code>{vm.ip || vm.address || 'Address unavailable'}</code></div></div><div className={`status-text ${running?'live':'down'}`}><i/>{running?'Running':'Stopped'}<small>{running?'Active now':'Offline'}</small></div>{[['CPU',vm.cpu_percent],['RAM',vm.mem_percent],['Disk',vm.disk_percent]].map(([label,val])=><div className="row-stat" key={label}><span>{label}</span><strong>{Number.isFinite(Number(val)) ? `${Math.round(Number(val))}%` : '—'}</strong><div><i style={{width:`${Math.min(Number(val)||0,100)}%`}}/></div></div>)}</div>
}

function ActivityItem({event}){
  const action = String(event.action || 'event'); const Icon = action === 'start' ? PlayCircle : action === 'stop' ? StopCircle : action === 'restart' ? Warning : CheckCircle
  const when = Number(event.ts) ? new Date(Number(event.ts) * 1000).toLocaleString() : 'Time unavailable'
  const subject = event.vm || event.name || event.container || 'system'
  return <div className="activity-item"><Icon size={23}/><div><strong>{action.replace(/[-_]/g,' ')}</strong><span>{subject}{event.reason ? ` • ${event.reason}` : ''}</span></div><time>{when}</time></div>
}

export default function Home(){
  // Progressive load: host/stats/activity paint as soon as they arrive; the
  // VM fleet (which shells out to the VM manager) streams in after.
  const [overview,setOverview] = useState(null)
  const [fleet,setFleet] = useState(null)
  const [error,setError] = useState('')
  const navigate = useNavigate()
  useEffect(()=>{
    let live = true
    const get = (path) => apiFetch(path).then(r=>r.json().then(body=>({ok:r.ok,body}))).then(({ok,body})=>{
      if(!ok || body?.ok === false) throw new Error(body?.error || 'Unable to load host overview')
      return body
    })
    const load = () => {
      get('/overview?parts=host').then(body=>{
        if(!live) return
        setOverview(prev=>({...(prev||{}), ...body})); setError('')
      }).catch(e=>live && setError(e.message || 'Unable to load host overview'))
      get('/overview?parts=instances').then(body=>{
        if(!live) return
        setFleet(body.instances || [])
      }).catch(()=>{ if(live) setFleet(prev=>prev||[]) })
    }
    load(); const timer = window.setInterval(load, 10000)
    return ()=>{ live=false; window.clearInterval(timer) }
  },[])

  if(!overview && !error) return <div className="home-page"><div className="loading-screen" role="status">Loading host overview…</div></div>
  if(error && !overview) return <div className="home-page"><div className="vm-empty-state" role="alert"><strong>Host overview unavailable</strong><p>{error}</p><Button onClick={()=>window.location.reload()}>Retry</Button></div></div>

  const host = overview.host || {}; const stats = overview.stats || {}
  const fleetPending = fleet === null
  const instances = (fleet || []).map(normalizeVmStatus)
  const activity = overview.activity || []
  const cpu = Number(stats.cpu?.usage); const memory = stats.memory || {}; const disk = stats.disk?.[0] || {}; const network = stats.network || {}
  const running = instances.filter(vm=>String(vm.status || '').toLowerCase().includes('running')).length
  return <div className="home-page">
    {error && <div className="vm-refresh-banner" role="status">Showing last known overview • {error}</div>}
    <header className="page-header hero-header"><div><h1>Host Overview</h1><p>{host.hostname || 'Host name unavailable'} <b>•</b> {host.os || 'OS unavailable'} <b>•</b> {host.kernel || 'Kernel unavailable'} <b>•</b> {formatDuration(host.uptimeSeconds)}</p></div><Button onClick={()=>navigate('/vm')}><Plus size={19}/>Create VM</Button></header>
    <section className="host-health" aria-label="Host health"><Metric icon={Cpu} label="CPU" value={Number.isFinite(cpu) ? `${Math.round(cpu)}%` : '—'} detail={`${stats.cpu?.cores ?? '—'} cores`} tone={cpu > 82 ? 'warn' : 'ok'}/><Metric icon={Memory} label="Memory" value={Number.isFinite(Number(memory.percent)) ? `${Math.round(Number(memory.percent))}%` : '—'} detail={`${formatBytes(memory.used)} of ${formatBytes(memory.total)}`} tone={Number(memory.percent) > 82 ? 'warn' : 'ok'}/><Metric icon={HardDrives} label="Storage" value={Number.isFinite(Number(disk.percent)) ? `${Math.round(Number(disk.percent))}%` : '—'} detail={`${formatBytes(disk.used)} of ${formatBytes(disk.total)}`} tone={Number(disk.percent) > 80 ? 'warn' : 'ok'}/><Metric icon={Network} label="Network" value="Live" detail={`Rx ${formatBytes(network.rx_bytes)} • Tx ${formatBytes(network.tx_bytes)}`}/></section>
    <div className="overview-grid"><section className="fleet-section"><div className="section-heading"><h2>Virtual Machines <span>{fleetPending ? '…' : instances.length}</span></h2><button onClick={()=>navigate('/vm')}>View all VMs <ArrowRight size={17}/></button></div><div className="fleet-list">{fleetPending ? [0,1,2].map(i=><div className="fleet-row skeleton-row" key={`sk-${i}`} aria-hidden="true"><div className="skeleton" style={{height:44}}/></div>) : instances.length ? instances.map(vm=><VmRow vm={vm} key={vm.name}/>) : <div className="vm-empty-state">No virtual machines found.</div>}</div><div className="host-summary"><h2>Host Summary</h2><div><span>Total VMs <strong>{fleetPending ? '—' : instances.length}</strong></span><span>Running VMs <strong>{fleetPending ? '—' : running}</strong></span><span>Hypervisor <strong>Reported by host</strong></span><span>Virtualization <strong>Reported by host</strong></span></div></div></section>
      <aside className="activity-section"><div className="section-heading"><h2>Recent System Activity</h2><button onClick={()=>navigate('/logs')}>View all logs <ArrowRight size={17}/></button></div><div className="activity-list">{activity.length ? activity.map((event,index)=><ActivityItem event={event} key={`${event.ts || index}-${event.action || 'event'}`}/>) : <div className="vm-empty-state">No recent activity.</div>}</div></aside></div>
  </div>
}
