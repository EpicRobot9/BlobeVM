import React, { useEffect, useMemo, useRef, useState } from 'react'
import Button from '../components/Button'
import apiFetch from '../lib/fetchWrapper'
import Modal from '../components/Modal'
import VmExec from '../components/VmExec'
import { useToasts } from '../components/ToastProvider'
import { instanceNamesKey, pollDelayMs } from '../lib/polling'
import { canCacheVmSettingsResponse, clearRemovedVmState, createLoadInFlightRunner, createLogSelectionTracker } from '../lib/vmManagerRaces'
import { canUseRemotePlacement, createPlacementPayload, getEligibleRemoteHosts, getPlacementValidationReason, hostOptionLabel, normalizeHostInventory, remotePlacementDisabledReason } from '../lib/hostPlacement'
import { canClaimProvisioningJob, canOpenInventoryVm, canOpenProvisionedVm, canRetryProvisioningConsole, deprovisioningPayload, provisioningClaimPayload, provisioningConsoleRetryPayload, provisioningProgress } from '../lib/provisioningUi'

function toneFor(status){
  const s = (status || '').toLowerCase()
  if(s.includes('up') || s.includes('running') || s.includes('healthy')) return 'live'
  if(s.includes('rebuild') || s.includes('update')) return 'busy'
  return 'down'
}

function StatusBadge({ status }){
  const tone = toneFor(status)
  const colors = {
    live: ['#22c55e', '#14532d'],
    busy: ['#f59e0b', '#78350f'],
    down: ['#fb7185', '#4c0519']
  }
  const [dot, bg] = colors[tone]
  return (
    <div className="vm-status-badge" style={{ background:bg, color:'#fff' }}>
      <span style={{ width:10, height:10, borderRadius:999, background:dot, boxShadow:`0 0 14px ${dot}` }} />
      <span>{status || 'Unknown'}</span>
    </div>
  )
}

function StatMeter({ label, value, tone='cpu' }){
  const safe = Math.max(0, Math.min(100, Number(value || 0)))
  return (
    <div className="vm-meter">
      <div className="vm-meter-head">
        <span>{label}</span>
        <strong>{safe}%</strong>
      </div>
      <div className="vm-meter-track">
        <div className={`vm-meter-fill ${tone}`} style={{ width: `${safe}%` }} />
      </div>
    </div>
  )
}

function VmCard({ vm, host, onAction, onDetails, onProfileChange, onManage, onTeardown, profileBusy, busyAction, refreshing }){
  const tone = toneFor(vm.status)
  const profile = vm._profile || vm._optimizer?.profile || 'desktop'
  const isRemote = vm.placement === 'remote'
  const consoleReady = canOpenInventoryVm(vm)
  const placementLabel = isRemote ? 'RemoteVM' : 'Local VM'
  const hostName = vm.host_name || host?.display_name || (isRemote ? vm.host_id || 'Remote host' : 'EpicVM Server')
  const hostUnavailable = isRemote && host?.online !== true
  const hostChipClass = hostUnavailable ? 'vm-meta-chip vm-host-chip offline' : 'vm-meta-chip vm-host-chip'
  return (
    <div className={`vm-card vm-card-${tone}`}>
      <div className="vm-card-refresh" aria-hidden="true">
        {refreshing ? <span className="vm-mini-spinner" /> : <span className="vm-refresh-idle" />}
      </div>

      <div className="vm-card-top">
        <div>
          <div className="vm-card-name">{vm.name}</div>
          <div className="vm-card-url"><a href={vm.url} target="_blank" rel="noreferrer">{vm.url}</a></div>
          <div className="vm-destination-summary">
            <span>Destination</span>
            <strong>{placementLabel} · {hostName}</strong>
          </div>
          {vm._title ? <div style={{color:'var(--muted)', fontSize:13, marginTop:6}}>Tab title: {vm._title}</div> : null}
          {vm._hostOverride ? <div style={{color:'var(--muted)', fontSize:13, marginTop:4}}>Domain: {vm._hostOverride}</div> : null}
        </div>
        <StatusBadge status={vm.status || 'Unknown'} />
      </div>

      <div className="vm-card-stats">
        <StatMeter label="CPU" value={vm._stats?.cpu_percent ?? 0} tone="cpu" />
        <StatMeter label="RAM" value={vm._stats?.mem_percent ?? 0} tone="ram" />
      </div>

      <div className="vm-card-meta">
        <div className={`vm-meta-chip vm-placement-chip ${isRemote ? 'remote' : 'local'}`}>{placementLabel}</div>
        <div className={hostChipClass}>Host: {hostName}</div>
        {hostUnavailable ? <div className="vm-meta-chip vm-host-chip offline">Host offline</div> : null}
        <div className="vm-meta-chip">Port: {vm.port || '—'}</div>
        <div className="vm-meta-chip">Name: {vm.name}</div>
        <label className="vm-meta-chip" style={{ gap:8 }}>
          <span>Type</span>
          <select value={profile} disabled={profileBusy} onChange={e=>onProfileChange(vm.name, e.target.value)} style={{ background:'rgba(2,6,23,.8)', color:'#fff', border:'1px solid rgba(255,255,255,.12)', borderRadius:8, padding:'4px 8px' }}>
            <option value="light">light</option>
            <option value="desktop">desktop</option>
            <option value="interactive">interactive</option>
            <option value="gaming">gaming</option>
            <option value="background">background</option>
            <option value="disposable">disposable</option>
          </select>
        </label>
      </div>

      <div className="vm-card-actions">
        <Button disabled={busyAction || hostUnavailable} onClick={()=>onAction('start', vm.name)}>Start</Button>
        <Button disabled={busyAction || hostUnavailable} onClick={()=>onAction('stop', vm.name)}>Stop</Button>
        <Button disabled={busyAction || hostUnavailable} onClick={()=>onAction('restart', vm.name)}>Restart</Button>
        <Button disabled={busyAction || !consoleReady} title={consoleReady ? 'Open console' : 'Console is available only after provisioning verification'} onClick={()=>onDetails(vm.name)}>Console</Button>
        <Button disabled={busyAction || hostUnavailable} onClick={()=>onManage(vm.name)}>Manage</Button>
        {isRemote ? <Button disabled={busyAction || hostUnavailable} onClick={()=>onTeardown(vm.name, vm.host_id)}>Tear down</Button> : null}
      </div>
    </div>
  )
}

export default function VMManager(){
  const { addToast } = useToasts()
  const [instances, setInstances] = useState([])
  const [initialLoading, setInitialLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [selected, setSelected] = useState(null)
  const [selectedVmHostId, setSelectedVmHostId] = useState('local')
  const [selectedVmUrl, setSelectedVmUrl] = useState('')
  const [logs, setLogs] = useState('')
  const [logLoading, setLogLoading] = useState(false)
  const [announcement, setAnnouncement] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const [optimizer, setOptimizer] = useState({ capacity:{}, vmStates:[], profiles:{} })
  const [hosts, setHosts] = useState([])
  const [placement, setPlacement] = useState('local')
  const [selectedHostId, setSelectedHostId] = useState('')
  const [invalidatedHostId, setInvalidatedHostId] = useState('')
  const [profileBusy, setProfileBusy] = useState('')
  const [createName, setCreateName] = useState('')
  const [provisioningProfile, setProvisioningProfile] = useState('standard')
  const [provisioningJob, setProvisioningJob] = useState(null)
  const [provisioningHostId, setProvisioningHostId] = useState('')
  const [provisioningClaimToken, setProvisioningClaimToken] = useState('')
  const [claimDraft, setClaimDraft] = useState({ username:'', password:'', confirm:'' })
  const [provisioningBusy, setProvisioningBusy] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [manageVm, setManageVm] = useState(null)
  const [manageVmHostId, setManageVmHostId] = useState('local')
  const [manageDraft, setManageDraft] = useState({ title:'', hostOverride:'', faviconUrl:'', accessMode:'public', assignedUsers:[] })
  const [manageBusy, setManageBusy] = useState(false)
  const [faviconFile, setFaviconFile] = useState(null)
  const [enrollmentDraft, setEnrollmentDraft] = useState({ hostId:'', displayName:'', agentUrl:'' })
  const [enrollmentFile, setEnrollmentFile] = useState(null)
  const [enrollmentBusy, setEnrollmentBusy] = useState(false)
  const enrollmentFileInputRef = useRef(null)
  const prevStatsRef = useRef({})
  const lastAnnounceRef = useRef({})
  const didLoadOnceRef = useRef(false)
  const instanceNamesKeyRef = useRef('')
  const vmSettingsCacheRef = useRef(new Map())
  const vmSettingsInFlightRef = useRef(new Map())
  const vmSettingsGenerationRef = useRef(0)

  useEffect(()=>{
    const params = new URLSearchParams(window.location.search)
    const jobId = String(params.get('resume_job') || '')
    const hostId = String(params.get('host_id') || '')
    if(!/^[a-f0-9]{32}$/i.test(jobId) || !/^[a-z0-9][a-z0-9._-]{0,62}$/.test(hostId)) return
    let stopped = false
    apiFetch(`/provisioning-jobs/${encodeURIComponent(jobId)}?host_id=${encodeURIComponent(hostId)}`)
      .then(async res => {
        const body = await res.json().catch(()=>({ ok:res.ok }))
        if(!res.ok || body.ok === false) throw new Error(body.error?.message || body.error || 'Unable to resume provisioning job')
        if(!stopped){ setProvisioningHostId(hostId); setProvisioningJob(body.job || null) }
      })
      .catch(err => { if(!stopped) addToast({title:'Provisioning resume failed',message:String(err),type:'error',timeout:7000}) })
    return ()=>{ stopped=true }
  }, [addToast])
  const vmSettingsNamesRef = useRef(new Set())
  const loadSequenceRef = useRef(0)
  const loadInFlightRef = useRef(null)
  const logRequestSequenceRef = useRef(0)
  const logSelectionTrackerRef = useRef(null)
  const loadRunnerRef = useRef(null)
  const mountedRef = useRef(true)
  const manageRequestSequenceRef = useRef(0)
  if(!loadRunnerRef.current) loadRunnerRef.current = createLoadInFlightRunner()
  if(!logSelectionTrackerRef.current) logSelectionTrackerRef.current = createLogSelectionTracker()

  async function fetchVmSettings(name, requestSequence = loadSequenceRef.current){
    if(vmSettingsCacheRef.current.has(name)) return vmSettingsCacheRef.current.get(name)
    const existing = vmSettingsInFlightRef.current.get(name)
    if(existing) return existing
    const requestGeneration = vmSettingsGenerationRef.current
    const request = (async()=>{
      try{
        const resp = await apiFetch(`/vm-settings/${encodeURIComponent(name)}`)
        const data = await resp.json().catch(()=>({ ok:false }))
        if(resp.ok && data && data.ok !== false && requestSequence === loadSequenceRef.current && requestGeneration === vmSettingsGenerationRef.current && vmSettingsNamesRef.current.has(name)) vmSettingsCacheRef.current.set(name, data)
        return data
      }catch(_e){
        return null
      }finally{
        if(vmSettingsInFlightRef.current.get(name) === request) vmSettingsInFlightRef.current.delete(name)
      }
    })()
    vmSettingsInFlightRef.current.set(name, request)
    return request
  }

  async function loadInternal({ silent = false } = {}){
    if(!mountedRef.current) return
    const requestSequence = ++loadSequenceRef.current
    if(silent && didLoadOnceRef.current){
      setRefreshing(true)
    } else {
      setInitialLoading(true)
    }
    try{
      const [rList, rStats, rOpt, rSettings, rHosts] = await Promise.all([
        apiFetch('/list?fleet=1'),
        apiFetch('/vm/stats').catch(()=>({ok:false})),
        apiFetch('/optimizer/v2/summary').catch(()=>({ok:false})),
        apiFetch('/settings').catch(()=>({ok:false})),
        apiFetch('/hosts').catch(()=>({ok:false}))
      ])
      const j = await rList.json().catch(()=>({instances:[]}))
      const statJ = rStats && rStats.ok ? await rStats.json().catch(()=>({vms:{}})) : (rStats && typeof rStats.json === 'function' ? await rStats.json().catch(()=>({vms:{}})) : {vms:{}})
      const optJ = rOpt && typeof rOpt.json === 'function' ? await rOpt.json().catch(()=>({ok:false})) : {ok:false}
      const settingsJ = rSettings && typeof rSettings.json === 'function' ? await rSettings.json().catch(()=>({})) : {}
      const hostsJ = rHosts && rHosts.ok !== false && typeof rHosts.json === 'function' ? await rHosts.json().catch(()=>null) : null
      if(!mountedRef.current || requestSequence !== loadSequenceRef.current) return
      if(hostsJ !== null) setHosts(normalizeHostInventory(hostsJ))
      const statsMap = (statJ && statJ.vms) ? statJ.vms : {}
      const optimizerVmMap = Object.fromEntries(((optJ && optJ.vmStates) || []).map(v => [v.name, v]))
      const profileMap = (optJ && optJ.profiles) || {}
      const titleMap = (settingsJ && settingsJ.vm_titles) || {}

      const listedInstances = j.instances || []
      const namesKey = instanceNamesKey(listedInstances)
      if(namesKey !== instanceNamesKeyRef.current){
        instanceNamesKeyRef.current = namesKey
        const currentNames = new Set(listedInstances.map(it => it.name))
        const removedNames = [...vmSettingsNamesRef.current].filter(name => !currentNames.has(name))
        vmSettingsGenerationRef.current += 1
        vmSettingsNamesRef.current = currentNames
        for(const name of vmSettingsCacheRef.current.keys()){
          if(!currentNames.has(name)) vmSettingsCacheRef.current.delete(name)
        }
        for(const name of vmSettingsInFlightRef.current.keys()){
          if(!currentNames.has(name)) vmSettingsInFlightRef.current.delete(name)
        }
        clearRemovedVmState(prevStatsRef.current, lastAnnounceRef.current, removedNames)
      }
      const missingSettings = listedInstances.filter(it => !vmSettingsCacheRef.current.has(it.name))
      await Promise.all(missingSettings.map(it => fetchVmSettings(it.name, requestSequence)))
      if(!mountedRef.current || requestSequence !== loadSequenceRef.current) return
      const vmSettingsMap = Object.fromEntries([...vmSettingsCacheRef.current.entries()])

      const insts = (j.instances || []).map(it => ({
        ...it,
        _stats: statsMap[it.name] || statsMap[''+it.name] || statsMap[it.name],
        _optimizer: optimizerVmMap[it.name] || {},
        _profile: profileMap[it.name] || 'desktop',
        _title: vmSettingsMap[it.name]?.title || titleMap[it.name] || '',
        _hostOverride: vmSettingsMap[it.name]?.hostOverride || '',
        _faviconUrl: vmSettingsMap[it.name]?.faviconUrl || ''
      }))

      try{
        const prev = prevStatsRef.current || {}
        const now = Date.now()
        const cpuThresholdDelta = parseFloat(localStorage.getItem('nbv2_announce_cpu_delta') || '20')
        const memThresholdDelta = parseFloat(localStorage.getItem('nbv2_announce_mem_delta') || '25')
        const cpuAbsolute = parseFloat(localStorage.getItem('nbv2_announce_cpu_absolute') || '85')
        const memAbsolute = parseFloat(localStorage.getItem('nbv2_announce_mem_absolute') || '90')
        const announceCooldownMs = parseInt(localStorage.getItem('nbv2_announce_cooldown') || String(60*1000), 10)

        for(const [vm, s] of Object.entries(statsMap || {})){
          if(!mountedRef.current || requestSequence !== loadSequenceRef.current) return
          const cpu = (s && typeof s.cpu_percent === 'number') ? s.cpu_percent : null
          const mem = (s && typeof s.mem_percent === 'number') ? s.mem_percent : null
          const p = prev[vm] || {}
          const prevCpu = (p && typeof p.cpu_percent === 'number') ? p.cpu_percent : undefined
          const prevMem = (p && typeof p.mem_percent === 'number') ? p.mem_percent : undefined
          const lastAnn = lastAnnounceRef.current[vm] || 0

          if(prevCpu !== undefined && cpu !== null && ((((cpu - prevCpu) >= cpuThresholdDelta) && cpu >= 30) || (cpu >= cpuAbsolute && prevCpu < cpuAbsolute)) && now - lastAnn > announceCooldownMs){
            const msg = `Alert: VM ${vm} CPU ${cpu}% (was ${prevCpu}%)`
            setAnnouncement(msg)
            addToast({title:`VM ${vm} CPU`, message: `${cpu}% (was ${prevCpu}%)`, type:'warn', timeout:8000})
            lastAnnounceRef.current[vm] = now
            setTimeout(()=>setAnnouncement(''), 8000)
          }

          if(prevMem !== undefined && mem !== null && ((((mem - prevMem) >= memThresholdDelta) && mem >= 40) || (mem >= memAbsolute && prevMem < memAbsolute)) && now - lastAnn > announceCooldownMs){
            const msg = `Alert: VM ${vm} memory ${mem}% (was ${prevMem}%)`
            setAnnouncement(msg)
            addToast({title:`VM ${vm} Memory`, message: `${mem}% (was ${prevMem}%)`, type:'warn', timeout:8000})
            lastAnnounceRef.current[vm] = now
            setTimeout(()=>setAnnouncement(''), 8000)
          }
        }
      }catch(e){}

      if(!mountedRef.current || requestSequence !== loadSequenceRef.current) return
      prevStatsRef.current = statsMap || {}
      setInstances(insts)
      if(optJ && optJ.ok) setOptimizer(optJ)
      didLoadOnceRef.current = true
    }catch(e){
      if(mountedRef.current && requestSequence === loadSequenceRef.current){
        console.error('load instances', e)
        addToast({ title:'Load failed', message:String(e), type:'error', timeout:8000 })
      }
    }
    if(mountedRef.current && requestSequence === loadSequenceRef.current){
      setInitialLoading(false)
      setRefreshing(false)
    }
  }

  async function load(options = {}){
    if(!mountedRef.current) return
    const request = loadRunnerRef.current.run(() => loadInternal(options))
    loadInFlightRef.current = request
    try{
      return await request
    }finally{
      if(loadInFlightRef.current === request) loadInFlightRef.current = null
    }
  }

  useEffect(()=>{
    let stopped = false
    let timer = null
    let loading = false

    const clearTimer = () => {
      if(timer !== null){
        clearTimeout(timer)
        timer = null
      }
    }

    const schedule = (delay = 0) => {
      if(stopped || document.visibilityState !== 'visible' || timer !== null) return
      timer = setTimeout(async () => {
        timer = null
        if(stopped || document.visibilityState !== 'visible') return
        if(loading) return schedule(pollDelayMs({ visible:true, intervalMs: parseInt(localStorage.getItem('nbv2_update_interval') || '3000', 10) }))
        loading = true
        try{
          await load({ silent: didLoadOnceRef.current })
        }finally{
          loading = false
          if(!stopped && document.visibilityState === 'visible'){
            const intervalMs = parseInt(localStorage.getItem('nbv2_update_interval') || '3000', 10)
            schedule(pollDelayMs({ visible:true, intervalMs }))
          }
        }
      }, delay)
    }

    const onVisibilityChange = () => {
      clearTimer()
      if(document.visibilityState === 'visible') schedule(0)
    }

    document.addEventListener('visibilitychange', onVisibilityChange)
    schedule(0)
    return () => {
      stopped = true
      clearTimer()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [])

  useEffect(()=>()=>{
    mountedRef.current = false
    loadSequenceRef.current += 1
    logRequestSequenceRef.current += 1
    manageRequestSequenceRef.current += 1
  }, [])

  async function action(cmd, name, opts = {}){
    const key = `${cmd}:${name}`
    const force = !!opts.force
    const hostId = opts.hostId || 'local'
    const isRemote = hostId !== 'local'
    setBusyAction(key)
    try{
      if(cmd === 'start' && !isRemote){
        await apiFetch(`/optimizer/activity/${encodeURIComponent(name)}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ source: force ? 'force-start-click' : 'start-click' }) }).catch(()=>null)
        const admRes = await apiFetch(`/optimizer/admission/${encodeURIComponent(name)}${force ? '?force=1' : ''}`).catch(()=>null)
        const admBody = admRes && typeof admRes.json === 'function' ? await admRes.json().catch(()=>null) : null
        if(admBody && admBody.admission && admBody.admission.ok === false && !force){
          const proceed = window.confirm(`${admBody.admission.reason || 'Start blocked by optimizer admission control'}\n\nForce start anyway?`)
          if(proceed){
            setBusyAction('')
            return action(cmd, name, { force:true })
          }
          throw new Error(admBody.admission.reason || 'Start blocked by optimizer admission control')
        }
      }
      const actionParams = new URLSearchParams()
      if(cmd === 'start' && force) actionParams.set('force', '1')
      if(hostId) actionParams.set('host_id', hostId)
      const actionQuery = actionParams.toString() ? `?${actionParams.toString()}` : ''
      const startBody = cmd === 'start' && (force || isRemote) ? actionParams : undefined
      const res = await apiFetch(`/${cmd}/${encodeURIComponent(name)}${startBody ? '' : actionQuery}`, {
        method:'POST',
        headers: startBody ? {'Content-Type':'application/x-www-form-urlencoded'} : undefined,
        body: startBody
      })
      const body = await res.json().catch(()=>({ ok:res.ok }))
      if(!res.ok || body.ok === false){
        throw new Error(body.error || body.message || `Failed to ${cmd} ${name}`)
      }
      addToast({ title:`${name}`, message:`${cmd} request sent successfully${force ? ' (forced)' : ''}`, type:'success', timeout:5000 })
    }catch(e){
      console.error('action error', e)
      addToast({ title:`${name}`, message:String(e), type:'error', timeout:8000 })
    }
    setBusyAction('')
    setTimeout(()=>load({ silent:true }), 800)
  }

  async function enrollRemoteHost(e){
    e?.preventDefault?.()
    if(!enrollmentFile) return
    setEnrollmentBusy(true)
    try{
      const form = new FormData()
      form.append('host_id', enrollmentDraft.hostId)
      form.append('display_name', enrollmentDraft.displayName)
      form.append('agent_url', enrollmentDraft.agentUrl)
      form.append('token_file', enrollmentFile)
      const res = await apiFetch('/hosts/enroll', { method:'POST', body:form })
      const body = await res.json().catch(()=>({ ok:res.ok }))
      if(!res.ok || body.ok === false) throw new Error(body.error || 'RemoteVM enrollment failed')
      addToast({ title:'RemoteVM connected', message:`${body.host?.display_name || enrollmentDraft.displayName} is now available`, type:'success', timeout:6000 })
      setEnrollmentDraft({ hostId:'', displayName:'', agentUrl:'' })
      setEnrollmentFile(null)
      if(enrollmentFileInputRef.current) enrollmentFileInputRef.current.value = ''
      await load({ silent:true })
    }catch(err){
      addToast({ title:'RemoteVM enrollment failed', message:String(err), type:'error', timeout:9000 })
    }finally{
      setEnrollmentBusy(false)
    }
  }

  async function createVm(e){
    e?.preventDefault?.()
    const name = createName.trim().toLowerCase()
    if(!name) return
    const placementReason = placement === 'remote' && !canUseRemotePlacement(hosts)
      ? remotePlacementDisabledReason(hosts)
      : getPlacementValidationReason({ placement, hostId: selectedHostId, hosts })
    if(placementReason) return
    const payload = createPlacementPayload({ name, placement, hostId: selectedHostId })
    setCreateBusy(true)
    try{
      if(placement === 'remote'){
        const res = await apiFetch('/provisioning-jobs', { method:'POST', headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()}, body: JSON.stringify({ host_id:selectedHostId, name, profile:provisioningProfile }) })
        const j = await res.json().catch(()=>({ ok:res.ok }))
        if(!res.ok || j.ok === false) throw new Error(j.error?.message || j.error || `Failed to start provisioning for ${name}`)
        setProvisioningJob(j.job || null)
        setProvisioningHostId(selectedHostId)
        setProvisioningClaimToken(String(j.claimToken || ''))
        setClaimDraft({ username:'', password:'', confirm:'' })
        addToast({ title:'Provisioning started', message:`${name} is moving through the EpicVM setup gates`, type:'success', timeout:6000 })
        setCreateName('')
        setCreateBusy(false)
        return
      }
      const body = new URLSearchParams(payload)
      const res = await apiFetch('/create', { method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body })
      const j = await res.json().catch(()=>({ ok:res.ok }))
      if(!res.ok || j.ok === false) throw new Error(j.error || `Failed to create ${name}`)
      addToast({ title:'VM created', message:`${name} is being created`, type:'success', timeout:5000 })
      setCreateName('')
      setTimeout(()=>load({ silent:true }), 1000)
    }catch(err){
      addToast({ title:'Create failed', message:String(err), type:'error', timeout:8000 })
    }
    setCreateBusy(false)
  }

  async function refreshProvisioningJob(job = provisioningJob){
    if(!job?.id || !provisioningHostId) return null
    const res = await apiFetch(`/provisioning-jobs/${encodeURIComponent(job.id)}?host_id=${encodeURIComponent(provisioningHostId)}`)
    const body = await res.json().catch(()=>({ ok:res.ok }))
    if(!res.ok || body.ok === false) throw new Error(body.error?.message || body.error || 'Unable to read provisioning progress')
    const next = body.job || null
    setProvisioningJob(next)
    return next
  }

  useEffect(()=>{
    if(!provisioningJob?.id || !provisioningHostId || ['ready','failed','console_failed'].includes(String(provisioningJob.state || ''))) return undefined
    let stopped = false
    const tick = async()=>{
      try{ if(!stopped) await refreshProvisioningJob() }catch(err){ if(!stopped) addToast({title:'Provisioning status unavailable', message:String(err), type:'error', timeout:7000}) }
    }
    const timer = setInterval(tick, 2500)
    return ()=>{ stopped=true; clearInterval(timer) }
  }, [provisioningJob?.id, provisioningJob?.state, provisioningHostId])

  async function claimProvisioningJob(e){
    e?.preventDefault?.()
    if(!provisioningJob || !canClaimProvisioningJob(provisioningJob) || !provisioningClaimToken) return
    if(claimDraft.password !== claimDraft.confirm) { addToast({title:'Claim rejected', message:'Passwords do not match.', type:'error', timeout:6000}); return }
    setProvisioningBusy(true)
    try{
      const res = await apiFetch(`/provisioning-jobs/${encodeURIComponent(provisioningJob.id)}/claim`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(provisioningClaimPayload({hostId:provisioningHostId, username:claimDraft.username, password:claimDraft.password, claimToken:provisioningClaimToken})) })
      const body = await res.json().catch(()=>({ ok:res.ok }))
      if(!res.ok || body.ok === false) throw new Error(body.error?.message || body.error || 'Guest claim failed')
      setProvisioningClaimToken('')
      setClaimDraft({ username:'', password:'', confirm:'' })
      setProvisioningJob(body.job || provisioningJob)
      addToast({title:'Guest claimed', message:'Continuing Tailscale, console, and readiness verification.', type:'success', timeout:7000})
    }catch(err){
      setProvisioningClaimToken('')
      await refreshProvisioningJob().catch(()=>null)
      addToast({title:'Claim failed', message:String(err), type:'error', timeout:8000})
    }
    finally{ setClaimDraft({ username:'', password:'', confirm:'' }); setProvisioningBusy(false) }
  }

  async function retryProvisioningConsole(e){
    e?.preventDefault?.()
    if(!provisioningJob || !canRetryProvisioningConsole(provisioningJob)) return
    if(claimDraft.password !== claimDraft.confirm) { addToast({title:'Retry rejected', message:'Passwords do not match.', type:'error', timeout:6000}); return }
    setProvisioningBusy(true)
    try{
      const res = await apiFetch(`/provisioning-jobs/${encodeURIComponent(provisioningJob.id)}/retry-console`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(provisioningConsoleRetryPayload({hostId:provisioningHostId, username:claimDraft.username, password:claimDraft.password})) })
      const body = await res.json().catch(()=>({ ok:res.ok }))
      if(!res.ok || body.ok === false) throw new Error(body.error?.message || body.error || 'Console retry failed')
      setProvisioningJob(body.job || provisioningJob)
      addToast({title:'Console ready', message:'The isolated browser console passed its reachability gate.', type:'success', timeout:7000})
    }catch(err){
      await refreshProvisioningJob().catch(()=>null)
      addToast({title:'Console retry failed', message:String(err), type:'error', timeout:8000})
    }finally{ setClaimDraft({ username:'', password:'', confirm:'' }); setProvisioningBusy(false) }
  }

  async function startTeardown(name, hostId){
    const confirmed = window.prompt(`Tear down ${name}? This stops the VM, revokes its device, removes its console route, and quarantines resources for seven days. Type ${name} to confirm.`)
    if(confirmed !== name) return
    setBusyAction(`teardown:${name}`)
    try{
      const res = await apiFetch('/deprovisioning-jobs', { method:'POST', headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()}, body: JSON.stringify(deprovisioningPayload({hostId, name})) })
      const body = await res.json().catch(()=>({ok:res.ok}))
      if(!res.ok || body.ok === false) throw new Error(body.error?.message || body.error || 'Teardown failed')
      addToast({title:name, message:'Teardown started; route and device revocation are being processed.', type:'success', timeout:7000})
      if(manageVm === name) setManageVm(null)
      setTimeout(()=>load({silent:true}), 900)
    }catch(err){ addToast({title:'Teardown failed', message:String(err), type:'error', timeout:8000}) }
    finally{ setBusyAction('') }
  }

  async function setProfile(name, profile){
    setProfileBusy(name)
    try{
      const r = await apiFetch(`/optimizer/profile/${encodeURIComponent(name)}`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ profile })
      })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || `Failed to set profile for ${name}`)
      setInstances(items => items.map(vm => vm.name === name ? { ...vm, _profile: j.profile || profile } : vm))
      addToast({ title:name, message:`VM type set to ${j.profile || profile}`, type:'success', timeout:4000 })
      setTimeout(()=>load({ silent:true }), 500)
    }catch(e){
      addToast({ title:name, message:String(e), type:'error', timeout:8000 })
    }
    setProfileBusy('')
  }

  async function openDetails(name, hostId = 'local', vmUrl = ''){
    logSelectionTrackerRef.current.select(name)
    logRequestSequenceRef.current += 1
    setSelected(name)
    setSelectedVmHostId(hostId || 'local')
    setSelectedVmUrl(vmUrl || '')
    await apiFetch(`/optimizer/activity/${encodeURIComponent(name)}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ source:'details-open' }) }).catch(()=>null)
    await fetchLogs(name, hostId)
  }

  async function fetchLogs(name, hostId = selectedVmHostId){
    if(!logSelectionTrackerRef.current.isSelected(name)) return Promise.resolve()
    const selectionGeneration = logSelectionTrackerRef.current.selectionGeneration
    const existing = logSelectionTrackerRef.current.get(name)
    if(existing) return existing
    const requestSequence = ++logRequestSequenceRef.current
    setLogLoading(true)
    const request = logSelectionTrackerRef.current.run(name, async()=>{
      try{
        const query = hostId && hostId !== 'local' ? `?host_id=${encodeURIComponent(hostId)}` : ''
        const r = await apiFetch(`/vm/logs/${encodeURIComponent(name)}${query}`)
        const j = await r.json().catch(()=>({ok:false, logs:''}))
        if(requestSequence !== logRequestSequenceRef.current || !logSelectionTrackerRef.current.isCurrent(name, selectionGeneration)) return
        setLogs(j.logs || j.logs === '' ? (j.logs || '') : (j.error || ''))
      }catch(e){
        if(requestSequence === logRequestSequenceRef.current && logSelectionTrackerRef.current.isCurrent(name, selectionGeneration)) setLogs('Error loading logs: ' + String(e))
      }finally{
        if(requestSequence === logRequestSequenceRef.current && logSelectionTrackerRef.current.isCurrent(name, selectionGeneration)) setLogLoading(false)
      }
    })
    return request
  }

  async function openManage(name, hostId = 'local'){
    const requestSequence = ++manageRequestSequenceRef.current
    const requestGeneration = vmSettingsGenerationRef.current
    setManageVm(name)
    setManageVmHostId(hostId || 'local')
    setManageBusy(true)
    setFaviconFile(null)
    try{
      const r = await apiFetch(`/vm-settings/${encodeURIComponent(name)}`)
      const j = await r.json().catch(()=>({}))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed to load VM settings')
      if(!mountedRef.current || requestSequence !== manageRequestSequenceRef.current) return
      if(canCacheVmSettingsResponse({ requestSequence, currentSequence: manageRequestSequenceRef.current, requestGeneration, currentGeneration: vmSettingsGenerationRef.current, namePresent: vmSettingsNamesRef.current.has(name) })){
        vmSettingsCacheRef.current.set(name, j)
        setManageDraft({
          title: j.title || '',
          hostOverride: j.hostOverride || '',
          faviconUrl: j.faviconUrl || '',
          accessMode: j.accessMode || 'public',
          assignedUsers: j.assignedUsers || []
        })
      }
    }catch(e){
      if(mountedRef.current && requestSequence === manageRequestSequenceRef.current) addToast({ title:'Load failed', message:String(e), type:'error', timeout:7000 })
    }
    if(mountedRef.current && requestSequence === manageRequestSequenceRef.current) setManageBusy(false)
  }

  async function saveManageSettings(){
    if(!manageVm) return
    setManageBusy(true)
    try{
      const r = await apiFetch(`/vm-settings/${encodeURIComponent(manageVm)}`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ title: manageDraft.title, hostOverride: manageDraft.hostOverride, accessMode: manageDraft.accessMode })
      })
      const j = await r.json().catch(()=>({ ok:r.ok }))
      if(!r.ok || j.ok === false) throw new Error(j.error || 'Failed saving VM settings')

      if(faviconFile){
        const fd = new FormData()
        fd.append('file', faviconFile)
        const favRes = await apiFetch(`/upload-vm-favicon/${encodeURIComponent(manageVm)}`, { method:'POST', body: fd })
        const favJ = await favRes.json().catch(()=>({ ok:favRes.ok }))
        if(!favRes.ok || favJ.ok === false) throw new Error(favJ.error || 'Failed uploading favicon')
      }

      vmSettingsGenerationRef.current += 1
      vmSettingsCacheRef.current.clear()
      vmSettingsInFlightRef.current.clear()
      if(!mountedRef.current) return
      addToast({ title:manageVm, message:'VM settings updated', type:'success', timeout:5000 })
      setManageVm(null)
      setFaviconFile(null)
      setTimeout(()=>load({ silent:true }), 700)
    }catch(e){
      if(mountedRef.current) addToast({ title:manageVm || 'VM', message:String(e), type:'error', timeout:8000 })
    }
    if(mountedRef.current) setManageBusy(false)
  }

  async function deleteVm(name, hostId = manageVmHostId){
    const confirmed = window.prompt(`Delete ${name}? This removes the VM. Type DELETE to confirm.`)
    if(confirmed !== 'DELETE') return
    setBusyAction(`delete:${name}`)
    try{
      const query = hostId && hostId !== 'local' ? `?host_id=${encodeURIComponent(hostId)}` : ''
      if(hostId && hostId !== 'local') return startTeardown(name, hostId)
      const res = await apiFetch(`/delete/${encodeURIComponent(name)}${query}`, { method:'POST' })
      const j = await res.json().catch(()=>({ ok:res.ok }))
      if(!res.ok || j.ok === false) throw new Error(j.error || `Failed to delete ${name}`)
      addToast({ title:name, message:'VM deleted', type:'success', timeout:5000 })
      if(manageVm === name) setManageVm(null)
      setTimeout(()=>load({ silent:true }), 700)
    }catch(e){
      addToast({ title:name, message:String(e), type:'error', timeout:8000 })
    }
    setBusyAction('')
  }

  useEffect(()=>{
    let timer = null
    let stopped = false
    const clear = () => {
      if(timer !== null){ clearTimeout(timer); timer = null }
    }
    const schedule = (delay = 2500) => {
      if(!stopped && selected && document.visibilityState === 'visible' && timer === null){
        timer = setTimeout(async()=>{
          timer = null
          if(stopped || !selected || document.visibilityState !== 'visible') return
          await fetchLogs(selected)
          schedule(2500)
        }, delay)
      }
    }
    const start = () => {
      clear()
      schedule()
    }
    const onVisibilityChange = () => {
      if(document.visibilityState === 'visible') start()
      else clear()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    start()
    return ()=>{
      stopped = true
      logRequestSequenceRef.current += 1
      logSelectionTrackerRef.current.select(null)
      clear()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [selected])

  const summary = useMemo(()=>{
    const total = instances.length
    const live = instances.filter(x => toneFor(x.status) === 'live').length
    const down = instances.filter(x => toneFor(x.status) === 'down').length
    const busy = instances.filter(x => toneFor(x.status) === 'busy').length
    return { total, live, down, busy }
  }, [instances])

  const eligibleRemoteHosts = useMemo(() => getEligibleRemoteHosts(hosts), [hosts])
  const remotePlacementAvailable = canUseRemotePlacement(hosts)
  const hostsById = useMemo(() => Object.fromEntries(hosts.map(host => [host.id, host])), [hosts])
  const selectedRemoteHost = eligibleRemoteHosts.find(host => host.id === selectedHostId)
  const placementReason = placement !== 'remote'
    ? ''
    : invalidatedHostId && !selectedHostId
      ? 'Selected remote host is no longer available'
      : !remotePlacementAvailable
        ? remotePlacementDisabledReason(hosts)
        : getPlacementValidationReason({ placement, hostId: selectedHostId, hosts })
  const destinationSummary = placement === 'remote'
    ? selectedRemoteHost
      ? `This RemoteVM will be created on ${selectedRemoteHost.display_name} over Tailscale.`
      : 'Select an eligible remote host to continue.'
    : 'This VM will be created locally on EpicVM Server.'

  useEffect(()=>{
    if(placement !== 'remote'){
      if(selectedHostId) setSelectedHostId('')
      if(invalidatedHostId) setInvalidatedHostId('')
      return
    }
    const eligibleIds = new Set(eligibleRemoteHosts.map(host => host.id))
    if(selectedHostId && !eligibleIds.has(selectedHostId)){
      setInvalidatedHostId(selectedHostId)
      setSelectedHostId('')
      return
    }
    if(invalidatedHostId && eligibleIds.has(invalidatedHostId) && !selectedHostId){
      setSelectedHostId(invalidatedHostId)
      setInvalidatedHostId('')
    }
  }, [eligibleRemoteHosts, invalidatedHostId, placement, selectedHostId])

  function choosePlacement(nextPlacement){
    setPlacement(nextPlacement)
    setInvalidatedHostId('')
    if(nextPlacement === 'remote') setSelectedHostId(eligibleRemoteHosts[0]?.id || '')
    else setSelectedHostId('')
  }

  function chooseRemoteHost(nextHostId){
    setSelectedHostId(nextHostId)
    setInvalidatedHostId('')
  }

  return (
    <div>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
      <div className="vm-page-hero glass-card">
        <div>
          <div className="eyebrow">Dashboard v2</div>
          <h1 style={{margin:'8px 0 10px'}}>VM Control Center</h1>
          <div style={{color:'var(--muted)', maxWidth:760}}>Create new VMs, manage running ones, open consoles, and edit per-VM presentation settings like custom domain, tab title, and favicon without touching the old dashboard.</div>
        </div>
        <div style={{display:'flex', alignItems:'center', gap:14, flexWrap:'wrap'}}>
          {refreshing && (
            <div className="vm-refresh-banner">
              <span className="vm-mini-spinner" />
              <span>Refreshing fleet…</span>
            </div>
          )}
          <div className="vm-summary-grid">
            <div className="summary-pill"><strong>{summary.total}</strong><span>Total</span></div>
            <div className="summary-pill live"><strong>{summary.live}</strong><span>Running</span></div>
            <div className="summary-pill warn"><strong>{summary.busy}</strong><span>Busy</span></div>
            <div className="summary-pill danger"><strong>{summary.down}</strong><span>Down</span></div>
          </div>
        </div>
      </div>

      <div className="glass-card" style={{marginTop:16}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12, flexWrap:'wrap'}}>
          <div style={{color:'var(--muted)', maxWidth:640}}>Use the create form to spin up new VMs. Each VM card now also has a Manage flow for deleting the VM, changing its custom domain/host override, tab title, and favicon.</div>
          <Button onClick={()=>load({ silent:true })}>Refresh</Button>
        </div>

        <form onSubmit={enrollRemoteHost} className="glass-card" style={{marginTop:16, padding:18}}>
          <div style={{display:'flex', justifyContent:'space-between', gap:12, alignItems:'baseline', flexWrap:'wrap'}}>
            <div>
              <h2 style={{margin:'0 0 6px'}}>Connect a RemoteVM host</h2>
              <div style={{color:'var(--muted)', maxWidth:760}}>Upload the protected Windows agent token directly through this authenticated dashboard. The token is stored server-side and is never returned to the browser.</div>
            </div>
            <div className="vm-meta-chip">Tailscale only</div>
          </div>
          <div className="vm-create-fields" style={{marginTop:14}}>
            <label className="vm-placement-field">
              <span>Host ID</span>
              <input value={enrollmentDraft.hostId} onChange={e=>setEnrollmentDraft(s=>({ ...s, hostId:e.target.value }))} placeholder="epic-pc" pattern="[a-z0-9][a-z0-9._\-]{0,62}" required />
            </label>
            <label className="vm-placement-field">
              <span>Display name</span>
              <input value={enrollmentDraft.displayName} onChange={e=>setEnrollmentDraft(s=>({ ...s, displayName:e.target.value }))} placeholder="Epic Windows PC" required />
            </label>
            <label className="vm-placement-field">
              <span>Tailscale agent URL</span>
              <input value={enrollmentDraft.agentUrl} onChange={e=>setEnrollmentDraft(s=>({ ...s, agentUrl:e.target.value }))} placeholder="http://100.64.x.x:8765" required />
            </label>
            <label className="vm-placement-field">
              <span>Protected token file</span>
              <input ref={enrollmentFileInputRef} type="file" accept=".token,.txt,text/plain" onChange={e=>setEnrollmentFile(e.target.files?.[0] || null)} required />
            </label>
            <Button type="submit" disabled={enrollmentBusy || !enrollmentFile}>{enrollmentBusy ? 'Uploading…' : 'Upload and connect'}</Button>
          </div>
        </form>

        <form onSubmit={createVm} className="vm-create-form">
          <div className="vm-create-fields">
            <label className="vm-placement-field vm-name-field">
              <span>VM name</span>
              <input value={createName} onChange={e=>setCreateName(e.target.value)} placeholder="new vm name (e.g. alpha)" pattern="[a-z0-9][a-z0-9._\-]{0,62}" required />
            </label>
            <label className="vm-placement-field">
              <span>VM location</span>
              <select value={placement} onChange={e=>choosePlacement(e.target.value)}>
                <option value="local">Local VM — EpicVM Server</option>
                <option value="remote" disabled={!remotePlacementAvailable}>Remote VM{!remotePlacementAvailable ? ' — No remote hosts connected' : ''}</option>
              </select>
            </label>
            {placement === 'remote' ? (
              <label className="vm-placement-field vm-remote-host-field">
                <span>Remote host</span>
                <select value={selectedHostId} onChange={e=>chooseRemoteHost(e.target.value)} disabled={!eligibleRemoteHosts.length || createBusy}>
                  <option value="">Select an eligible host</option>
                  {eligibleRemoteHosts.map(host => <option key={host.id} value={host.id}>{hostOptionLabel(host)}</option>)}
                </select>
              </label>
            ) : null}
            {placement === 'remote' ? (
              <label className="vm-placement-field">
                <span>Provisioning profile</span>
                <select value={provisioningProfile} onChange={e=>setProvisioningProfile(e.target.value)} disabled={createBusy}>
                  <option value="standard">Standard · 4 vCPU · 8 GB · 96 GB</option>
                  <option value="gaming" disabled>Gaming · disabled until GPU-P pilot</option>
                </select>
              </label>
            ) : null}
            <Button type="submit" disabled={createBusy || !!placementReason}>{createBusy ? 'Creating…' : 'Create VM'}</Button>
          </div>
          <div className="vm-placement-summary">
            <span>Destination</span>
            <strong>{destinationSummary}</strong>
          </div>
          {!remotePlacementAvailable ? <div className="vm-placement-notice">Remote VM unavailable: No remote hosts connected.</div> : null}
          {placementReason ? <div id="vm-placement-reason" className="vm-placement-error" role="alert">{placementReason}</div> : null}
          {provisioningJob ? (
            <div className="vm-placement-notice" style={{marginTop:12}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:12,flexWrap:'wrap'}}>
                <strong>Provisioning: {provisioningJob.name}</strong>
                <span>{provisioningJob.state}</span>
              </div>
              <div style={{height:8,background:'rgba(255,255,255,.1)',borderRadius:999,marginTop:10,overflow:'hidden'}}><div style={{height:'100%',width:`${provisioningProgress(provisioningJob)}%`,background:'#22c55e',transition:'width .25s'}} /></div>
              {canClaimProvisioningJob(provisioningJob) && provisioningClaimToken ? (
                <div style={{display:'grid',gap:8,marginTop:12}}>
                  <strong>One-time guest claim</strong>
                  <span style={{color:'var(--muted)',fontSize:13}}>Use an administrator username and password for this guest. The claim is single-use and is not shown again.</span>
                  <input value={claimDraft.username} onChange={e=>setClaimDraft(s=>({...s,username:e.target.value}))} placeholder="Guest admin username" autoComplete="username" required />
                  <input value={claimDraft.password} onChange={e=>setClaimDraft(s=>({...s,password:e.target.value}))} placeholder="Guest admin password" type="password" autoComplete="new-password" minLength={12} required />
                  <input value={claimDraft.confirm} onChange={e=>setClaimDraft(s=>({...s,confirm:e.target.value}))} placeholder="Repeat password" type="password" autoComplete="new-password" minLength={12} required />
                  <Button type="button" onClick={claimProvisioningJob} disabled={provisioningBusy}>{provisioningBusy ? 'Claiming…' : 'Claim guest securely'}</Button>
                </div>
              ) : null}
              {canRetryProvisioningConsole(provisioningJob) ? (
                <div style={{display:'grid',gap:8,marginTop:12}}>
                  <strong>Retry retained console</strong>
                  <span style={{color:'var(--muted)',fontSize:13}}>The VM was retained. Re-enter its credentials to rebuild only the stopped console bundle.</span>
                  <input value={claimDraft.username} onChange={e=>setClaimDraft(s=>({...s,username:e.target.value}))} placeholder="Guest administrator" autoComplete="username" required />
                  <input value={claimDraft.password} onChange={e=>setClaimDraft(s=>({...s,password:e.target.value}))} placeholder="Guest password" type="password" autoComplete="current-password" minLength={12} required />
                  <input value={claimDraft.confirm} onChange={e=>setClaimDraft(s=>({...s,confirm:e.target.value}))} placeholder="Repeat password" type="password" autoComplete="current-password" minLength={12} required />
                  <Button type="button" onClick={retryProvisioningConsole} disabled={provisioningBusy}>{provisioningBusy ? 'Retrying…' : 'Retry console securely'}</Button>
                </div>
              ) : null}
              {canOpenProvisionedVm(provisioningJob) ? <div style={{color:'#86efac',marginTop:10}}>Ready. The VM will appear in the fleet after the next refresh.</div> : null}
              {provisioningJob.state === 'failed' ? <div role="alert" style={{color:'#fca5a5',marginTop:10}}>Guest setup stopped safely. The VM was retained for diagnosis.</div> : null}
              {provisioningJob.state === 'console_failed' ? <div role="alert" style={{color:'#fca5a5',marginTop:10}}>Console setup stopped safely. Its route is down and diagnostic data was retained.</div> : null}
            </div>
          ) : null}
        </form>

        <div style={{marginTop:18}}>
          {initialLoading && instances.length === 0 ? (
            <div className="vm-card-grid">
              {Array.from({ length:4 }).map((_, i)=><div key={i} className="skeleton" style={{height:220,borderRadius:20}} />)}
            </div>
          ) : instances.length === 0 ? (
            <div className="vm-empty-state">No VMs found. Incredible. A VM manager with nothing to manage.</div>
          ) : (
            <div className="vm-card-grid">
              {instances.map(vm => {
                const hostId = vm.host_id || 'local'
                return <VmCard key={`${hostId}:${vm.name}`} vm={vm} host={hostsById[hostId]} onAction={(cmd, name, opts={})=>action(cmd, name, { ...opts, hostId })} onDetails={(name)=>openDetails(name, hostId, vm.url)} onManage={(name)=>openManage(name, hostId)} onTeardown={startTeardown} onProfileChange={setProfile} profileBusy={profileBusy === vm.name} busyAction={!!busyAction} refreshing={refreshing} />
              })}
            </div>
          )}
        </div>
      </div>

      <Modal open={!!selected} title={`VM: ${selected}`} onClose={()=>setSelected(null)} width={1180}>
        <div style={{display:'flex',gap:12, flexWrap:'wrap'}}>
          <div style={{flex:'1 1 620px'}}>
            <iframe title={`VM ${selected}`} src={selectedVmUrl || `/dashboard/vm/${encodeURIComponent(selected)}/`} style={{width:'100%',height:360,border:'1px solid rgba(255,255,255,0.04)', background:'#020617'}} />
            {selectedVmHostId === 'local' ? <div style={{marginTop:12}}>
              <VmExec vmName={selected} />
            </div> : <div className="vm-placement-notice" style={{marginTop:12}}>Remote console is served by the selected host URL.</div>}
          </div>
          <div style={{width:420,maxWidth:'100%',display:'flex',flexDirection:'column',gap:8}}>
            <div style={{fontSize:13,color:'var(--muted)'}}>Console / Logs</div>
            <div style={{background:'#02040a',color:'#dff',padding:12,borderRadius:12,height:460,overflow:'auto',fontFamily:'monospace',fontSize:12,border:'1px solid rgba(255,255,255,0.04)'}}>
              {logLoading ? <div>Loading logs…</div> : <pre style={{whiteSpace:'pre-wrap',margin:0}}>{logs}</pre>}
            </div>
            <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
              <Button onClick={()=>fetchLogs(selected)}>Refresh Logs</Button>
              <a href={selectedVmUrl || `/dashboard/vm/${encodeURIComponent(selected)}/`} target="_blank" rel="noreferrer"><Button>Open in new tab</Button></a>
            </div>
          </div>
        </div>
      </Modal>

      <Modal open={!!manageVm} title={`Manage VM: ${manageVm}`} onClose={()=>setManageVm(null)} width={760}>
        <div style={{display:'grid', gap:14}}>
          <div style={{color:'var(--muted)'}}>{manageVmHostId === 'local' ? 'Edit the custom host/domain this VM uses, the browser tab title shown in the wrapper, and optionally upload a per-VM favicon.' : 'RemoteVM settings are managed on the host agent. Delete remains available here; presentation settings are local-only for now.'}</div>
          <label style={{display:'grid', gap:6}}>
            <span>Custom domain / host override</span>
            <input value={manageDraft.hostOverride || ''} onChange={e=>setManageDraft(s => ({ ...s, hostOverride: e.target.value }))} placeholder="vm42.example.com (leave blank to use default)" style={{background:'rgba(2,6,23,.7)', color:'#fff', border:'1px solid rgba(255,255,255,.12)', borderRadius:12, padding:'12px 14px'}} />
          </label>
          <label style={{display:'grid', gap:6}}>
            <span>Browser tab title</span>
            <input value={manageDraft.title || ''} onChange={e=>setManageDraft(s => ({ ...s, title: e.target.value }))} placeholder="My Cool VM" style={{background:'rgba(2,6,23,.7)', color:'#fff', border:'1px solid rgba(255,255,255,.12)', borderRadius:12, padding:'12px 14px'}} />
          </label>
          <label style={{display:'grid', gap:6}}>
            <span>Access mode</span>
            <select value={manageDraft.accessMode || 'public'} onChange={e=>setManageDraft(s => ({ ...s, accessMode: e.target.value }))} style={{background:'rgba(2,6,23,.7)', color:'#fff', border:'1px solid rgba(255,255,255,.12)', borderRadius:12, padding:'12px 14px'}}>
              <option value="public">Public</option>
              <option value="restricted">Restricted (login + assignment required)</option>
            </select>
          </label>
          {manageDraft.accessMode === 'restricted' ? (
            <div style={{color:'var(--muted)'}}>Users currently assigned to this VM: {(manageDraft.assignedUsers || []).length ? manageDraft.assignedUsers.join(', ') : 'none yet'} — edit assignments from the Users & Access page.</div>
          ) : null}
          <label style={{display:'grid', gap:6}}>
            <span>VM favicon / tab icon</span>
            <input type="file" accept=".ico,image/x-icon,image/png,image/webp,image/jpeg" onChange={e=>setFaviconFile(e.target.files?.[0] || null)} style={{background:'rgba(2,6,23,.7)', color:'#fff', border:'1px solid rgba(255,255,255,.12)', borderRadius:12, padding:'12px 14px'}} />
          </label>
          {manageDraft.faviconUrl ? (
            <div style={{display:'flex', alignItems:'center', gap:10, color:'var(--muted)'}}>
              <img src={`${manageDraft.faviconUrl}?v=${Date.now()}`} alt="VM favicon" style={{width:20,height:20,borderRadius:4}} />
              <span>Existing favicon detected for this VM.</span>
            </div>
          ) : null}
          <div style={{display:'flex', gap:10, flexWrap:'wrap'}}>
            <Button onClick={saveManageSettings} disabled={manageBusy || manageVmHostId !== 'local'}>{manageBusy ? 'Saving…' : 'Save VM settings'}</Button>
            <Button onClick={()=>deleteVm(manageVm, manageVmHostId)} disabled={manageBusy} style={{background:'linear-gradient(135deg,#ef4444,#b91c1c)', color:'#fff'}}>Delete VM</Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
