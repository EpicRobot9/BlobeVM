import React, { useState } from 'react'
import { useTheme } from '../lib/theme'
import Button from './Button'

export default function Topbar({onLogout, collapsed, onToggle, onToggleMobile}){
  const { theme, toggle } = useTheme()
  const [open, setOpen] = useState(false)
  return (
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',width:'100%'}}>
      <div style={{display:'flex',alignItems:'center',gap:12}}>
        <button onClick={onToggleMobile} style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:40,height:40,borderRadius:8,border:'none',background:'transparent',color:'var(--muted)',marginRight:6}}>☰</button>
        <button onClick={onToggle} style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:36,height:36,borderRadius:8,border:'none',background:'transparent',color:'var(--muted)',marginRight:6}} aria-pressed={collapsed}>‹</button>
        <strong style={{fontSize:16}}>BlobeVM Manager</strong>
        <span style={{color:'var(--muted)'}}>Dashboard</span>
      </div>
      <div style={{display:'flex',alignItems:'center',gap:10}}>
        <Button onClick={toggle} style={{padding:'8px 10px',background:'transparent',border:'1px solid rgba(255,255,255,0.04)'}}>{theme === 'dark' ? '🌙' : '☀️'}</Button>
        <div style={{position:'relative'}}>
          <Button onClick={()=>setOpen(s=>!s)} style={{padding:'8px 10px',background:'transparent',border:'1px solid rgba(255,255,255,0.04)'}}>👤</Button>
          {open && (
            <div style={{position:'absolute',right:0,top:'110%',background:'var(--card)',padding:8,borderRadius:8,boxShadow:'0 8px 24px rgba(2,6,23,0.6)'}}>
              <div style={{padding:6,whiteSpace:'nowrap'}} onClick={onLogout}>Logout</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
