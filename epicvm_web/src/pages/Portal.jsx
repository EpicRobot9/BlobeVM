import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Desktop, WindowsLogo, GameController, Power, Plug, ArrowClockwise, Stop, Spinner, Plus, CaretDown, Laptop } from '@phosphor-icons/react'
import { myVms, logout, startVm, stopVm, restartVm, addCloudPc, pairCloudPc } from '../api'

const TYPE_META = {
  linux: { label: 'Linux VM', icon: Desktop, cls: 't-linux', tag: 'BETA DEFAULT' },
  windows: { label: 'Windows VM', icon: WindowsLogo, cls: 't-windows', tag: 'LIMITED' },
  gaming: { label: 'Gaming VM', icon: GameController, cls: 't-gaming', tag: 'EXPERIMENTAL' },
  cloudpc: { label: 'Cloud PC', icon: Laptop, cls: 't-cloudpc', tag: 'YOUR PC' },
}

function readinessBadge(r) {
  const map = {
    ready: ['READY', 'rb-ready'],
    provisioning: ['PREPARING', 'rb-work'],
    offline: ['PC OFFLINE', 'rb-off'],
    stopped: ['OFFLINE', 'rb-off'],
    stopping: ['SHUTTING DOWN', 'rb-work'],
    failed: ['FAILED', 'rb-bad'],
  }
  const [txt, cls] = map[r] || ['UNKNOWN', 'rb-off']
  return <span className={`evm-rbadge ${cls}`}>{txt}</span>
}

export default function Portal({ user, onSignout }) {
  const navigate = useNavigate()
  const [vms, setVms] = useState(null)
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const [showAddPc, setShowAddPc] = useState(false)
  const [addPc, setAddPc] = useState({ displayName: '', tailnetIp: '', sunshineUsername: '', sunshinePassword: '' })
  const [addPcBusy, setAddPcBusy] = useState(false)
  const [addPcErr, setAddPcErr] = useState('')

  const load = () => {
    setLoading(true)
    myVms().then((res) => {
      if (!res.ok) { setErr('Could not load your machines.'); setLoading(false); return }
      setVms(res.body.vms || [])
      setSummary(res.body.summary || null)
      setLoading(false)
    }).catch(() => { setErr('Could not load your machines.'); setLoading(false) })
  }

  useEffect(() => { load() }, [])

  async function signout() {
    await logout().catch(() => {})
    window.location.assign('/EpicVM/')
  }

  const [openName, setOpenName] = useState(null)
  const [busy, setBusy] = useState('')
  const [pairBusy, setPairBusy] = useState('')

  const toggleManage = (n) => setOpenName((o) => (o === n ? null : n))

  async function act(kind, vmName) {
    setBusy(vmName + ':' + kind)
    let res
    if (kind === 'start') res = await startVm(vmName)
    else if (kind === 'stop') res = await stopVm(vmName)
    else if (kind === 'restart') res = await restartVm(vmName)
    setBusy('')
    if (res && res.ok) { load() } else if (res) alert(res.body.error || 'Action failed')
  }

  async function submitAddPc(e) {
    e.preventDefault()
    setAddPcBusy(true)
    setAddPcErr('')
    const res = await addCloudPc({
      displayName: addPc.displayName.trim(),
      tailnetIp: addPc.tailnetIp.trim(),
      sunshineUsername: addPc.sunshineUsername.trim(),
      sunshinePassword: addPc.sunshinePassword,
    })
    setAddPcBusy(false)
    if (res.ok) {
      setShowAddPc(false)
      setAddPc({ displayName: '', tailnetIp: '', sunshineUsername: '', sunshinePassword: '' })
      load()
    } else {
      setAddPcErr(res.body.error || 'Could not add your PC')
    }
  }

  async function pairPc(vm) {
    setPairBusy(vm.name)
    const su = window.prompt('Sunshine username on your PC (used once to pair):')
    if (su === null) { setPairBusy(''); return }
    const sp = window.prompt('Sunshine password on your PC (used once to pair):')
    if (sp === null) { setPairBusy(''); return }
    const res = await pairCloudPc(vm.name, { sunshineUsername: su, sunshinePassword: sp })
    setPairBusy('')
    if (res.ok) { load() } else { alert(res.body.error || 'Pairing failed') }
  }

  const username = user?.username || 'user'

  return (
    <div className="evm-page evm-portal">
      <header className="evm-portal-head">
        <div className="evm-ph-left">
          <span className="evm-brand">
            <span className="evm-brand-mark">E</span> EpicVM
          </span>
          <span className="evm-beta-pill">BETA</span>
        </div>
        <div className="evm-ph-right">
          <span className="evm-who">@{username}</span>
          <button className="evm-btn evm-btn-ghost evm-btn-sm" onClick={signout}>Sign out</button>
        </div>
      </header>

      <section className="evm-hero2">
        <div className="evm-hero2-main">
          <h1 className="evm-hero2-title">YOUR<br />MACHINES</h1>
          <p className="evm-hero2-sub">Welcome back, {username}. This is your EpicVM control center.</p>
        </div>
        <div className="evm-summary">
          {summary ? (
            <>
              <div className="evm-stat"><b>{summary.total}</b><span>TOTAL</span></div>
              <div className="evm-stat s-ready"><b>{summary.ready}</b><span>READY</span></div>
              <div className="evm-stat s-work"><b>{summary.provisioning}</b><span>PREPARING</span></div>
              <div className="evm-stat s-off"><b>{summary.stopped}</b><span>OFFLINE</span></div>
            </>
          ) : <div className="evm-stat"><b>–</b><span>…</span></div>}
        </div>
      </section>

      {err && <div className="evm-error">{err}</div>}

      {loading && !vms ? (
        <div className="evm-portal-loading"><Spinner size={28} className="spin" /> Loading your machines…</div>
      ) : (
        <section className="evm-grid">
          {(vms || []).map((vm) => {
            const tm = TYPE_META[vm.type] || TYPE_META.linux
            const Icon = tm.icon
            const ready = vm.readiness === 'ready'
            const provisioning = vm.readiness === 'provisioning'
            const open = openName === vm.name
            const isCloudPc = vm.type === 'cloudpc'
            return (
              <article key={vm.name} className={`evm-card ${tm.cls}`}>
                <div className="evm-m-top">
                  <span className={`evm-type-tag ${tm.cls}`}><Icon size={14} /> {tm.tag}</span>
                  {readinessBadge(vm.readiness)}
                </div>
                <h2 className="evm-card-name">{vm.name}</h2>
                <div className="evm-card-os"><Icon size={16} /> {tm.label}{vm.os ? ` · ${vm.os}` : ''}</div>
                <p className="evm-card-desc">
                  {vm.type === 'linux' && 'Your personal Linux environment for coding, hosting, automation, and dev work.'}
                  {vm.type === 'windows' && 'A full Windows desktop for apps and workflows that need Windows.'}
                  {vm.type === 'gaming' && 'GPU-accelerated Windows built for remote gaming and GPU workloads.'}
                  {vm.type === 'cloudpc' && 'Your own PC, streamed over the web. No agent required — powered by Sunshine + Moonlight.'}
                </p>
                <div className="evm-card-meta">
                  {vm.cpu ? <span>CPU {vm.cpu}</span> : null}
                  {vm.memory ? <span>RAM {vm.memory}</span> : null}
                  {!vm.cpu && !vm.memory ? <span>Cloud computer</span> : null}
                </div>

                <div className="evm-card-actions">
                  {ready ? (
                    <a className="evm-btn evm-btn-primary evm-btn-block" href={vm.wrapperUrl || vm.url}>CONNECT <Plug size={18} /></a>
                  ) : provisioning ? (
                    <button className="evm-btn evm-btn-ghost evm-btn-block" disabled><Spinner size={16} className="spin" /> PREPARING…</button>
                  ) : (
                    <button className="evm-btn evm-btn-ghost evm-btn-block" disabled><Plug size={18} /> NOT READY</button>
                  )}
                  <button
                    className={`evm-btn evm-btn-sm evm-manage-btn ${open ? 'is-open' : ''}`}
                    onClick={() => toggleManage(vm.name)}
                    aria-expanded={open}
                  >
                    {open ? 'CLOSE' : 'MANAGE'} <CaretDown size={16} className={`evm-caret ${open ? 'up' : ''}`} />
                  </button>
                </div>
                <div className={`evm-card-manage-panel ${open ? 'open' : ''}`}>
                  {isCloudPc && !vm.paired && (
                    <button className="evm-btn evm-btn-block evm-mp-btn evm-btn-warn" disabled={pairBusy === vm.name} onClick={() => pairPc(vm)}>
                      {pairBusy === vm.name ? <Spinner size={16} className="spin" /> : null} PAIR
                    </button>
                  )}
                  <button className="evm-btn evm-btn-block evm-mp-btn" disabled={busy === vm.name + ':start' || provisioning} onClick={() => act('start', vm.name)}>
                    {busy === vm.name + ':start' ? <Spinner size={16} className="spin" /> : <Power size={16} />} START
                  </button>
                  <button className="evm-btn evm-btn-block evm-mp-btn" disabled={busy === vm.name + ':restart'} onClick={() => act('restart', vm.name)}>
                    {busy === vm.name + ':restart' ? <Spinner size={16} className="spin" /> : <ArrowClockwise size={16} />} RESTART
                  </button>
                  <button className="evm-btn evm-btn-block evm-mp-btn evm-btn-danger" disabled={busy === vm.name + ':stop'} onClick={() => act('stop', vm.name)}>
                    {busy === vm.name + ':stop' ? <Spinner size={16} className="spin" /> : <Stop size={16} />} STOP
                  </button>
                  <a className="evm-mp-details" href={`/EpicVM/vm/${encodeURIComponent(vm.name)}/`} onClick={(e) => { e.preventDefault(); window.location.assign(`/EpicVM/vm/${encodeURIComponent(vm.name)}/`) }}>Open console →</a>
                </div>
              </article>
            )
          })}
          {/* Connect your PC CTA */}
          <button className="evm-card evm-add-card" onClick={() => setShowAddPc(true)}>
            <Plus size={32} />
            <h3>Connect your PC</h3>
            <p>Stream your own Sunshine-enabled PC. No agent to install.</p>
          </button>
          {!loading && vms && vms.length === 0 && (
            <div className="evm-empty">
              <Plus size={32} />
              <h3>No machines yet</h3>
              <p>Your account is approved, but EpicVM hasn't finished preparing a machine. Check back shortly.</p>
            </div>
          )}
        </section>
      )}

      {showAddPc && (
        <div className="evm-modal-backdrop" onClick={() => setShowAddPc(false)}>
          <div className="evm-modal" onClick={(e) => e.stopPropagation()}>
            <h2>Connect your PC</h2>
            <p className="evm-modal-sub">Your PC must already run <b>Sunshine</b> and be on the same Tailscale network as EpicVM.</p>
            <form onSubmit={submitAddPc}>
              <label>Display name</label>
              <input value={addPc.displayName} onChange={(e) => setAddPc({ ...addPc, displayName: e.target.value })} placeholder="My Rig" required />
              <label>Tailscale IP of your PC</label>
              <input value={addPc.tailnetIp} onChange={(e) => setAddPc({ ...addPc, tailnetIp: e.target.value })} placeholder="100.x.x.x" required />
              <label>Sunshine username (optional — to auto-pair now)</label>
              <input value={addPc.sunshineUsername} onChange={(e) => setAddPc({ ...addPc, sunshineUsername: e.target.value })} placeholder="sunshine" autoComplete="username" />
              <label>Sunshine password (optional)</label>
              <input type="password" value={addPc.sunshinePassword} onChange={(e) => setAddPc({ ...addPc, sunshinePassword: e.target.value })} placeholder="••••" autoComplete="current-password" />
              {addPcErr && <div className="evm-error">{addPcErr}</div>}
              <div className="evm-modal-actions">
                <button type="button" className="evm-btn evm-btn-ghost" onClick={() => setShowAddPc(false)}>Cancel</button>
                <button type="submit" className="evm-btn evm-btn-primary" disabled={addPcBusy}>
                  {addPcBusy ? <Spinner size={16} className="spin" /> : null} Add PC
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <footer className="evm-portal-foot">
        <span>Beta · use at your own risk</span>
        <a href="https://techexplore.us/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
      </footer>
    </div>
  )
}
