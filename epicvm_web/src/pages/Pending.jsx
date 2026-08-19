import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Hourglass, CheckCircle, XCircle, Spinner, ArrowRight, Desktop } from '@phosphor-icons/react'
import { myVms, logout } from '../api'

const STATE_COPY = {
  pending: {
    icon: Hourglass, tone: 'warn', title: 'Access Request Received',
    body: "Your EpicVM account is waiting for approval. We'll review your request and provision your machine once approved. This usually takes a little while — thanks for your patience.",
  },
  approved: {
  icon: CheckCircle, tone: 'ok', title: 'You’re Approved',
  body: 'Your account is approved. EpicVM is preparing your machine. Once it is ready you can open the VM Portal from here.',
  },
  creating: {
    icon: Spinner, tone: 'warn', title: 'Your Machine Is Being Created',
    body: 'EpicVM is provisioning your cloud computer. This can take a few minutes. You can check back here or open the portal shortly.',
  },
  ready: {
    icon: CheckCircle, tone: 'ok', title: 'Your Machine Is Ready',
    body: 'Your VM is ready. Open the VM Portal to connect and manage your machine.',
  },
  rejected: {
    icon: XCircle, tone: 'bad', title: 'Request Not Approved',
    body: "Your access request wasn't approved this time. Thanks for your interest — you're welcome to reach out if you'd like to discuss it.",
  },
  failed: {
    icon: XCircle, tone: 'bad', title: 'Provisioning Hit A Snag',
    body: 'Your account is approved, but automatic provisioning ran into an issue. The admin has been notified; please check back shortly.',
  },
}

export default function Pending({ user }) {
  const navigate = useNavigate()
  const [vms, setVms] = useState(null)
  const [loading, setLoading] = useState(false)

  const status = (user && user.accountStatus) || 'pending'
  const provState = (user && user.provisioningState) || null

  // When approved, poll the portal VM list to surface ready state.
  useEffect(() => {
    if (status !== 'approved') return
    let live = true
    const tick = () => {
      setLoading(true)
      myVms().then((res) => {
        if (!live) return
        if (res.ok && Array.isArray(res.body.vms)) setVms(res.body.vms)
        setLoading(false)
      }).catch(() => live && setLoading(false))
    }
    tick()
    const t = window.setInterval(tick, 8000)
    return () => { live = false; window.clearInterval(t) }
  }, [status])

  const readyVm = Array.isArray(vms) ? vms.find((v) => String(v.status || '').toLowerCase() === 'running' || v.running) : null
  const key = status === 'approved' ? (readyVm ? 'ready' : (provState || 'creating')) : (status === 'pending' ? 'pending' : status)
  const copy = STATE_COPY[key] || STATE_COPY.pending
  const Icon = copy.icon

  async function signout() {
    await logout().catch(() => {})
    window.location.assign('/EpicVM/')
  }

  return (
    <div className="evm-page evm-auth">
      <div className="evm-auth-card evm-pending">
        <span className={`evm-status-badge ${copy.tone}`}><Icon size={20} /> {status === 'approved' ? (readyVm ? 'Ready' : 'Approved') : status}</span>
        <h1 className="evm-h1 sm">{copy.title}</h1>
        <p className="evm-body">{copy.body}</p>

        {user && (
          <div className="evm-account">
            <div><span>Account</span><strong>{user.username}</strong></div>
            {user.vmName && <div><span>Machine</span><strong>{user.vmName}</strong></div>}
            {user.provisioningState && status === 'approved' && (
              <div><span>Provisioning</span><strong className={`evm-prov evm-prov-${user.provisioningState}`}>{user.provisioningState}</strong></div>
            )}
          </div>
        )}

        {status === 'approved' && (
          <div className="evm-pending-actions">
            {readyVm ? (
              <a className="evm-btn evm-btn-primary evm-btn-lg" href="/portal/">Open VM Portal <ArrowRight size={18} /></a>
            ) : (
              <button className="evm-btn evm-btn-ghost evm-btn-lg" disabled={loading} onClick={() => window.location.assign('/portal/')}>
                {loading ? 'Checking…' : 'Open VM Portal'}
              </button>
            )}
          </div>
        )}

        <div className="evm-pending-foot">
          <a href="/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
          <button className="evm-link-btn" onClick={signout}>Sign out</button>
        </div>
      </div>
    </div>
  )
}
