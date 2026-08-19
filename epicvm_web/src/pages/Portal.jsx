import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Desktop, WindowsLogo, GameController, Power, Plug, ArrowClockwise, Stop, Spinner, Plus } from '@phosphor-icons/react'
import { myVms, logout } from '../api'

const TYPE_META = {
  linux: { label: 'Linux VM', icon: Desktop, cls: 't-linux', tag: 'BETA DEFAULT' },
  windows: { label: 'Windows VM', icon: WindowsLogo, cls: 't-windows', tag: 'LIMITED' },
  gaming: { label: 'Gaming VM', icon: GameController, cls: 't-gaming', tag: 'EXPERIMENTAL' },
}

function readinessBadge(r) {
  const map = {
    ready: ['READY', 'rb-ready'],
    provisioning: ['PREPARING', 'rb-work'],
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

  const username = user?.username || 'user'

  return (
    <div className="evm-page evm-portal">
      <header className="evm-portal-head">
        <div className="evm-ph-left">
          <span className="evm-brand">
            <span className="evm-brand-mark">E</span> EpicVM
          </span>
          <span className="evm-beta-pill">PRIVATE BETA</span>
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
            return (
              <article key={vm.name} className={`evm-card ${tm.cls}`}>
                <div className="evm-card-cut" />
                <div className="evm-card-top">
                  <span className={`evm-type-tag ${tm.cls}`}><Icon size={14} /> {tm.tag}</span>
                  {readinessBadge(vm.readiness)}
                </div>
                <h2 className="evm-card-name">{vm.name}</h2>
                <div className="evm-card-os"><Icon size={16} /> {tm.label}{vm.os ? ` · ${vm.os}` : ''}</div>
                <p className="evm-card-desc">
                  {vm.type === 'linux' && 'Your personal Linux environment for coding, hosting, automation, and dev work.'}
                  {vm.type === 'windows' && 'A full Windows desktop for apps and workflows that need Windows.'}
                  {vm.type === 'gaming' && 'GPU-accelerated Windows built for remote gaming and GPU workloads.'}
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
                  <button className="evm-btn evm-btn-sm" onClick={() => navigate(`/portal/${encodeURIComponent(vm.name)}`)}>MANAGE</button>
                </div>
              </article>
            )
          })}
          {!loading && vms && vms.length === 0 && (
            <div className="evm-empty">
              <Plus size={32} />
              <h3>No machines yet</h3>
              <p>Your account is approved, but EpicVM hasn't finished preparing a machine. Check back shortly.</p>
            </div>
          )}
        </section>
      )}

      <footer className="evm-portal-foot">
        <span>Private beta · use at your own risk</span>
        <a href="https://techexplore.us/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
      </footer>
    </div>
  )
}
