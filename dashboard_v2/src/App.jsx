import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import Topbar from './components/Topbar'
import Sidebar from './components/Sidebar'
import ToastProvider from './components/ToastProvider'
import Login from './components/Login'
import Home from './pages/Home'
import VMManager from './pages/VMManager'
import ResourceUsage from './pages/ResourceUsage'
import Logs from './pages/Logs'
import Optimizer from './pages/Optimizer'
import Settings from './pages/Settings'
import APIInfo from './pages/APIInfo'
import AdvancedTools from './pages/AdvancedTools'
import { isAuthenticated, setToken } from './lib/auth'

export default function App(){
  const [collapsed, setCollapsed] = useState(()=>{
    try{ return JSON.parse(localStorage.getItem('nbv2_sidebar_collapsed') || 'false') }catch(e){ return false }
  })
  const [mobileOpen, setMobileOpen] = useState(false)
  const [authed, setAuthed] = useState(false)
  const navigate = useNavigate()

  useEffect(()=>{
    setAuthed(isAuthenticated())
  },[])

  if(!authed){
    return <div className="app-shell"><div className="main" style={{display:'flex',alignItems:'center',justifyContent:'center'}}><Login onLogin={()=>{ setAuthed(true); navigate('/') }} /></div></div>
  }

  const location = useLocation()
  return (
    <ToastProvider>
    <div className="app-shell">
      <aside className={`sidebar ${collapsed? 'collapsed':''} ${mobileOpen? 'mobile-open':''}`}>
        <Sidebar collapsed={collapsed} onCollapse={()=>{
          const next = !collapsed
          try{ localStorage.setItem('nbv2_sidebar_collapsed', JSON.stringify(next)) }catch(e){}
          setCollapsed(next)
        }} mobileOpen={mobileOpen} onMobileClose={()=>setMobileOpen(false)} />
      </aside>
      <div style={{flex:1,display:'flex',flexDirection:'column'}}>
        <div className="topbar"><Topbar collapsed={collapsed} onToggle={()=>{
          const next = !collapsed
          try{ localStorage.setItem('nbv2_sidebar_collapsed', JSON.stringify(next)) }catch(e){}
          setCollapsed(next)
        }} onToggleMobile={()=>setMobileOpen(v=>!v)} onLogout={()=>{ setToken(null); setAuthed(false); navigate('/login') }} /></div>
        <main className="main">
          <div key={location.pathname} className="page">
            <Routes>
              <Route path="/" element={<Home/>} />
              <Route path="/vm" element={<VMManager/>} />
              <Route path="/resources" element={<ResourceUsage/>} />
              <Route path="/logs" element={<Logs/>} />
              <Route path="/optimizer" element={<Optimizer/>} />
              <Route path="/settings" element={<Settings/>} />
              <Route path="/api" element={<APIInfo/>} />
              <Route path="/tools" element={<AdvancedTools/>} />
              <Route path="/login" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
    </ToastProvider>
  )
}
