import React from 'react'

export default function Button({children, className='', onClick, style={}, ...rest}){
  const handleClick = (e) => {
    const el = e.currentTarget
    const rect = el.getBoundingClientRect()
    const ripple = document.createElement('span')
    const size = Math.max(rect.width, rect.height)
    ripple.style.position = 'absolute'
    ripple.style.borderRadius = '50%'
    ripple.style.width = ripple.style.height = size + 'px'
    ripple.style.left = (e.clientX - rect.left - size/2) + 'px'
    ripple.style.top = (e.clientY - rect.top - size/2) + 'px'
    ripple.style.background = 'rgba(255,255,255,0.12)'
    ripple.style.transform = 'scale(0)'
    ripple.style.transition = 'transform .5s ease, opacity .6s ease'
    ripple.className = 'ripple-span'
    ripple.style.pointerEvents = 'none'
    el.style.position = 'relative'
    el.appendChild(ripple)
    requestAnimationFrame(()=>{ ripple.style.transform = 'scale(1)'; ripple.style.opacity = '0'; })
    setTimeout(()=>{ try{ el.removeChild(ripple) }catch(e){} }, 700)
    if(onClick) onClick(e)
  }

  return (
    <button {...rest} onClick={handleClick} className={`card-elev ${className}`} style={{position:'relative',overflow:'hidden',...style}}>
      {children}
    </button>
  )
}
