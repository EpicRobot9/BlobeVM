import React, { createContext, useContext, useEffect, useState } from 'react'

const ThemeContext = createContext(null)
const STORAGE_KEY = 'nbv2_theme'

export function ThemeProvider({children}){
  const [theme, setTheme] = useState(() => {
    try{
      const saved = localStorage.getItem(STORAGE_KEY)
      if(saved) return saved
    }catch(e){}
    // detect system preference
    try{
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
    }catch(e){ return 'dark' }
  })

  useEffect(()=>{
    try{ localStorage.setItem(STORAGE_KEY, theme) }catch(e){}
    try{ document.documentElement.dataset.theme = theme }catch(e){}
  }, [theme])

  const toggle = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  return <ThemeContext.Provider value={{theme, setTheme, toggle}}>{children}</ThemeContext.Provider>
}

export function useTheme(){
  const ctx = useContext(ThemeContext)
  if(!ctx) throw new Error('useTheme must be used inside ThemeProvider')
  return ctx
}
