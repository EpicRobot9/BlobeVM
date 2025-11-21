import React, { useState, useEffect } from 'react'
import apiFetch from '../lib/fetchWrapper'
import Button from './Button'
import { useToasts } from './ToastProvider'

export default function VmExec({ vmName }){
  const [cmd, setCmd] = useState('')
  const [output, setOutput] = useState('')
  const [running, setRunning] = useState(false)
  const [history, setHistory] = useState([])
  const { addToast } = useToasts()

  useEffect(()=>{
    if(!vmName){ setOutput(''); setHistory([]) }
  }, [vmName])

  async function runCmd(){
    if(!vmName) return setOutput('No VM selected')
    if(!cmd) return
    setRunning(true)
    try{
      const r = await apiFetch(`/vm/exec/${encodeURIComponent(vmName)}`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cmd})})
      const j = await r.json().catch(()=>({ok:false, error:'invalid json'}))
      const out = (j.output || '') + (j.error_output ? '\n[stderr]\n'+j.error_output : '')
      const entry = {ts: Date.now(), cmd, out, ok: !!j.ok}
      setHistory(h=>[entry, ...h].slice(0,50))
      setOutput(out)
      // show toast for result
      if(j && j.ok){ addToast({title: `Exec on ${vmName} succeeded`, message: cmd, type:'success'}) }
      else { addToast({title: `Exec on ${vmName} failed`, message: j.error || 'See output', type:'error'}) }
    }catch(e){ setOutput('Exec error: '+String(e)); addToast({title:`Exec error on ${vmName}`, message:String(e), type:'error'}) }
    setRunning(false)
  }

  return (
    <div className="vm-exec" style={{display:'flex',flexDirection:'column',gap:8}}>
      <div style={{display:'flex',gap:8}}>
        <input placeholder={vmName ? `Run command on ${vmName}` : 'Select a VM first'} value={cmd} onChange={e=>setCmd(e.target.value)} style={{flex:1,padding:8,borderRadius:8,border:'1px solid rgba(255,255,255,0.04)'}} />
        <Button onClick={runCmd} disabled={running || !cmd}>{running ? 'Running…' : 'Run'}</Button>
      </div>
      <div className="cols" style={{display:'flex',gap:12,alignItems:'flex-start'}}>
        <div className="output" style={{flex:1}}>
          <div style={{fontSize:13,color:'var(--muted)',marginBottom:6}}>Output</div>
          <div style={{background:'#02040a',color:'#dff',padding:8,borderRadius:8,height:220,overflow:'auto',fontFamily:'monospace',fontSize:12}}>
            <pre style={{whiteSpace:'pre-wrap',margin:0}}>{output}</pre>
          </div>
        </div>
        <div className="history" style={{width:260}}>
          <div style={{fontSize:13,color:'var(--muted)',marginBottom:6}}>History</div>
          <div style={{background:'rgba(255,255,255,0.02)',padding:8,borderRadius:8,height:220,overflow:'auto'}}>
            {history.length===0 ? <div style={{color:'var(--muted)'}}>No commands yet</div> : history.map((h,idx)=> (
              <div key={h.ts+idx} style={{marginBottom:8,borderBottom:'1px dashed rgba(255,255,255,0.02)',paddingBottom:6}}>
                <div style={{fontSize:12,color:'var(--muted)'}}>{new Date(h.ts).toLocaleString()}</div>
                <div style={{fontFamily:'monospace',fontSize:13,marginTop:6}}>{h.cmd}</div>
                <div style={{fontSize:12,color:h.ok ? 'var(--green)' : 'var(--muted)',marginTop:6}}>{h.out ? (h.out.length>200 ? h.out.slice(0,200)+'…' : h.out) : ''}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
