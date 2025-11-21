import React, { useEffect, useState } from 'react'
import apiFetch from '../lib/fetchWrapper'
import VmExec from '../components/VmExec'
import Button from '../components/Button'

export default function AdvancedTools(){
  const [vms, setVms] = useState([])
  const [selected, setSelected] = useState('')

  useEffect(()=>{
    async function load(){
      try{
        const r = await apiFetch('/list')
        const j = await r.json().catch(()=>({instances:[]}))
        setVms((j.instances||[]).map(i=>i.name))
        if((j.instances||[]).length) setSelected((j.instances||[])[0].name)
      }catch(e){ console.error('load vms', e) }
    }
    load()
  }, [])

  return (
    <div>
      <h1 style={{marginTop:0}}>Advanced Tools</h1>
      <div className="glass-card">
        <div style={{display:'flex',gap:12,alignItems:'center',marginBottom:12}}>
          <div style={{fontSize:13,color:'var(--muted)'}}>Select VM</div>
          <select value={selected} onChange={e=>setSelected(e.target.value)} style={{padding:8,borderRadius:8}}>
            {vms.map(v=> <option key={v} value={v}>{v}</option>)}
          </select>
          <a href="/dashboard/vm/" target="_blank" rel="noreferrer"><Button>Open VM Manager</Button></a>
        </div>

        <div style={{marginTop:6}}>
          <VmExec vmName={selected} />
        </div>
      </div>
    </div>
  )
}
