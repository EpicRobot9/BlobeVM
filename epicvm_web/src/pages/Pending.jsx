import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Hourglass, CheckCircle, XCircle, Spinner, ArrowRight, Desktop } from '@phosphor-icons/react'
import { myVms, logout } from '../api'

export default function Pending({ user, onSignout }) {
  const navigate = useNavigate()
  const [vms, setVms] = useState(null)
  const status = (user && user.accountStatus) || 'pending'
  const rejected = status === 'rejected'
  const approved = status === 'approved'

  // Approved-but-no-VM or provisioning: poll VMs for readiness.
  useEffect(() => {
    if (status !== 'approved') return
    let live = true
    const tick = () => {
      myVms().then((res) => {
        if (!live) return
        if (res.ok) setVms(res.body.vms || [])
      }).catch(() => {})
    }
    tick()
    const t = window.setInterval(tick, 7000)
    return () => { live = false; window.clearInterval(t) }
  }, [status])

  const hasVm = Array.isArray(vms) && vms.length > 0
  const readyVm = Array.isArray(vms) ? vms.find((v) => v.readiness === 'ready' || v.running) : null

  async function signout() {
    await logout().catch(() => {})
    window.location.assign('/EpicVM/')
  }

  if (rejected) {
    return (
      <div className="evm-page evm-auth">
        <div className="evm-auth-card evm-pending evm-rejected">
          <span className="evm-status-badge bad"><XCircle size={20} /> rejected</span>
          <h1 className="evm-h1 sm">Request Not Approved</h1>
          <p className="evm-body">Your access request wasn't approved this time. Thanks for your interest — feel free to reach out if you'd like to talk about it.</p>
          <div className="evm-pending-foot">
            <a href="/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
            <button className="evm-link-btn" onClick={signout}>Sign out</button>
          </div>
        </div>
      </div>
    )
  }

  // Approved: show provisioning / ready states.
  if (approved) {
    const title = readyVm ? 'Your Machine Is Ready' : (hasVm ? 'Preparing Your Machine' : 'Preparing Your Machine')
    const body = readyVm
      ? 'Your EpicVM cloud computer is ready. Open the VM Portal to connect and manage it.'
      : 'EpicVM is creating and configuring your cloud computer. This may take a little time — you can open the portal shortly.'
    return (
      <div className="evm-page evm-auth">
        <div className="evm-auth-card evm-pending">
          <span className={`evm-status-badge ${readyVm ? 'ok' : 'warn'}`}>{readyVm ? <CheckCircle size={20} /> : <Spinner size={20} className="spin" />} {readyVm ? 'ready' : 'preparing'}</span>
          <h1 className="evm-h1 sm">{title}</h1>
          <p className="evm-body">{body}</p>
          {user && user.vmName && <div className="evm-account"><div><span>Machine</span><strong>{user.vmName}</strong></div></div>}
          <div className="evm-pending-actions">
            <button className="evm-btn evm-btn-primary evm-btn-lg" onClick={() => navigate('/portal')}>OPEN PORTAL <ArrowRight size={18} /></button>
          </div>
          <div className="evm-pending-foot">
            <a href="/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
            <button className="evm-link-btn" onClick={signout}>Sign out</button>
          </div>
        </div>
      </div>
    )
  }

  // Pending (default).
  return (
    <div className="evm-page evm-auth">
      <div className="evm-auth-card evm-pending">
        <span className="evm-status-badge warn"><Hourglass size={20} /> pending</span>
        <h1 className="evm-h1 sm">Access Request Received</h1>
        <p className="evm-body">Your EpicVM account is waiting for approval. We'll review your request and provision your machine once approved. This usually takes a little while — thanks for your patience.</p>
        <div className="evm-pending-foot">
          <a href="/EpicVM/" onClick={(e) => { e.preventDefault(); window.location.assign('/EpicVM/') }}>Back to home</a>
          <button className="evm-link-btn" onClick={signout}>Sign out</button>
        </div>
      </div>
    </div>
  )
}
