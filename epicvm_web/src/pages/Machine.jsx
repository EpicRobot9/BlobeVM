import React, { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Desktop, WindowsLogo, GameController, Power, Plug, ArrowClockwise, Stop, Spinner, CaretLeft } from '@phosphor-icons/react'
import { myVms, startVm, stopVm, restartVm } from '../api'

const TYPE_META = {
  linux: { label: 'Linux VM', icon: Desktop, cls: 't-linux', tag: 'BETA DEFAULT' },
  windows: { label: 'Windows VM', icon: WindowsLogo, cls: 't-windows', tag: 'LIMITED' },
  gaming: { label: 'Gaming VM', icon: GameController, cls: 't-gaming', tag: 'EXPERIMENTAL' },
}

function readinessBadge(r) {
  const map = { ready: ['READY', 'rb-ready'], provisioning: ['PREPARING', 'rb-work'], stopped: ['OFFLINE', 'rb-off'], stopping: ['SHUTTING DOWN', 'rb-work'], failed: ['FAILED', 'rb-bad'] }
  const [txt, cls] = map[r] || ['UNKNOWN', 'rb-off']
  return <span className={`evm-rbadge ${cls}`}>{txt}</span>
}

export default function Machine({ user, onSignout }) {
  const { name } = useParams()
  const navigate = useNavigate()
  const [vm, setVm] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')

  const load = () => {
    myVms().then((res) => {
      if (!res.ok) { setLoading(false); return }
      const found = (res.body.vms || []).find((v) => v.name === name)
      setVm(found || null)
      setLoading(false)
    }).catch(() => setLoading(false))
  }
  useEffect(() => { load() }, [name])

  async function act(kind) {
    setBusy(kind)
    let res
    if (kind === 'start') res = await startVm(name)
    else if (kind === 'stop') res = await stopVm(name)
    else if (kind === 'restart') res = await restartVm(name)
    setBusy('')
    if (res && res.ok) { load(); if (kind === 'start' && res.body && res.body.wrapperUrl) window.location.assign(res.body.wrapperUrl) }
    else if (res) alert(res.body.error || 'Action failed')
  }

  if (loading) return <div className="evm-page evm-portal"><div className="evm-portal-loading"><Spinner size={26} className="spin" /> Loading…</div></div>
  if (!vm) return (
    <div className="evm-page evm-portal">
      <div className="evm-empty"><h3>Machine not found</h3><p>You don't have access to “{name}”.</p>
        <button className="evm-btn evm-btn-primary" onClick={() => navigate('/portal')}>Back to portal</button></div>
    </div>
  )

  const tm = TYPE_META[vm.type] || TYPE_META.linux
  const Icon = tm.icon
  const ready = vm.readiness === 'ready'
  const provisioning = vm.readiness === 'provisioning'

  return (
    <div className="evm-page evm-portal">
      <header className="evm-portal-head">
        <div className="evm-ph-left">
          <button className="evm-link-btn" onClick={() => navigate('/portal')}><CaretLeft size={18} /> Portal</button>
          <span className="evm-brand-mark sm">E</span>
        </div>
        <div className="evm-ph-right"><button className="evm-btn evm-btn-ghost evm-btn-sm" onClick={onSignout}>Sign out</button></div>
      </header>

      <article className={`evm-machine ${tm.cls}`}>
        <div className="evm-card-cut" />
        <div className="evm-m-top">
          <span className={`evm-type-tag ${tm.cls}`}><Icon size={14} /> {tm.tag}</span>
          {readinessBadge(vm.readiness)}
        </div>
        <h1 className="evm-m-name">{vm.name}</h1>
        <div className="evm-m-os"><Icon size={18} /> {tm.label}{vm.os ? ` · ${vm.os}` : ''}</div>

        <div className="evm-m-grid">
          <div className="evm-m-cell"><span>STATUS</span><b>{vm.status || vm.state || '—'}</b></div>
          <div className="evm-m-cell"><span>READINESS</span><b>{vm.readiness}</b></div>
          <div className="evm-m-cell"><span>ACCESS</span><b>{vm.accessMode}</b></div>
          <div className="evm-m-cell"><span>RESOURCES</span><b>{vm.cpu ? `${vm.cpu} / ${vm.memory}` : '—'}</b></div>
        </div>

        <div className="evm-m-actions">
          {ready ? (
            <a className="evm-btn evm-btn-primary evm-btn-lg" href={vm.wrapperUrl || vm.url}>CONNECT <Plug size={20} /></a>
          ) : provisioning ? (
            <button className="evm-btn evm-btn-ghost evm-btn-lg" disabled><Spinner size={18} className="spin" /> PREPARING…</button>
          ) : (
            <button className="evm-btn evm-btn-ghost evm-btn-lg" disabled><Plug size={20} /> NOT READY</button>
          )}
          <button className="evm-btn evm-btn-block" disabled={busy === 'start' || provisioning} onClick={() => act('start')}>
            {busy === 'start' ? <Spinner size={16} className="spin" /> : <Power size={16} />} START
          </button>
          <button className="evm-btn evm-btn-block" disabled={busy === 'restart'} onClick={() => act('restart')}>
            {busy === 'restart' ? <Spinner size={16} className="spin" /> : <ArrowClockwise size={16} />} RESTART
          </button>
          <button className="evm-btn evm-btn-block evm-btn-danger" disabled={busy === 'stop'} onClick={() => act('stop')}>
            {busy === 'stop' ? <Spinner size={16} className="spin" /> : <Stop size={16} />} STOP
          </button>
        </div>

        {vm.type === 'gaming' && (
          <p className="evm-m-note">Gaming VMs are experimental and GPU capacity is limited. Performance may vary during the private beta.</p>
        )}
      </article>

      <footer className="evm-portal-foot">
        <span>Need help? Message Epic and mention “{vm.name}”.</span>
        <a href="https://techexplore.us/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
      </footer>
    </div>
  )
}
