// Main bootstrap: reads init from window.__VM_WRAPPER_INIT and mounts App
(function(){
  const React = window.React;
  const ReactDOM = window.ReactDOM;
  const { useState, useEffect } = React;
  const init = window.__VM_WRAPPER_INIT || { vmname: null, vmurl: null };
  function App(){
    const status = window.useVMStatus(init.vmname, {interval:1500});
    if(status && /up/i.test(status)){
      const f = document.getElementById('vmframe'); if(f) f.style.display='block';
      return null;
    }
    return React.createElement(window.VMFallback, {vmname: init.vmname, vmurl: init.vmurl});
  }
  try{
    const root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(React.createElement(App));
  }catch(e){ console.error('VM wrapper mount failed', e); }
})();
