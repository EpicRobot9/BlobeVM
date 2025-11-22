// Defines a global hook `useVMStatus(vmname, opts)` that components can call.
// Relies on React being available as a global.
(function(){
  window.useVMStatus = function(vmname, opts){
    const interval = (opts && opts.interval) || 1500;
    const { useState, useEffect } = React;
    return (function useHook(){
      const [status, setStatus] = useState(null);
      useEffect(()=>{
        let cancelled = false;
        let handle = null;
        async function pollOnce(){
          try{
            const j = await window.api.getVMStatus(vmname);
            if(!cancelled) setStatus(j && j.status ? j.status : (j && j.ok===false ? 'unknown' : null));
          }catch(e){ if(!cancelled) setStatus('unknown'); }
        }
        pollOnce();
        handle = setInterval(pollOnce, interval);
        return ()=>{ cancelled = true; if(handle) clearInterval(handle); };
      }, [vmname, interval]);
      return status;
    })();
  };
})();
