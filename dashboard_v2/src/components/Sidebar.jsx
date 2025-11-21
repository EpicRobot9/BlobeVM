import React from 'react'
import { NavLink } from 'react-router-dom'

const items = [
  {to:'/', label:'Home Overview'},
  {to:'/vm', label:'VM Manager'},
  {to:'/resources', label:'Resource Usage'},
  {to:'/logs', label:'Logs Viewer'},
  {to:'/optimizer', label:'Optimizer Control'},
  {to:'/settings', label:'Settings'},
  {to:'/api', label:'API & System Info'},
  {to:'/tools', label:'Advanced Tools'}
]

export default function Sidebar({collapsed, onCollapse, mobileOpen, onMobileClose}){
  return (
    <div style={{display:'flex',flexDirection:'column',height:'100%'}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'8px 6px'}}>
        <div style={{fontWeight:700,color:'#cfe8ff'}}>BlobeVM</div>
        <div style={{display:'flex',gap:6,alignItems:'center'}}>
          {mobileOpen ? <button onClick={onMobileClose} style={{background:'transparent',border:'none',color:'var(--muted)'}}>✕</button> : null}
          <button onClick={onCollapse} className="collapse-btn" style={{background:'transparent',border:'none',color:'var(--muted)',transform: collapsed ? 'rotate(180deg)' : 'rotate(0)',transition:'transform .28s ease'}}>‹</button>
        </div>
      </div>
      <nav style={{marginTop:8,display:'flex',flexDirection:'column',gap:6}}>
        {items.map(i=> (
          <NavLink key={i.to} to={i.to} className={({isActive})=> 'nav-item' + (isActive? ' active':'')}>
            <span style={{width:18,height:18,background:'linear-gradient(90deg,var(--blue-500),var(--blue-600))',borderRadius:6,display:'inline-block',transform:'translateZ(0)',transition:'transform .18s ease'}}></span>
            <span style={{flex:1}}>{i.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
