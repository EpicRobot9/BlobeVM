import React, { createContext, useContext, useState, useCallback, useEffect } from 'react'

const ToastContext = createContext(null)

let nextId = 1

export function ToastProvider({ children }){
  const [toasts, setToasts] = useState([])

  const addToast = useCallback(({ title, message, type='info', timeout=5000 }) =>{
    const id = nextId++
    setToasts(t => [{ id, title, message, type }, ...t])
    if(timeout && timeout > 0){
      setTimeout(()=> setToasts(t => t.filter(x => x.id !== id)), timeout)
    }
    return id
  }, [])

  const removeToast = useCallback((id) => setToasts(t => t.filter(x => x.id !== id)), [])

  return (
    <ToastContext.Provider value={{ addToast, removeToast }}>
      {children}
      <div className="toast-container" aria-live="polite" aria-atomic="true">
        {toasts.map(t => (
          <div key={t.id} className={`toast ${t.type||'info'}`} role="status">
            <div className="toast-title">{t.title}</div>
            <div className="toast-message">{t.message}</div>
            <button className="toast-close" onClick={()=>removeToast(t.id)} aria-label="Dismiss">✕</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToasts(){
  const ctx = useContext(ToastContext)
  if(!ctx) throw new Error('useToasts must be used within ToastProvider')
  return ctx
}

export default ToastProvider
