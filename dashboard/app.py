#!/usr/bin/env python3
import os, json, subprocess, shlex, base64, socket, threading, time
import shutil
from urllib import request as urlrequest, error as urlerror
from functools import wraps
from flask import Flask, jsonify, request, abort, send_from_directory, render_template_string, Response

APP_ROOT = '/opt/blobe-vm'
MANAGER = 'blobe-vm-manager'
HOST_DOCKER_BIN = os.environ.get('HOST_DOCKER_BIN') or '/usr/bin/docker'
CONTAINER_DOCKER_BIN = os.environ.get('CONTAINER_DOCKER_BIN') or '/usr/bin/docker'
DOCKER_VOLUME_BIND = f'{HOST_DOCKER_BIN}:{CONTAINER_DOCKER_BIN}:ro'
TEMPLATE = r"""
<!doctype html><html><head><title>BlobeVM Dashboard</title>
<style>body{font-family:system-ui,Arial;margin:1.5rem;background:#111;color:#eee}table{border-collapse:collapse;width:100%;}th,td{padding:.5rem;border-bottom:1px solid #333}a,button{background:#2563eb;color:#fff;border:none;padding:.4rem .8rem;border-radius:4px;text-decoration:none;cursor:pointer}form{display:inline}h1{margin-top:0} .badge{background:#444;padding:.15rem .4rem;border-radius:3px;font-size:.65rem;text-transform:uppercase;margin-left:.3rem} .muted{opacity:.75} .btn-red{background:#dc2626} .btn-gray{background:#374151} .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}.green{background:#10b981}.red{background:#ef4444}.gray{background:#6b7280}.amber{background:#f59e0b}</style>
</head><body>
<h1>BlobeVM Dashboard</h1>
<div id=errbox style="display:none;background:#7f1d1d;color:#fff;padding:.5rem .75rem;border-radius:4px;margin:.5rem 0"></div>
<form method=post action="/dashboard/api/create" onsubmit="return createVM(event)">
<input name=name placeholder="name" required pattern="[a-zA-Z0-9-]+" />
<button type=submit>Create</button>
</form>
<div style="margin:.5rem 0 1rem 0">
<input id=spport placeholder="single-port (e.g., 20002)" style="width:220px" />
<button onclick="enableSinglePort()">Enable single-port mode</button>
<span class=badge>Experimental</span>
</div>
<div style="margin:.25rem 0 1.25rem 0" class=muted>
<input id=dashport placeholder="direct dash port (optional)" style="width:260px" />
<button class="btn-gray" onclick="disableSinglePort()">Disable single-port (direct mode)</button>
</div>
<table><thead><tr><th>Name</th><th>Status</th><th>Port/Path</th><th>URL</th><th>Actions</th></tr></thead><tbody id=tbody></tbody></table>
<div style="margin:1rem 0 2rem 0">
        <button onclick="bulkRecreate()">Recreate ALL VMs</button>
        <button onclick="bulkRebuildAll()">Rebuild ALL VMs</button>
        <button onclick="bulkUpdateAndRebuild()">Update & Rebuild ALL VMs</button>
        <button onclick="pruneDocker()" class="btn-gray">Prune Docker</button>
        <button onclick="bulkDeleteAll()" class="btn-red">Delete ALL VMs</button>
        <span class="muted" style="margin-left: .5rem">Shift+Click Check for report-only (no auto-fix)</span>
    </div>
<div style="margin:1.5rem 0 .5rem 0">
    <span class=badge>Custom domain (merged mode):</span>
    <input id=customdomain placeholder="e.g. vms.example.com" style="width:220px" />
    <button onclick="setCustomDomain()">Set domain</button>
    <span id=domainip style="margin-left:1.5rem"></span>
</div>
<script>
// Debug helpers: enable extra logs with ?debug=1
const DEBUG = new URLSearchParams(window.location.search).has('debug');
const dbg = (...args) => { if (DEBUG) console.log('[BLOBEDASH]', ...args); };
window.addEventListener('error', (e) => console.error('[BLOBEDASH] window error', e.message, e.error || e));
window.addEventListener('unhandledrejection', (e) => console.error('[BLOBEDASH] unhandledrejection', e.reason));

function showErr(msg){
    try{
        const eb = document.getElementById('errbox');
        if(!eb) return;
        eb.style.display = 'block';
        eb.textContent = String(msg);
    }catch(e){ console.error('showErr error', e); }
}

function clearErr(){
    try{ const eb = document.getElementById('errbox'); if(eb){ eb.style.display='none'; eb.textContent=''; } }catch(e){}
}

let mergedMode = false, basePath = '/vm', customDomain = '', dashPort = '', dashIp = '';
let vms = [];
let availableApps = [];
async function load(){
    try {
        const [r, r2, r3] = await Promise.all([
            fetch('/dashboard/api/list'),
            fetch('/dashboard/api/modeinfo'),
            fetch('/dashboard/api/apps').catch(()=>({ok:false}))
        ]);
        const eb = document.getElementById('errbox');
        if (!r.ok || !r2.ok) {
            const msg = `/dashboard/api/list: ${r.status} | /dashboard/api/modeinfo: ${r2.status}`;
            console.error('[BLOBEDASH] API error', msg);
            eb.style.display = 'block';
            eb.textContent = `Dashboard API error: ${msg}. If you enabled auth, ensure the same credentials are applied to API calls (refresh the page).`;
            return;
        }
        eb.style.display = 'none'; eb.textContent = '';
    const data = await r.json().catch(err => { console.error('[BLOBEDASH] list JSON error', err); return {instances:[]}; });
        const info = await r2.json().catch(err => { console.error('[BLOBEDASH] modeinfo JSON error', err); return {}; });
        if (r3 && r3.ok) {
            const apps = await r3.json().catch(()=>({apps:[]}));
            availableApps = apps.apps || [];
        }
        dbg('modeinfo', info);
        dbg('instances', data.instances);
    mergedMode = !!info.merged;
    basePath = info.basePath||'/vm';
    // normalize basePath: ensure single leading slash and no trailing slash
    if(!basePath) basePath = '/vm';
    if(!basePath.startsWith('/')) basePath = '/' + basePath;
    basePath = basePath.replace(/\/+$/, '');
        customDomain = info.domain||'';
        dashPort = info.dashPort||'';
        dashIp = info.ip||'';
        document.getElementById('customdomain').value = customDomain;
        document.getElementById('domainip').textContent = `Point domain to: ${dashIp}`;
    vms = data.instances || [];
    const tb=document.getElementById('tbody');
        tb.innerHTML='';
    const appOpts = (availableApps||[]).map(a=>`<option value="${a}">${a}</option>`).join('');
    vms.forEach(i=>{
            const tr=document.createElement('tr');
            const dot = statusDot(i.status);
            let portOrPath = '';
            let openUrl = i.url;
            if(mergedMode){
                // merged: show /vm/<name> or domain
                portOrPath = `${basePath}/${i.name}`;
                if(customDomain){
                    openUrl = `http://${customDomain}${basePath}/${i.name}/`;
                }
            }else{
                // direct: show port; always build link using current browser host
                // Prefer explicit port from API, else try to parse from URL or status text
                if (i.port && String(i.port).match(/^\d+$/)) {
                    portOrPath = String(i.port);
                } else {
                    let m = i.url && i.url.match(/:(\d+)/);
                    portOrPath = m ? m[1] : '';
                }
                if (!portOrPath && i.status) {
                    const ms = i.status.match(/\(port\s+(\d+)\)/i);
                    if (ms) portOrPath = ms[1];
                }
                if (portOrPath) {
                    const proto = window.location.protocol;
                    const host = window.location.hostname;
                    openUrl = `${proto}//${host}:${portOrPath}/`;
                } else {
                    openUrl = '';
                }
            }
            dbg('row', { name: i.name, status: i.status, rawUrl: i.url, mergedMode, portOrPath, openUrl });
             tr.innerHTML=`<td>${i.name}</td><td>${dot}<span class=muted>${i.status||''}</span></td><td>${portOrPath}</td><td><a href="${openUrl}" target="_blank" rel="noopener noreferrer">${openUrl}</a></td>`+
                 `<td>`+
                 `<button onclick="openLink('${openUrl}')">Open</button>`+
                 `<button onclick="act('start','${i.name}')">Start</button>`+
                 `<button onclick="act('stop','${i.name}')">Stop</button>`+
                 `<button onclick="act('restart','${i.name}')">Restart</button>`+
                 `<button title="Shift-click for no-fix" onclick="checkVM(event,'${i.name}')" class="btn-gray">Check</button>`+
                 `<button onclick="updateVM('${i.name}')" class="btn-gray">Update</button>`+
                 `<button onclick="installChrome('${i.name}')">Install Chrome</button>`+
                 `<select id="appsel-${i.name}" class="btn-gray" style="background:#1f2937;color:#fff;padding:.35rem .4rem;margin-left:.25rem"><option value="">App…</option>${appOpts}</select>`+
                 `<button onclick="installSelectedApp('${i.name}')">Install</button>`+
                 `<button onclick="uninstallSelectedApp('${i.name}')" class="btn-red">Uninstall</button>`+
                 `<button onclick="reinstallSelectedApp('${i.name}')" class="btn-gray">Reinstall</button>`+
                 `<button onclick="appStatusSelected('${i.name}')" class="btn-gray">Status</button>`+
                 `<button onclick="recreateVM('${i.name}')">Recreate</button>`+
                 `<button onclick="rebuildVM('${i.name}')">Rebuild</button>`+
                 `<button onclick=\"cleanVM('${i.name}')\" class=\"btn-gray\">Clean</button>`+
                 `<button onclick="delvm('${i.name}')" class="btn-red">Delete</button>`+
                 `</td>`;
          tb.appendChild(tr);
        });
    } catch (err) {
        console.error('[BLOBEDASH] load() error', err);
    }
}
function recreateVM(name){
    if(!confirm('Recreate VM '+name+'?'))return;
    fetch('/dashboard/api/recreate',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:[name]})
    }).then(load);
}
function rebuildVM(name){
    if(!confirm('Rebuild (image + recreate) VM '+name+'?'))return;
    fetch('/dashboard/api/rebuild-vms',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:[name]})
    }).then(load);
}
async function updateVM(name){
    if(!confirm('Update packages inside VM '+name+'?'))return;
    try{
        const r = await fetch(`/dashboard/api/update-vm/${encodeURIComponent(name)}`,{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && (j.ok || j.started)){
            alert('Update started. Status will show as Updating…');
        }else{
            showErr('Update failed:\n'+((j && (j.error||j.output))||'unknown error'));
        }
    }catch(e){
        showErr('Update error: '+e);
    }
    load();
}

async function pruneDocker(){
    if(!confirm('Prune unused Docker data (images, containers, cache)?')) return;
    try{
        const r = await fetch('/dashboard/api/prune-docker', {method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && (j.ok || j.started)){
            alert('Docker prune started. This may take a while.');
        }else{
            showErr('Failed to start prune: ' + (j && (j.error||j.output) || 'unknown'));
        }
    }catch(e){ alert('Prune error: '+e); }
}

async function cleanVM(name){
    if(!confirm('Clean apt caches and temporary files inside VM '+name+'?')) return;
    try{
        const r = await fetch(`/dashboard/api/clean-vm/${encodeURIComponent(name)}`, {method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert('Clean requested.');
        }else{
            alert('Clean failed:\n' + ((j && (j.error||j.output)) || 'unknown error'));
        }
    }catch(e){ alert('Clean error: ' + e); }
    load();
}
async function installChrome(name){
    if(!confirm('Install Google Chrome in VM '+name+'?'))return;
    try{
        const r = await fetch(`/dashboard/api/app-install/${encodeURIComponent(name)}/chrome`,{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert('Chrome installation requested.');
        }else{
            alert('Chrome install failed:\n'+((j && (j.error||j.output))||'unknown error'));
        }
    }catch(e){
        alert('Install error: '+e);
    }
    load();
}
async function installApp(name, app){
    try{
        const r = await fetch(`/dashboard/api/app-install/${encodeURIComponent(name)}/${encodeURIComponent(app)}`,{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert(`${app} installation requested.`);
        }else{
            alert(`${app} install failed:\n`+((j && (j.error||j.output))||'unknown error'));
        }
    }catch(e){
        alert('Install error: '+e);
    }
    load();
}
function openLink(url){
    try{
        if(!url || typeof url !== 'string'){
            alert('No URL available yet. Try again after the VM starts.');
            return;
        }
        // Basic sanity: must start with http(s)://
        if(!/^https?:\/\//i.test(url)){
            alert('Invalid URL.');
            return;
        }
        window.open(url, '_blank');
    }catch(e){
        console.error('openLink error', e);
    }
}
function selectedApp(name){
    const el = document.getElementById(`appsel-${name}`);
    return (el && el.value ? el.value.trim() : '');
}
async function installSelectedApp(name){
    const app = selectedApp(name);
    if(!app){ alert('Select an app first.'); return; }
    await installApp(name, app);
}
async function appStatusSelected(name){
    const app = selectedApp(name);
    if(!app){ alert('Select an app first.'); return; }
    try{
        const r = await fetch(`/dashboard/api/app-status/${encodeURIComponent(name)}/${encodeURIComponent(app)}`);
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert(`${app} status: ${j.status||'installed'}`);
        }else{
            alert(`${app} not installed or unknown.`);
        }
    }catch(e){
        alert('Status error: '+e);
    }
}
async function uninstallSelectedApp(name){
    const app = selectedApp(name);
    if(!app){ alert('Select an app first.'); return; }
    if(!confirm(`Uninstall ${app} from ${name}?`)) return;
    try{
        const r = await fetch(`/dashboard/api/app-uninstall/${encodeURIComponent(name)}/${encodeURIComponent(app)}`,{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert(`${app} uninstall requested.`);
        }else{
            alert(`${app} uninstall failed:\n`+((j && (j.error||j.output))||'unknown error'));
        }
    }catch(e){
        alert('Uninstall error: '+e);
    }
    load();
}
async function reinstallSelectedApp(name){
    const app = selectedApp(name);
    if(!app){ alert('Select an app first.'); return; }
    if(!confirm(`Reinstall ${app} in ${name}? This will uninstall first.`)) return;
    try{
        const r = await fetch(`/dashboard/api/app-reinstall/${encodeURIComponent(name)}/${encodeURIComponent(app)}`,{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert(`${app} reinstall requested.`);
        }else{
            alert(`${app} reinstall failed:\n`+((j && (j.error||j.output))||'unknown error'));
        }
    }catch(e){
        alert('Reinstall error: '+e);
    }
    load();
}
function bulkRecreate(){
    if(!confirm('Recreate ALL VMs?'))return;
    fetch('/dashboard/api/recreate',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:vms.map(x=>x.name)})
    }).then(load);
}
function bulkRebuildAll(){
    if(!confirm('Rebuild (image + recreate) ALL VMs?'))return;
    fetch('/dashboard/api/rebuild-vms',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:vms.map(x=>x.name)})
    }).then(load);
}
function bulkUpdateAndRebuild(){
    if(!confirm('Update repo, rebuild image, and recreate ALL VMs?'))return;
    fetch('/dashboard/api/update-and-rebuild',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:vms.map(x=>x.name)})
    }).then(load);
}
function bulkDeleteAll(){
    var conf=prompt('Delete ALL VMs? This cannot be undone. Type DELETE to confirm.');
    if(conf!=='DELETE')return;
    fetch('/dashboard/api/delete-all-instances',{method:'POST'}).then(load);
}
async function setCustomDomain(){
    try{
        const dom = document.getElementById('customdomain').value.trim();
        if(!dom){ showErr('Enter a domain.'); return; }
        console.log('[BLOBEDASH] setCustomDomain ->', dom);
        clearErr();
        const di = document.getElementById('domainip'); if(di) di.textContent = 'Applying...';
        // Ask server to persist domain and apply merged/domain-mode settings so VMs pick it up
        const r = await fetch('/dashboard/api/set-domain?apply=1', {method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`domain=${encodeURIComponent(dom)}&apply=1`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ip){
            if(di) di.textContent = `Point domain to: ${j.ip}`;
        } else {
            showErr('Saved, but could not resolve IP.');
            if(di) di.textContent = '';
        }
        if(j && j.applied){
            alert('Domain saved and merged-mode applied. VMs are being restarted in background.');
        }
    }catch(e){
        showErr('Set domain error: '+e);
    }
}
function statusDot(st){
    const s=(st||'').toLowerCase();
    let cls='gray';
    if(s.includes('rebuilding') || s.includes('updating')) cls='amber';
    else if(s.includes('up')) cls='green';
    else if(s.includes('exited')||s.includes('stopped')||s.includes('dead')) cls='red';
    return `<span class="dot ${cls}"></span>`;
}
async function act(cmd,name){await fetch(`/dashboard/api/${cmd}/${name}`,{method:'post'});load();}
async function delvm(name){if(!confirm('Delete '+name+'?'))return;await fetch(`/dashboard/api/delete/${name}`,{method:'post'});load();}
async function createVM(e){
    e.preventDefault();
    const fd=new FormData(e.target);
    try {
        const r = await fetch('/dashboard/api/create', {method:'post',body:new URLSearchParams(fd)});
        if (!r.ok) {
            const j = await r.json().catch(()=>({}));
            showErr(j.error || 'Failed to create VM.');
        }
    } catch (err) {
        showErr('Error creating VM: ' + err);
    }
    e.target.reset();
    load();
}
async function enableSinglePort(){
    const p=document.getElementById('spport').value||'20002';
    const r=await fetch('/dashboard/api/enable-single-port',{method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`port=${encodeURIComponent(p)}`});
    const j=await r.json().catch(()=>({}));
    dbg('enable-single-port', {port: p, response: j});
    alert((j && j.message) || 'Requested. The dashboard may move to the new port soon.');
}
async function disableSinglePort(){
    const p=document.getElementById('dashport').value||'';
    const body=p?`port=${encodeURIComponent(p)}`:'';
    const r=await fetch('/dashboard/api/disable-single-port',{method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});
    const j=await r.json().catch(()=>({}));
    dbg('disable-single-port', {port: p, response: j});
    alert((j && (j.message||j.error)) || 'Requested. The dashboard may move to a high port soon.');
}
async function checkVM(ev,name){
    const nofix = ev && ev.shiftKey ? 1 : 0;
    try{
        const r = await fetch(`/dashboard/api/check/${encodeURIComponent(name)}`,{method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body: nofix? 'nofix=1' : ''});
        const j = await r.json().catch(()=>({}));
        dbg('check', name, j);
        if(j && j.ok){
            alert(`OK ${j.code} - ${j.url}${j.fixed? ' (auto-resolved)': ''}`);
        }else{
            alert(`FAIL ${j && j.code ? j.code : ''} - ${(j && j.url) || ''}\n${(j && j.output) || ''}`);
        }
    }catch(e){
        alert('Check error: '+e);
    }
    load();
}
    load();setInterval(load,8000);

    // Optimizer panel controls
    async function loadOptimizer(){
        try{
            const r = await fetch('/dashboard/api/optimizer/status');
            if(!r.ok) return;
            const j = await r.json();
            const el = document.getElementById('optimizer-status');
            if(el) el.textContent = JSON.stringify(j, null, 2);
            const en = document.getElementById('optimizer-enabled');
            if(en) en.checked = !!(j && j.cfg && j.cfg.enabled);
            const mg = document.getElementById('guard-memory');
            if(mg) mg.checked = !!(j && j.cfg && j.cfg.guards && j.cfg.guards.memory);
            const cg = document.getElementById('guard-cpu');
            if(cg) cg.checked = !!(j && j.cfg && j.cfg.guards && j.cfg.guards.cpu);
            const sg = document.getElementById('guard-swap');
            if(sg) sg.checked = !!(j && j.cfg && j.cfg.guards && j.cfg.guards.swap);
            const hg = document.getElementById('guard-health');
            if(hg) hg.checked = !!(j && j.cfg && j.cfg.guards && j.cfg.guards.health);
            const sm = document.getElementById('guard-strictmem');
            if(sm) sm.checked = !!(j && j.cfg && j.cfg.strictMemoryLimit);
        }catch(e){ console.error('loadOptimizer', e); }
    }

    async function optimizerSet(key, val){
        await fetch('/dashboard/api/optimizer/set', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key, val})});
        await loadOptimizer();
    }

    async function optimizerRunOnce(){
        const r = await fetch('/dashboard/api/optimizer/run-once', {method:'POST'});
        if(r.ok) alert('Optimizer run started'); else showErr('Failed to start optimizer run');
    }

    async function optimizerTail(){
        const r = await fetch('/dashboard/api/optimizer/logs');
        if(!r.ok) return showErr('No logs');
        const t = await r.text();
        const el = document.getElementById('optimizer-logs');
        if(el) el.textContent = t;
    }

    async function optimizerCleanSystem(){
        if(!confirm('Run system cleaner (will drop caches and prune docker). Proceed?')) return;
        const r = await fetch('/dashboard/api/optimizer/clean-system', {method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.started) alert('Cleaner started'); else showErr('Cleaner failed: '+(j.error||'unknown'));
    }

    // Periodically refresh optimizer panel
    loadOptimizer(); setInterval(loadOptimizer, 15000);

</script>

<div style="margin:1.5rem 0;padding:1rem;border:1px solid #333;border-radius:6px;background:#081226">
    <h2 style="margin-top:0">Optimizer Panel</h2>
    <div style="display:flex;gap:1rem;align-items:center;margin-bottom:.5rem">
        <label><input id="optimizer-enabled" type="checkbox" onchange="optimizerSet('enabled', this.checked)"> Optimizer Enabled</label>
        <label><input id="guard-memory" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {memory:this.checked}))"> Memory Guard</label>
        <label><input id="guard-cpu" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {cpu:this.checked}))"> CPU Guard</label>
        <label><input id="guard-swap" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {swap:this.checked}))"> Swap Guard</label>
        <label><input id="guard-health" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {health:this.checked}))"> Health Guard</label>
        <label><input id="guard-strictmem" type="checkbox" onchange="optimizerSet('strictMemoryLimit', this.checked)"> Strict Memory Limits</label>
    </div>
    <div style="margin-bottom:.5rem">
        <button onclick="optimizerRunOnce()">Run Once</button>
        <button onclick="optimizerTail()" class="btn-gray">Show Logs</button>
        <button onclick="optimizerCleanSystem()" class="btn-red">System Cleaner</button>
    </div>
    <pre id="optimizer-status" style="background:#000;color:#9ee;padding:.5rem;border-radius:4px;max-height:180px;overflow:auto"></pre>
    <pre id="optimizer-logs" style="background:#000;color:#9ee;padding:.5rem;border-radius:4px;max-height:240px;overflow:auto;margin-top:.5rem"></pre>
</div>

</body></html>
"""

app = Flask(__name__)

BUSER = os.environ.get('BLOBEDASH_USER')
BPASS = os.environ.get('BLOBEDASH_PASS')

def need_auth():
    return bool(BUSER and BPASS)

def check_auth(header: str) -> bool:
    if not header or not header.lower().startswith('basic '):
        return False
    try:
        raw = base64.b64decode(header.split(None,1)[1]).decode('utf-8')
        user, pw = raw.split(':',1)
        return user == BUSER and pw == BPASS
    except Exception:
        return False

def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if need_auth():
            if not check_auth(request.headers.get('Authorization')):
                return Response('Auth required', 401, {'WWW-Authenticate':'Basic realm="BlobeVM Dashboard"'})
        return fn(*args, **kwargs)
    return wrapper

def _state_dir():
    return os.environ.get('BLOBEDASH_STATE', '/opt/blobe-vm')

def _repo_manager_path():
    # Fallback path to the repo-managed CLI inside the mounted state dir
    return os.path.join(_state_dir(), 'server', 'blobe-vm-manager')

def _inst_dir():
    return os.path.join(_state_dir(), 'instances')

def _flag_path(name: str, flag: str) -> str:
    return os.path.join(_inst_dir(), name, f'.{flag}')

def _set_flag(name: str, flag: str, on: bool = True):
    try:
        p = _flag_path(name, flag)
        if on:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                f.write(str(int(time.time())))
        else:
            if os.path.isfile(p):
                os.remove(p)
    except Exception:
        pass

def _has_flag(name: str, flag: str, max_age_sec: int = 6*3600) -> bool:
    try:
        p = _flag_path(name, flag)
        if not os.path.isfile(p):
            return False
        if max_age_sec is None:
            return True
        st = os.stat(p)
        return (time.time() - st.st_mtime) < max_age_sec
    except Exception:
        return False

def _run_manager(*args):
    """Run the manager with given args. If the primary manager doesn't support
    the command (prints Usage/unknown), fall back to the repo script.
    Returns (ok: bool, stdout: str, stderr: str, returncode: int).
    """
    try:
        r = subprocess.run([MANAGER, *args], capture_output=True, text=True)
    except FileNotFoundError:
        r = subprocess.CompletedProcess([MANAGER, *args], 127, '', 'not found')
    ok = (r.returncode == 0)
    errtxt = (r.stderr or '') + ('' if ok else ('\n' + (r.stdout or '')))
    # Heuristic: if command not recognized or prints usage, try fallback
    need_fallback = (
        (not ok) and (
            'Usage: blobe-vm-manager' in errtxt or
            'unknown' in errtxt.lower() or
            'not found' in errtxt.lower()
        )
    )
    if need_fallback:
        alt = _repo_manager_path()
        if os.path.isfile(alt):
            # If not executable, try invoking via bash
            cmd = [alt, *args] if os.access(alt, os.X_OK) else ['bash', alt, *args]
            r2 = subprocess.run(cmd, capture_output=True, text=True)
            return (r2.returncode == 0, (r2.stdout or '').strip(), (r2.stderr or '').strip(), r2.returncode)
    return (ok, (r.stdout or '').strip(), (r.stderr or '').strip(), r.returncode)

def _is_direct_mode():
    env = _read_env()
    return env.get('NO_TRAEFIK', '1') == '1'

def _request_host():
    try:
        host = request.headers.get('X-Forwarded-Host') or request.host or ''
        return (host.split(':')[0] if host else '')
    except Exception:
        return ''

def _vm_host_port(cname: str) -> str:
    try:
        r = _docker('port', cname, '3000/tcp')
        if r.returncode == 0 and r.stdout:
            line = r.stdout.strip().splitlines()[0]
            parts = line.rsplit(':', 1)
            if len(parts) == 2 and parts[1].strip().isdigit():
                return parts[1].strip()
    except Exception:
        pass
    return ''

def _build_vm_url(name: str) -> str:
    """Best-effort VM URL appropriate for the current mode, for browser-origin host.
    In direct mode, combine request host with published port. In merged mode, use manager url.
    """
    if _is_direct_mode():
        host = _request_host()
        if not host:
            return ''
        cname = f'blobevm_{name}'
        hp = _vm_host_port(cname)
        if hp:
            return f'http://{host}:{hp}/'
    # Fallback to manager-provided URL
    try:
        return subprocess.check_output([MANAGER, 'url', name], text=True).strip()
    except Exception:
        return ''

def manager_json_list():
    """Return a list of instances with best-effort status and URL.
    Tries the manager 'list' first (requires docker CLI). Falls back to scanning
    the instances directory and asking the manager for each URL individually.
    """
    instances = []
    try:
        # Fast path: parse manager list output
        out = subprocess.check_output([MANAGER, 'list'], text=True)
        lines = [l[2:] for l in out.splitlines() if l.startswith('- ')]
        for l in lines:
            try:
                parts = [p.strip() for p in l.split('->')]
                name = parts[0].split()[0]
                status = parts[1] if len(parts) > 1 else ''
                url = parts[2] if len(parts) > 2 else ''
                instances.append({'name': name, 'status': status, 'url': url})
            except Exception:
                pass
        if instances:
            # In direct mode, override URL with host:published-port (or manager port) to avoid container IPs
            if _is_direct_mode():
                host = _request_host()
                for it in instances:
                    cname = f"blobevm_{it['name']}"
                    hp = _vm_host_port(cname)
                    if not hp:
                        try:
                            hp = subprocess.check_output([MANAGER, 'port', it['name']], text=True).strip()
                        except Exception:
                            hp = ''
                    # Record explicit port for frontend
                    if hp and hp.isdigit():
                        it['port'] = hp
                    if hp and host:
                        it['url'] = f"http://{host}:{hp}/"
            # Apply transient statuses (e.g., rebuilding/updating)
            for it in instances:
                try:
                    if _has_flag(it['name'], 'rebuilding'):
                        it['status'] = 'Rebuilding...'
                    elif _has_flag(it['name'], 'updating'):
                        it['status'] = 'Updating...'
                except Exception:
                    pass
            return instances
    except Exception:
        # likely docker CLI not present inside container -> fall back
        pass

    # Fallback: scan instance folders and resolve URL per instance
    inst_root = os.path.join(_state_dir(), 'instances')
    try:
        names = [n for n in os.listdir(inst_root) if os.path.isdir(os.path.join(inst_root, n))]
    except Exception:
        names = []
    # Cache docker ps output if docker exists
    docker_status = {}
    try:
        out = _docker('ps', '-a', '--format', '{{.Names}} {{.Status}}')
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split(None, 1)
                if parts:
                    docker_status[parts[0]] = parts[1] if len(parts) > 1 else ''
    except Exception:
        pass
    for name in sorted(names):
        url = ''
        cname = f'blobevm_{name}'
        status = docker_status.get(cname, '') or ''
        if not status:
            status = '(unknown)'
        port = ''
        # In direct mode, compute URL using host published port
        if _is_direct_mode():
            host = _request_host()
            hp = _vm_host_port(cname)
            if not hp:
                try:
                    hp = subprocess.check_output([MANAGER, 'port', name], text=True).strip()
                except Exception:
                    hp = ''
            if hp and host:
                url = f"http://{host}:{hp}/"
            else:
                # Fallback to manager per-VM URL (may be container IP, but last resort)
                try:
                    url = subprocess.check_output([MANAGER, 'url', name], text=True).strip()
                except Exception:
                    url = ''
            if hp and hp.isdigit():
                port = hp
        else:
            try:
                url = subprocess.check_output([MANAGER, 'url', name], text=True).strip()
            except Exception:
                url = ''
        # Transient status override
        if _has_flag(name, 'rebuilding'):
            status = 'Rebuilding...'
        elif _has_flag(name, 'updating'):
            status = 'Updating...'
        inst = {'name': name, 'status': status, 'url': url}
        if port:
            inst['port'] = port
        instances.append(inst)
    return instances

def _read_env():
    env_path = os.path.join(_state_dir(), '.env')
    data = {}
    try:
        with open(env_path, 'r') as f:
            for line in f:
                if not line.strip() or line.strip().startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    v = v.strip().strip('\n').strip().strip("'\"")
                    data[k.strip()] = v
    except Exception:
        pass
    return data

def _write_env_kv(updates: dict):
    env_path = os.path.join(_state_dir(), '.env')
    existing = _read_env()
    existing.update({k: str(v) for k, v in updates.items()})
    # Write back preserving simple KEY='VAL' format
    lines = []
    for k, v in existing.items():
        if v is None:
            v = ''
        # single-quote with escaping
        vq = "'" + str(v).replace("'", "'\\''") + "'"
        lines.append(f"{k}={vq}")
    try:
        with open(env_path, 'w') as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception:
        return False

def _docker(*args):
    return subprocess.run(['docker', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

@app.get('/dashboard/api/modeinfo')
@auth_required
def api_modeinfo():
    env = _read_env()
    merged = env.get('NO_TRAEFIK', '1') == '0'
    base_path = env.get('BASE_PATH', '/vm')
    domain = env.get('BLOBEVM_DOMAIN', '')
    dash_port = env.get('DASHBOARD_PORT', '')
    # Show the host the user used to reach the dashboard
    ip = _request_host() or ''
    return jsonify({'merged': merged, 'basePath': base_path, 'domain': domain, 'dashPort': dash_port, 'ip': ip})

@app.post('/dashboard/api/set-domain')
@auth_required
def api_set_domain():
    dom = request.values.get('domain','').strip()
    if not dom:
        return jsonify({'ok': False, 'error': 'No domain'}), 400
    # Persist the domain
    _write_env_kv({'BLOBEVM_DOMAIN': dom})
    # If caller requested, also apply merged/domain-mode settings so domain routing will be used.
    apply_mode = request.values.get('apply') in ('1','true','yes')
    if apply_mode:
        # Set merged-mode env vars that manager expects. Do not modify routing code itself.
        _write_env_kv({
            'NO_TRAEFIK': '0',
            'MERGED_MODE': '1',
            'TRAEFIK_NETWORK': 'proxy',
            'ENABLE_DASHBOARD': '1',
        })
        # Run background worker to ensure proxy network exists and restart VMs so they pick up new mode
        def worker_apply(domain_name):
            try:
                # Ensure network exists
                r = _docker('network', 'inspect', 'proxy')
                if r.returncode != 0:
                    _docker('network', 'create', 'proxy')
                # Restart all instances so they reattach with updated labels/mode
                inst_root = os.path.join(_state_dir(), 'instances')
                try:
                    names = [n for n in os.listdir(inst_root) if os.path.isdir(os.path.join(inst_root, n))]
                except Exception:
                    names = []
                for name in names:
                    cname = f'blobevm_{name}'
                    # remove container and start via manager to ensure labels/networks are applied
                    _docker('rm', '-f', cname)
                    try:
                        subprocess.run([MANAGER, 'start', name], check=False)
                    except Exception:
                        pass
            except Exception:
                pass
        threading.Thread(target=worker_apply, args=(dom,), daemon=True).start()
    # Best-effort IP hint: show the host the user is using to reach the dashboard
    ip = _request_host() or ''
    if not ip:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = ''
    return jsonify({'ok': True, 'domain': dom, 'ip': ip, 'applied': apply_mode})

def _enable_single_port(port: int):
    """Enable single-port mode by launching a tiny Traefik and reattaching services.
    - Creates network 'proxy' if missing
    - Starts traefik on host port <port>
    - Recreates dashboard joined to 'proxy' with labels for /dashboard
    - Recreates VM containers via manager so they carry labels and join the network
    """
    # Persist env changes for manager url rendering
    _write_env_kv({
        'NO_TRAEFIK': '0',
        'HTTP_PORT': str(port),
        'TRAEFIK_NETWORK': 'proxy',
        'ENABLE_DASHBOARD': '1',
        'BASE_PATH': _read_env().get('BASE_PATH', '/vm'),
        'MERGED_MODE': '1',
    })

    # Ensure network exists
    r = _docker('network', 'inspect', 'proxy')
    if r.returncode != 0:
        _docker('network', 'create', 'proxy')

    # Start or recreate Traefik
    # Map chosen host port -> container :80
    ps_names = _docker('ps', '-a', '--format', '{{.Names}}').stdout.splitlines()
    if 'traefik' in ps_names:
        _docker('rm', '-f', 'traefik')
    _docker('run', '-d', '--name', 'traefik', '--restart', 'unless-stopped',
            '-p', f'{port}:80',
            '-v', '/var/run/docker.sock:/var/run/docker.sock:ro',
            '--network', 'proxy',
            'traefik:v2.11',
            '--providers.docker=true',
            '--providers.docker.exposedbydefault=false',
            '--entrypoints.web.address=:80',
            '--api.dashboard=true')

    # Start an additional dashboard container joined to proxy with labels
    # Keep the current one running to avoid killing this process mid-flight
    if 'blobedash-proxy' in ps_names:
        _docker('rm', '-f', 'blobedash-proxy')
    _docker('run', '-d', '--name', 'blobedash-proxy', '--restart', 'unless-stopped',
            '-v', f'{_state_dir()}:/opt/blobe-vm',
            '-v', '/usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro',
            '-v', '/var/run/docker.sock:/var/run/docker.sock',
            '-v', DOCKER_VOLUME_BIND,
            '-v', f'{_state_dir()}/dashboard/app.py:/app/app.py:ro',
            '-e', f'BLOBEDASH_USER={os.environ.get("BLOBEDASH_USER","")}',
            '-e', f'BLOBEDASH_PASS={os.environ.get("BLOBEDASH_PASS","")}',
            '-e', f'HOST_DOCKER_BIN={HOST_DOCKER_BIN}',
            '--network', 'proxy',
            '--label', 'traefik.enable=true',
            '--label', 'traefik.http.routers.blobe-dashboard.rule=PathPrefix(`/dashboard`)',
            '--label', 'traefik.http.routers.blobe-dashboard.entrypoints=web',
            '--label', 'traefik.http.services.blobe-dashboard.loadbalancer.server.port=5000',
            'python:3.11-slim',
            'bash', '-c', 'pip install --no-cache-dir flask && python /app/app.py')

    # Recreate VM containers into proxy network
    inst_root = os.path.join(_state_dir(), 'instances')
    names = []
    try:
        names = [n for n in os.listdir(inst_root) if os.path.isdir(os.path.join(inst_root, n))]
    except Exception:
        pass
    for name in names:
        cname = f'blobevm_{name}'
        _docker('rm', '-f', cname)
        try:
            subprocess.run([MANAGER, 'start', name], check=False)
        except Exception:
            pass

def _disable_single_port(dash_port: int | None):
    # Persist env toggles
    env = _read_env()
    direct_start = int(env.get('DIRECT_PORT_START', '20000') or '20000')
    updates = {
        'NO_TRAEFIK': '1',
        'ENABLE_DASHBOARD': '1',
        'DASHBOARD_PORT': str(dash_port) if dash_port else env.get('DASHBOARD_PORT',''),
        'MERGED_MODE': '0',
    }
    _write_env_kv(updates)

    # Stop traefik and proxy dashboard if present
    _docker('rm', '-f', 'blobedash-proxy')
    _docker('rm', '-f', 'traefik')

    # Recreate VMs into direct mode (exposed ports)
    inst_root = os.path.join(_state_dir(), 'instances')
    try:
        names = [n for n in os.listdir(inst_root) if os.path.isdir(os.path.join(inst_root, n))]
    except Exception:
        names = []
    for name in names:
        cname = f'blobevm_{name}'
        _docker('rm', '-f', cname)
        try:
            subprocess.run([MANAGER, 'start', name], check=False)
        except Exception:
            pass

    # Ensure direct-mode dashboard is present: either call ensure script if available,
    # or recreate a blobedash container with a high port.
    ensure_script = '/opt/blobe-vm/server/blobedash-ensure.sh'
    if os.path.isfile(ensure_script):
        try:
            if dash_port:
                env2 = os.environ.copy(); env2['DASHBOARD_PORT'] = str(dash_port)
                subprocess.run(['bash', ensure_script], env=env2, check=False)
            else:
                subprocess.run(['bash', ensure_script], check=False)
        except Exception:
            pass
    else:
        # Fallback: start direct dashboard on the provided port or choose one
        port = dash_port or direct_start
        # Free any existing blobedash
        _docker('rm', '-f', 'blobedash')
        _docker('run', '-d', '--name', 'blobedash', '--restart', 'unless-stopped',
            '-p', f'{port}:5000',
            '-v', f'{_state_dir()}:/opt/blobe-vm',
            '-v', '/usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro',
            '-v', DOCKER_VOLUME_BIND,
            '-v', '/var/run/docker.sock:/var/run/docker.sock',
            '-v', f'{_state_dir()}/dashboard/app.py:/app/app.py:ro',
            '-e', f'BLOBEDASH_USER={os.environ.get("BLOBEDASH_USER","")}',
            '-e', f'BLOBEDASH_PASS={os.environ.get("BLOBEDASH_PASS","")}',
            '-e', f'HOST_DOCKER_BIN={HOST_DOCKER_BIN}',
        'python:3.11-slim',
            'bash', '-c', 'pip install --no-cache-dir flask && python /app/app.py')

@app.get('/dashboard')
@auth_required
def root():
    return render_template_string(TEMPLATE)

@app.get('/dashboard/api/list')
@auth_required
def api_list():
    return jsonify({'instances': manager_json_list()})

@app.post('/dashboard/api/create')
@auth_required
def api_create():
    name = request.form.get('name','').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'No name provided'}), 400
    try:
        result = subprocess.run([MANAGER, 'create', name], capture_output=True, text=True)
        if result.returncode == 125:
            # Docker exit 125: container name conflict or similar
            msg = result.stderr.strip() or 'VM already exists or container conflict.'
            # Try to start anyway
            subprocess.run([MANAGER, 'start', name], capture_output=True)
            return jsonify({'ok': False, 'error': msg})
        elif result.returncode != 0:
            return jsonify({'ok': False, 'error': result.stderr.strip() or 'Error creating VM.'}), 500
        # Auto-start after creation
        subprocess.run([MANAGER, 'start', name], capture_output=True)
    except FileNotFoundError:
        return jsonify({'ok': False, 'error': 'blobe-vm-manager not found in container. Make sure it is installed and mounted.'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Error creating VM: {e}'}), 500
    return jsonify({'ok': True})

@app.post('/dashboard/api/start/<name>')
@auth_required
def api_start(name):
    subprocess.check_call([MANAGER, 'start', name])
    return jsonify({'ok': True})

@app.post('/dashboard/api/stop/<name>')
@auth_required
def api_stop(name):
    subprocess.check_call([MANAGER, 'stop', name])
    return jsonify({'ok': True})

@app.post('/dashboard/api/delete/<name>')
@auth_required
def api_delete(name):
    subprocess.check_call([MANAGER, 'delete', name])
    return jsonify({'ok': True})

@app.post('/dashboard/api/restart/<name>')
@auth_required
def api_restart(name):
    try:
        r = subprocess.run([MANAGER, 'restart', name], capture_output=True, text=True)
        ok = (r.returncode == 0)
        return jsonify({'ok': ok, 'output': r.stdout.strip(), 'error': r.stderr.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# Bulk/targeted VM actions
@app.post('/dashboard/api/recreate')
@auth_required
def api_recreate():
    names = request.json.get('names', [])
    if not names:
        return jsonify({'error': 'No VM names provided'}), 400
    try:
        result = subprocess.run([MANAGER, 'recreate', *names], capture_output=True, text=True)
        ok = (result.returncode == 0)
        return jsonify({'ok': ok, 'output': result.stdout.strip(), 'error': result.stderr.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/rebuild-vms')
@auth_required
def api_rebuild_vms():
    names = request.json.get('names', [])
    if not names:
        return jsonify({'error': 'No VM names provided'}), 400
    # Mark VMs as rebuilding and run in the background so UI can show status
    for n in names:
        _set_flag(n, 'rebuilding', True)
    def worker(targets):
        try:
            subprocess.run([MANAGER, 'rebuild-vms', *targets], capture_output=True, text=True)
        finally:
            for n in targets:
                _set_flag(n, 'rebuilding', False)
    threading.Thread(target=worker, args=(names,), daemon=True).start()
    return jsonify({'ok': True, 'started': True})

@app.post('/dashboard/api/update-and-rebuild')
@auth_required
def api_update_and_rebuild():
    names = request.json.get('names', [])
    # Mark as rebuilding and run in background
    targets = names[:]
    if not targets:
        # If none specified, mark all known instances
        try:
            targets = [i['name'] for i in manager_json_list()]
        except Exception:
            targets = []
    for n in targets:
        _set_flag(n, 'rebuilding', True)
    def worker(tgts):
        try:
            args = [MANAGER, 'update-and-rebuild'] + names
            subprocess.run(args, capture_output=True, text=True)
        finally:
            for n in tgts:
                _set_flag(n, 'rebuilding', False)
    threading.Thread(target=worker, args=(targets,), daemon=True).start()
    return jsonify({'ok': True, 'started': True})

@app.post('/dashboard/api/delete-all-instances')
@auth_required
def api_delete_all_instances():
    try:
        result = subprocess.run([MANAGER, 'delete-all-instances'], capture_output=True, text=True)
        ok = (result.returncode == 0)
        return jsonify({'ok': ok, 'output': result.stdout.strip(), 'error': result.stderr.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/prune-docker')
@auth_required
def api_prune_docker():
    """Prune unused Docker data on the host. Runs in background."""
    def worker():
        try:
            _docker('system', 'prune', '-af')
            _docker('builder', 'prune', '-af')
            _docker('image', 'prune', '-af')
            _docker('volume', 'prune', '-f')
        except Exception:
            pass
    try:
        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'ok': True, 'started': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/update-vm/<name>')
@auth_required
def api_update_vm(name):
    # Set transient updating flag and run in background to avoid blocking and to show status
    try:
        _set_flag(name, 'updating', True)
        def worker(vm_name):
            try:
                _run_manager('update-vm', vm_name)
            finally:
                _set_flag(vm_name, 'updating', False)
        threading.Thread(target=worker, args=(name,), daemon=True).start()
        return jsonify({'ok': True, 'started': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/app-install/<name>/<app>')
@auth_required
def api_app_install(name, app):
    try:
        ok, out, err, _ = _run_manager('app-install', name, app)
        return jsonify({'ok': ok, 'output': out, 'error': err})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.get('/dashboard/api/app-status/<name>/<app>')
@auth_required
def api_app_status(name, app):
    try:
        ok, out, err, _ = _run_manager('app-status', name, app)
        # Try to parse a simple status from stdout, else return as-is
        return jsonify({'ok': ok, 'output': out, 'error': err})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/app-uninstall/<name>/<app>')
@auth_required
def api_app_uninstall(name, app):
    try:
        ok, out, err, _ = _run_manager('app-uninstall', name, app)
        return jsonify({'ok': ok, 'output': out, 'error': err})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/app-reinstall/<name>/<app>')
@auth_required
def api_app_reinstall(name, app):
    try:
        ok, out, err, _ = _run_manager('app-reinstall', name, app)
        return jsonify({'ok': ok, 'output': out, 'error': err})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/clean-vm/<name>')
@auth_required
def api_clean_vm(name):
    """Clean apt caches and common temp directories inside the VM container."""
    cname = f'blobevm_{name}'
    # Best-effort: ignore errors
    try:
        cmds = [
            'apt-get update || true',
            'apt-get -y autoremove || true',
            'apt-get -y autoclean || true',
            'apt-get -y clean || true',
            'rm -rf /var/cache/apt/archives/* || true',
            'rm -rf /var/lib/apt/lists/* || true',
            'rm -rf /tmp/* /var/tmp/* || true',
            'mkdir -p /var/lib/apt/lists || true'
        ]
        for c in cmds:
            _docker('exec', '-u', 'root', cname, 'bash', '-lc', c)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.get('/dashboard/api/apps')
@auth_required
def api_apps():
    # Enumerate app scripts under /opt/blobe-vm/root/installable-apps
    apps_dir = os.path.join(_state_dir(), 'root', 'installable-apps')
    apps = []
    try:
        for f in os.listdir(apps_dir):
            if f.endswith('.sh'):
                apps.append(f[:-3])
    except Exception:
        pass
    apps.sort()
    return jsonify({'apps': apps})

def _http_check(url: str, timeout: float = 8.0) -> int:
    if not url:
        return 0
    # Ensure trailing slash to satisfy path prefix routers
    if not url.endswith('/'):
        url = url + '/'
    req = urlrequest.Request(url, method='HEAD')
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, 'status', 200))
    except urlerror.HTTPError as e:
        try:
            return int(e.code)
        except Exception:
            return 0
    except Exception:
        return 0

@app.post('/dashboard/api/check/<name>')
@auth_required
def api_check(name):
    nofix = request.values.get('nofix') in ('1','true','yes','on')
    url = _build_vm_url(name)
    code = _http_check(url)
    if code and 200 <= code < 400:
        return jsonify({'ok': True, 'code': code, 'url': url, 'fixed': False})
    if nofix:
        return jsonify({'ok': False, 'code': code, 'url': url, 'output': 'no-fix mode'}), 400
    # Attempt auto-resolve: recreate container and retry briefly
    fixed = False
    try:
        cname = f'blobevm_{name}'
        subprocess.run(['docker', 'rm', '-f', cname], capture_output=True)
        subprocess.run([MANAGER, 'start', name], capture_output=True)
        for _ in range(8):
            time.sleep(1)
            url = _build_vm_url(name)
            code = _http_check(url)
            if code and 200 <= code < 400:
                fixed = True
                break
    except Exception:
        pass
    return jsonify({'ok': (code and 200 <= code < 400), 'code': code or 0, 'url': url, 'fixed': fixed})

@app.post('/dashboard/api/enable-single-port')
@auth_required
def api_enable_single_port():
    try:
        port = int(request.values.get('port', '20002'))
    except Exception:
        abort(400)
    # Check if port is free on the host by trying to bind inside the container
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('0.0.0.0', port))
        s.close()
    except OSError:
        return jsonify({'ok': False, 'error': f'Port {port} appears to be in use. Choose a different port.'}), 409
    # Run in a background thread to avoid killing the serving container mid-request
    def worker():
        try:
            _enable_single_port(port)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'message': f'Enabling single-port mode on :{port}. Dashboard may reload at http://<host>:{port}/dashboard shortly.'})

@app.post('/dashboard/api/disable-single-port')
@auth_required
def api_disable_single_port():
    dash_port = request.values.get('port')
    try:
        dash_port = int(dash_port) if dash_port else None
    except Exception:
        return jsonify({'ok': False, 'error': 'Invalid port'}), 400
    def worker():
        try:
            _disable_single_port(dash_port)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()
    env = _read_env()
    effective_port = str(dash_port) if dash_port else env.get('DASHBOARD_PORT','') or env.get('DIRECT_PORT_START','20000')
    msg = f'Disabling single-port mode; dashboard will run on http://<host>:{effective_port}/dashboard.'
    return jsonify({'ok': True, 'message': msg, 'port': effective_port})


@app.get('/dashboard/api/optimizer/status')
@auth_required
def api_optimizer_status():
    """Return optimizer status and basic stats by invoking optimizer CLI if available."""
    state = _state_dir()
    node = 'node'
    script = os.path.join(state, 'optimizer', 'OptimizerService.js')
    if not os.path.isfile(script):
        return jsonify({'ok': False, 'error': 'optimizer script not installed'}), 404
    try:
        # Prefer running local `node` if available
        if shutil.which('node'):
            r = subprocess.run([node, script, 'status'], capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and r.stdout:
                return Response(r.stdout, mimetype='application/json')
        else:
            # Try running the optimizer CLI inside a temporary node docker container
            try:
                dr = subprocess.run(['docker', 'run', '--rm', '-v', f"{state}:{state}", '-w', state, 'node:18-slim', 'node', script, 'status'], capture_output=True, text=True, timeout=12)
                if dr.returncode == 0 and dr.stdout:
                    return Response(dr.stdout, mimetype='application/json')
            except Exception:
                pass

        # Fall back to returning config file if CLI didn't return
        cfgp = os.path.join(state, '.optimizer.json')
        cfg = {}
        try:
            with open(cfgp,'r') as f: cfg = json.load(f)
        except Exception:
            cfg = {'enabled': False}
        return jsonify({'ok': True, 'cfg': cfg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/run-once')
@auth_required
def api_optimizer_run_once():
    state = _state_dir()
    node = 'node'
    script = os.path.join(state, 'optimizer', 'OptimizerService.js')
    if not os.path.isfile(script):
        return jsonify({'ok': False, 'error': 'optimizer script not installed'}), 404
    def worker():
        try:
            if shutil.which('node'):
                subprocess.run([node, script, 'run-once'], capture_output=True, text=True)
            else:
                # Run using docker node image if available
                try:
                    subprocess.run(['docker', 'run', '--rm', '-v', f"{state}:{state}", '-w', state, 'node:18-slim', 'node', script, 'run-once'], capture_output=True, text=True)
                except Exception:
                    pass
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'started': True})


@app.post('/dashboard/api/optimizer/set')
@auth_required
def api_optimizer_set():
    data = request.get_json() or {}
    key = data.get('key')
    val = data.get('val')
    if not key:
        return jsonify({'ok': False, 'error': 'missing key'}), 400
    state = _state_dir()
    node = 'node'
    script = os.path.join(state, 'optimizer', 'OptimizerService.js')
    if not os.path.isfile(script):
        # fallback: modify cfg file directly
        cfgp = os.path.join(state, '.optimizer.json')
        try:
            cfg = {}
            if os.path.isfile(cfgp):
                with open(cfgp,'r') as f: cfg = json.load(f)
        except Exception:
            cfg = {}
        # apply simple assignment semantics
        if key == 'guards' and isinstance(val, dict):
            cfg.setdefault('guards', {}).update(val)
        else:
            cfg[key] = val
        try:
            with open(cfgp,'w') as f: json.dump(cfg, f)
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500
    try:
        args = [node, script, 'set', key, json.dumps(val)]
        r = subprocess.run(args, capture_output=True, text=True)
        return jsonify({'ok': r.returncode==0, 'output': r.stdout.strip(), 'error': r.stderr.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/optimizer/logs')
@auth_required
def api_optimizer_logs():
    p = '/var/blobe/logs/optimizer/optimizer.log'
    try:
        if os.path.isfile(p):
            return Response(open(p,'r').read(), mimetype='text/plain')
        return jsonify({'ok': False, 'error': 'no logs'}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/clean-system')
@auth_required
def api_optimizer_clean_system():
    """Run system cleaner: drop caches and prune docker but skip domain networks."""
    def worker():
        try:
            # Drop caches
            try:
                subprocess.run(['sync'], check=False)
                subprocess.run(['bash','-c','echo 3 > /proc/sys/vm/drop_caches'], check=False)
            except Exception:
                pass
            # Basic prune (images/containers/builders/volumes)
            try:
                subprocess.run(['docker','system','prune','-af'], check=False)
                subprocess.run(['docker','builder','prune','-af'], check=False)
                subprocess.run(['docker','image','prune','-af'], check=False)
                subprocess.run(['docker','volume','prune','-f'], check=False)
            except Exception:
                pass
            # Network prune but skip Blobe domain networks
            protected = set()
            env = _read_env()
            try:
                if env.get('TRAEFIK_NETWORK'):
                    protected.add(env.get('TRAEFIK_NETWORK'))
            except Exception:
                pass
            # Always protect common names
            for n in ('proxy','traefik','blobe','blobedash','blobedash-proxy'):
                protected.add(n)
            # Inspect networks and protect any with containers like traefik/blobedash
            try:
                nets_out = subprocess.check_output(['docker','network','ls','--format','{{.Name}}'], text=True).splitlines()
                for net in nets_out:
                    if not net: continue
                    nl = net.strip()
                    if any(x in nl.lower() for x in ('proxy','traefik','blobe','blobedash')):
                        protected.add(nl)
                    else:
                        # inspect containers attached
                        try:
                            js = subprocess.check_output(['docker','network','inspect',nl,'--format','{{json .Containers}}'], text=True)
                            if 'traefik' in js or 'blobedash' in js or 'blobedash-proxy' in js:
                                protected.add(nl)
                        except Exception:
                            pass
            except Exception:
                pass
            # Remove networks that are not protected (best-effort)
            try:
                nets_out = subprocess.check_output(['docker','network','ls','--format','{{.Name}}'], text=True).splitlines()
                for net in nets_out:
                    if not net: continue
                    if net in protected: continue
                    try:
                        subprocess.run(['docker','network','rm', net], check=False)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass
    try:
        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'ok': True, 'started': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
