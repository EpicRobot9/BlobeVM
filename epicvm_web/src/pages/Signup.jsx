import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { User, Lock, Envelope, IdentificationBadge, ArrowRight } from '@phosphor-icons/react'
import { signup } from '../api'

export default function Signup({ setUser }) {
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', password: '', displayName: '', email: '', who: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const set = (k) => (e) => setForm((s) => ({ ...s, [k]: e.target.value }))

  async function submit(e) {
    e.preventDefault()
    setErr('')
    if (form.password.length < 3) {
      setErr('Password must be at least 3 characters.')
      return
    }
    setBusy(true)
    try {
      const res = await signup(form)
      if (res.ok && res.body.ok) {
        // Account is pending; show the pending state.
        navigate('/pending')
        return
      }
      setErr(res.body.error || 'Unable to create your account. Please try again.')
    } catch {
      setErr('Unable to reach EpicVM. Please try again.')
    }
    setBusy(false)
  }

  return (
    <div className="evm-page evm-auth">
      <div className="evm-auth-card">
        <span className="evm-pill">PRIVATE BETA</span>
        <h1 className="evm-h1 sm">Request Access</h1>
        <p className="evm-body">Create an account. We'll review your request and provision your machine after approval.</p>
        <form onSubmit={submit} className="evm-form">
          <label>Username
            <span className="evm-input"><User size={18} /><input value={form.username} onChange={set('username')} placeholder="3-64 chars: letters, numbers, . _ -" autoComplete="username" required /></span>
          </label>
          <label>Password
            <span className="evm-input"><Lock size={18} /><input type="password" value={form.password} onChange={set('password')} placeholder="At least 12 characters" autoComplete="new-password" required /></span>
          </label>
          <label>Display name <span className="evm-opt">(optional)</span>
            <span className="evm-input"><IdentificationBadge size={18} /><input value={form.displayName} onChange={set('displayName')} placeholder="How you'd like to be addressed" /></span>
          </label>
          <label>Email <span className="evm-opt">(optional)</span>
            <span className="evm-input"><Envelope size={18} /><input type="email" value={form.email} onChange={set('email')} placeholder="you@example.com" autoComplete="email" /></span>
          </label>
          <label>How do you know Epic? <span className="evm-opt">(optional — helps me recognize you)</span>
            <span className="evm-input"><IdentificationBadge size={18} /><input value={form.who} onChange={set('who')} placeholder="e.g. a friend, a student group" /></span>
          </label>
          {err && <div className="evm-error" role="alert">{err}</div>}
          <button className="evm-btn evm-btn-primary evm-btn-lg" disabled={busy} type="submit">
            {busy ? 'Submitting…' : <>Request Access <ArrowRight size={18} /></>}
          </button>
        </form>
        <p className="evm-switch">Already have an account? <a href="/EpicVM/signin" onClick={(e) => { e.preventDefault(); navigate('/signin') }}>Sign in</a></p>
      </div>
    </div>
  )
}
