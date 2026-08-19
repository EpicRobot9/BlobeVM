import React from 'react'
import { useNavigate } from 'react-router-dom'

// Shared chrome for the auth pages so Sign In / Request Access feel like part of
// the same EpicVM portal (orange-bordered header + BETA pill, matching Portal.jsx).
export default function AuthShell({ children, title }) {
  const navigate = useNavigate()
  return (
    <div className="evm-page evm-auth-page">
      <header className="evm-portal-head">
        <div className="evm-ph-left">
          <button className="evm-brand evm-brand-btn" onClick={() => navigate('/')} aria-label="EpicVM home">
            <span className="evm-brand-mark">E</span> EpicVM
          </button>
          <span className="evm-beta-pill">BETA</span>
        </div>
        <div className="evm-ph-right">
          {title === 'Sign In'
            ? <a className="evm-link-btn" href="/EpicVM/signup" onClick={(e) => { e.preventDefault(); navigate('/signup') }}>Request Access</a>
            : <a className="evm-link-btn" href="/EpicVM/signin" onClick={(e) => { e.preventDefault(); navigate('/signin') }}>Sign In</a>}
        </div>
      </header>
      <div className="evm-auth-wrap">
        {children}
      </div>
    </div>
  )
}
