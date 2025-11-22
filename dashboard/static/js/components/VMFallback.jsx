// VMFallback component (expects React global and window.api + window.useVMStatus)
(function(){
  const React = window.React;
  const { useState, useEffect, useRef } = React;
  function VMFallback(props){
    const vmname = props.vmname;
    const vmurl = props.vmurl;
    const status = window.useVMStatus(vmname, {interval:1500});
    const [phase, setPhase] = useState('idle'); // idle, starting, error
    const [errMsg, setErrMsg] = useState('');
    const startTimeoutRef = useRef(null);

    useEffect(()=>{
      if(status && /up/i.test(status)){
        const f = document.getElementById('vmframe'); if(f) f.style.display='block';
      } else {
        const f = document.getElementById('vmframe'); if(f) f.style.display='none';
      }
    }, [status]);

    useEffect(()=>()=>{ if(startTimeoutRef.current) clearTimeout(startTimeoutRef.current); }, []);

    async function doStart(){
      setPhase('starting'); setErrMsg('');
      try{
        const r = await window.api.startVM(vmname);
        if(!r.ok){ setPhase('error'); setErrMsg((r.body && r.body.error) || `HTTP ${r.status}`); return; }
      }catch(e){ setPhase('error'); setErrMsg(String(e)); return; }

      const startTs = Date.now();
      const timeoutMs = 35000;
      async function pollForUp(){
        try{
          const j = await window.api.getVMStatus(vmname);
          if(j && /up/i.test(j.status || '')){
            setPhase('idle');
            const f = document.getElementById('vmframe'); if(f) f.style.display='block';
            try{ if(window.alert) setTimeout(()=>alert(`${vmname} is now running`), 50); }catch(e){}
            return;
          }
        }catch(e){ }
        if(Date.now() - startTs > timeoutMs){ setPhase('error'); setErrMsg('Start timed out after 35 seconds'); return; }
        startTimeoutRef.current = setTimeout(pollForUp, 1500);
      }
      pollForUp();
    }

    return (
      React.createElement('div', {className:'fallback', role:'status'},
        React.createElement('div',{className:'card'},
          React.createElement('div',{className:'vm-name'}, vmname + ' is currently down…'),
          phase==='starting' ? (
            React.createElement('div', null,
              React.createElement('div', {className:'spinner', 'aria-hidden':true}),
              React.createElement('div', {className:'muted'}, 'Starting ' + vmname + '…')
            )
          ) : (
            React.createElement('div', null,
              React.createElement('button', {className:'btn-primary', onClick: doStart}, 'Start VM'),
              React.createElement('div', {className:'muted'}, 'If this fails, contact an administrator.')
            )
          ),
          phase==='error' && (
            React.createElement('div', {className:'errbox'},
              React.createElement('strong', null, 'There was an error starting ' + vmname + ':'),
              React.createElement('div', {style:{marginTop:8}}, errMsg),
              React.createElement('div', {style:{marginTop:10}},
                React.createElement('button', {className:'btn-primary', onClick: doStart}, 'Retry'),
                React.createElement('button', {className:'btn-secondary', onClick: ()=>location.reload(), style:{marginLeft:8}}, 'Reload')
              )
            )
          )
        )
      )
    );
  }
  window.VMFallback = VMFallback;
})();
