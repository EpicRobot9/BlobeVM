(function(){
  window.api = window.api || {};
  window.api.startVM = async function(vmname){
    const res = await fetch(`/dashboard/api/start/${encodeURIComponent(vmname)}`, {method: 'POST'});
    let j = {};
    try{ j = await res.json(); }catch(e){ j = {ok:false, error: 'Invalid JSON'} }
    return { ok: res.ok && j && j.ok, status: res.status, body: j };
  };
  window.api.getVMStatus = async function(vmname){
    const res = await fetch(`/dashboard/api/vm/${encodeURIComponent(vmname)}/status`, {cache: 'no-store'});
    try{ return await res.json(); }catch(e){ return {ok:false, error: 'Invalid JSON'} }
  };
})();
