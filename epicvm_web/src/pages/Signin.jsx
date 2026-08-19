import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { User, Lock, ArrowRight } from '@phosphor-icons/react'
import { login } from '../api'
import AuthShell from '../components/AuthShell'

export default function Signin({ setUser }) {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      const res = await login(username, password)
      if (res.ok && res.body.ok) {
        setUser(res.body.user)
        navigate('/pending')
        return
      }
      setErr(res.body.error || 'Invalid credentials.')
    } catch {
      setErr('Unable to reach EpicVM. Please try again.')
    }
    setBusy(false)
  }

  return (
    <AuthShell title="Sign In">
      <div className="evm-auth-card">
        <h1 className="evm-h1 sm">Sign In</h1>
        <p className="evm-body">Welcome back. Sign in to check your account status or open the VM Portal.</p>
        <form onSubmit={submit} className="evm-form">
          <label>Username
            <span className="evm-input"><User size={18} /><input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" autoComplete="username" required /></span>
          </label>
          <label>Password
            <span className="evm-input"><Lock size={18} /><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="password" autoComplete="current-password" required /></span>
          </label>
          {err && <div className="evm-error" role="alert">{err}</div>}
          <button className="evm-btn evm-btn-primary evm-btn-lg" disabled={busy} type="submit">
            {busy ? 'Signing in…' : <>Sign In <ArrowRight size={18} /></>}
          </button>
        </form>
        <p className="evm-switch">No account yet? <a href="/EpicVM/signup" onClick={(e) => { e.preventDefault(); navigate('/signup') }}>Request Access</a></p>
      </div>
    </AuthShell>
  )
}
