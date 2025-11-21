import React from 'react'

export default function Modal({open, title, onClose, children, width=800}){
  if(!open) return null
  const maxW = typeof width === 'number' ? `${width}px` : width
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(2,6,23,0.6)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:60}}>
      <div style={{width:'100%',maxWidth:`calc(${maxW} > 100% ? 100% : ${maxW})`,maxHeight: '90vh', overflow:'auto',padding:12}} className="glass-card">
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <h3 style={{margin:0}}>{title}</h3>
          <button onClick={onClose} style={{background:'transparent',border:'none',color:'var(--muted)'}}>✕</button>
        </div>
        <div style={{marginTop:12}}>{children}</div>
      </div>
    </div>
  )
}
