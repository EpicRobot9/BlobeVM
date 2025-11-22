#!/usr/bin/env python3
import os, json, subprocess, shlex, base64, socket, threading, time
import shutil
import re
from urllib import request as urlrequest, error as urlerror
from functools import wraps
from flask import Flask, jsonify, request, abort, send_from_directory, render_template_string, Response
import optimizer as dash_optimizer
import hmac, hashlib, time, base64
try:
    import psutil
except Exception:
    psutil = None

app = Flask(__name__)
 
# --- Auth helpers (must be defined before route decorators) ---
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
            # Allow basic auth via BUSER/BPASS or a valid v2 token (so dashboard_v2 can reuse existing APIs)
            auth_header = request.headers.get('Authorization')
            basic_ok = check_auth(auth_header)
            token_ok = False
            try:
                if auth_header and auth_header.lower().startswith('bearer '):
                    token = auth_header.split(None,1)[1].strip()
                    token_ok = _verify_v2_token(token)
                else:
                    # also accept token via cookie
                    token_cookie = request.cookies.get('Dashboard-Auth')
                    if token_cookie:
                        token_ok = _verify_v2_token(token_cookie)
            except Exception:
                token_ok = False
            if not (basic_ok or token_ok):
                return Response('Auth required', 401, {'WWW-Authenticate':'Basic realm="BlobeVM Dashboard"'})
        return fn(*args, **kwargs)
    return wrapper

@app.get('/dashboard/api/v2status')
@auth_required
def api_v2status():
    env = _read_env()
    domain = env.get('BLOBEVM_DOMAIN', '')
    running = False
    url = None
    try:
        r = subprocess.run([
            'docker', 'ps', '-q', '-f', 'name=^blobedash-v2$'
        ], capture_output=True, text=True)
        cid = r.stdout.strip()
        if cid and domain:
            running = True
            url = f'http://{domain}/Dashboard'
    except Exception:
        running = False
        url = None
    return jsonify({'running': running, 'url': url})

APP_ROOT = '/opt/blobe-vm'
MANAGER = 'blobe-vm-manager'
HOST_DOCKER_BIN = os.environ.get('HOST_DOCKER_BIN') or '/usr/bin/docker'
CONTAINER_DOCKER_BIN = os.environ.get('CONTAINER_DOCKER_BIN') or '/usr/bin/docker'
DOCKER_VOLUME_BIND = f'{HOST_DOCKER_BIN}:{CONTAINER_DOCKER_BIN}:ro'
TEMPLATE = r"""
<!doctype html><html><head><title>{{ title }}</title>
{% if favicon_url %}<link rel="icon" href="{{ favicon_url }}" />{% endif %}
<style>body{font-family:system-ui,Arial;margin:1.5rem;background:#111;color:#eee}table{border-collapse:collapse;width:100%;}th,td{padding:.5rem;border-bottom:1px solid #333}a,button{background:#2563eb;color:#fff;border:none;padding:.4rem .8rem;border-radius:4px;text-decoration:none;cursor:pointer}form{display:inline}h1{margin-top:0} .badge{background:#444;padding:.15rem .4rem;border-radius:3px;font-size:.65rem;text-transform:uppercase;margin-left:.3rem} .muted{opacity:.75} .btn-red{background:#dc2626} .btn-gray{background:#374151} .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}.green{background:#10b981}.red{background:#ef4444}.gray{background:#6b7280}.amber{background:#f59e0b}</style>
</head><body>
<h1 id="dash-title">{{ title }}</h1>
<div id=errbox style="display:none;background:#7f1d1d;color:#fff;padding:.5rem .75rem;border-radius:4px;margin:.5rem 0"></div>
<div id=v2status style="margin:.5rem 0;padding:.5rem;border:1px dashed #233;background:#071229;border-radius:6px;color:#cfe8ff">
New dashboard status: <span id="v2state">Checking…</span>
<span id="v2link"></span>
</div>
<!-- v2 dashboard status script moved to end of body -->
</body>
<script>
async function pollV2Status() {
    try {
        const res = await fetch('/dashboard/api/v2status');
        const data = await res.json();
        const el = document.getElementById('v2state');
        const linkEl = document.getElementById('v2link');
        if (data.running) {
            el.textContent = 'Running';
            if (data.url) {
                linkEl.innerHTML = `<br><a href="${data.url}" target="_blank" style="color:#4fd1c5;font-weight:bold">Open Dashboard V2</a>`;
            } else {
                linkEl.innerHTML = '';
            }
        } else {
            el.textContent = 'Stopped';
            linkEl.innerHTML = '';
        }
    } catch (e) {
        const el = document.getElementById('v2state');
        if (el) el.textContent = 'Error';
    }
}
setInterval(pollV2Status, 3000);
window.addEventListener('DOMContentLoaded', pollV2Status);
</script>
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
        <button onclick="bulkResetAll()" class="btn-red">Reset ALL VMs</button>
        <button onclick="bulkDeleteAll()" class="btn-red">Delete ALL VMs</button>
        <span class="muted" style="margin-left: .5rem">Shift+Click Check for report-only (no auto-fix)</span>
    </div>
<div style="margin:1.5rem 0 .5rem 0">
    <span class=badge>Custom domain (merged mode):</span>
    <input id=customdomain placeholder="e.g. vms.example.com" style="width:220px" />
    <button onclick="setCustomDomain()">Set domain</button>
    <span id=domainip style="margin-left:1.5rem"></span>
</div>
<div style="margin:.5rem 0;padding:.5rem;border:1px solid #222;border-radius:6px;background:#07121a">
    <strong style="display:block;margin-bottom:.25rem">Dashboard Settings</strong>
    <input id="setting-title" placeholder="Dashboard title" style="width:320px" />
    <input id="setting-favicon" placeholder="Favicon URL (http/https)" style="width:320px;margin-left:.5rem" />
    <input id="setting-v2pw" placeholder="New Dashboard v2 admin password (leave blank to keep)" style="width:420px;display:block;margin-top:.5rem" />
    <!-- Removed favicon upload -->
    <button onclick="saveSettings()" style="margin-left:.5rem">Save</button>
    <button onclick="clearFavicon()" class="btn-gray" style="margin-left:.25rem">Clear Favicon</button>
    <div id="settings-msg" class="muted" style="margin-top:.5rem"></div>
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
        const [r, r2, r3, r4] = await Promise.all([
            fetch('/dashboard/api/list'),
            fetch('/dashboard/api/modeinfo'),
            fetch('/dashboard/api/apps').catch(()=>({ok:false})),
            fetch('/dashboard/api/settings').catch(()=>({ok:false}))
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
        const settings = (r4 && r4.ok) ? await r4.json().catch(()=>({})) : {};
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
    const vmTitles = settings.vm_titles || {};
    const tb=document.getElementById('tbody');
        tb.innerHTML='';
             // Removed app options
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
            const vmTitle = vmTitles[i.name] || '';
             // add cache-busting to favicon src so uploads show up immediately
             const favSrc = `/dashboard/vm-favicon/${i.name}.ico?v=${Date.now()}`;
             tr.innerHTML=`<td><img src="${favSrc}" style="width:16px;height:16px;vertical-align:middle;margin-right:6px" onerror="this.style.display='none'"/>${i.name}<div id="vmtitle-display-${i.name}" style="font-size:.85rem;color:#9ca3af;margin-top:3px">${vmTitle||''}</div></td><td>${dot}<span class=muted>${i.status||''}</span></td><td>${portOrPath}</td><td><a href="${openUrl}" target="_blank" rel="noopener noreferrer">${openUrl}</a></td>`+
                 `<td>`+
                 `<button onclick="openVMWithUrl('${i.name}','${openUrl.replace(/'/g, "\\'")}")">Open</button>`+
                 `<button onclick="act('start','${i.name}')">Start</button>`+
                 `<button onclick="act('stop','${i.name}')">Stop</button>`+
                 `<button onclick="act('restart','${i.name}')">Restart</button>`+
                 `<button title="Shift-click for no-fix" onclick="checkVM(event,'${i.name}')" class="btn-gray">Check</button>`+
                 `<button onclick="updateVM('${i.name}')" class="btn-gray">Update</button>`+
                 `<button onclick="recreateVM('${i.name}')">Recreate</button>`+
                 `<button onclick=\"cleanVM('${i.name}')\" class=\"btn-gray\">Clean</button>`+
                 `<button onclick="resetVM('${i.name}')" class="btn-red">Reset</button>`+
                 `<button onclick="delvm('${i.name}')" class="btn-red">Delete</button>`+
                 `<div style="margin-top:.5rem">`+
                 `<input id="vmtitle-${i.name}" placeholder="Tab title" value="${vmTitle}" style="width:220px" />`+
                 `<button onclick="saveVMTitle('${i.name}')" style="margin-left:.25rem">Save Title</button>`+
                 `</div>`+
                 `</td>`;
          tb.appendChild(tr);
        });
    } catch (err) {
        console.error('[BLOBEDASH] load() error', err);
    }
}
// Check new dashboard availability and API presence
async function checkV2(){
    const el = document.getElementById('v2state');
    if(!el) return;
    el.textContent = 'Checking…';
    try{
        const r = await fetch('/Dashboard/', {cache:'no-store'});
        if(r.ok){
            el.innerHTML = 'Available — <a href="/Dashboard/" target="_blank">Open</a>';
        }else if(r.status === 404){
            el.textContent = 'Not built (no files)';
        }else{
            const txt = await r.text().catch(()=>r.statusText||'error')
            el.textContent = `HTTP ${r.status}: ${txt.slice(0,120)}`
        }
    }catch(e){
        el.textContent = 'Error contacting /Dashboard: ' + (e && e.message ? e.message : String(e))
    }
    // Check whether v2 API endpoints exist (unauthenticated probe)
    try{
        const r2 = await fetch('/dashboard/api/vm/stats', {method:'GET', cache:'no-store'});
        if(r2.status === 401){
            el.innerHTML += ' · API: present (auth required)'
        }else if(r2.ok){
            el.innerHTML += ' · API: present'
        }else if(r2.status === 404){
            el.innerHTML += ' · API: missing (404)'
        }else{
            el.innerHTML += ' · API status: ' + r2.status
        }
    }catch(e){
        el.innerHTML += ' · API probe error: ' + (e && e.message ? e.message : String(e))
    }
    // fetch server-side info for more details (requires auth)
    try{
        const r3 = await fetch('/dashboard/api/v2/info')
        if(r3.ok){
            const j = await r3.json().catch(()=>null)
            if(j && j.info){
                const info = j.info
                if(info.last_error){
                    el.innerHTML += '<div style="margin-top:6px;color:#fbb">Build error: '+(info.last_error.length>300?info.last_error.slice(0,300)+'…':info.last_error)+'</div>'
                }else if(!info.dist_exists){
                    el.innerHTML += ' · No build artifacts found'
                }else{
                    const m = info.index_mtime ? new Date(info.index_mtime*1000).toLocaleString() : ''
                    el.innerHTML += ` · Built: ${m} · files: ${info.files_count}`
                }
            }
        }
    }catch(e){ /* ignore */ }
}

// run the v2 probe at start and periodically
setTimeout(checkV2, 500);
setInterval(checkV2, 30*1000);
function recreateVM(name){
    if(!confirm('Recreate VM '+name+'?'))return;
    fetch('/dashboard/api/recreate',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:[name]})
    }).then(load);
}
function resetVM(name){
    // Strong confirmation because this permanently removes instance data
    var promptMsg = 'Reset VM ' + name + '? This will permanently remove all instance data. Type RESET to confirm.';
    var conf = prompt(promptMsg);
    if(conf !== 'RESET') return;
    try{
        fetch('/dashboard/api/reset/' + encodeURIComponent(name),{method:'POST'}).then(()=>{
            alert('Reset requested. VM will be recreated shortly.');
            load();
        }).catch(e=>{ showErr('Reset request failed: '+e); });
    }catch(e){ showErr('Reset error: '+e); }
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

function openVM(name){
    try{
        // prefer the saved VM title if available, else read from input field
        const el = document.getElementById('vmtitle-' + name);
        let t = '';
        if(el && el.value) t = el.value;
        // fallback to global dashboard title
        if(!t){
            const st = document.getElementById('setting-title');
            if(st && st.value) t = st.value;
        }
        try{ if(t) document.title = t; }catch(e){}
    }catch(e){ console.error('openVM title set error', e); }
    openLink('/dashboard/vm/' + encodeURIComponent(name) + '/');
}

function openVMWithUrl(name, url){
    try{
        // set title from saved input or global
        const el = document.getElementById('vmtitle-' + name);
        let t = '';
        if(el && el.value) t = el.value;
        if(!t){ const st = document.getElementById('setting-title'); if(st && st.value) t = st.value; }
        try{ if(t) document.title = t; }catch(e){}
    }catch(e){ console.error('openVMWithUrl title set error', e); }
    try{
        // If dashboard is running in merged mode, open the dashboard's own wrapper so
        // the server-rendered per-VM title + favicon are applied. Otherwise open the
        // provided direct URL.
        if(typeof mergedMode !== 'undefined' && mergedMode){
            try{
                // use configured basePath (e.g. /vm) so we open /vm/<name>/ on dashboard origin
                const bp = (typeof basePath !== 'undefined' && basePath) ? basePath : '/vm';
                const norm = bp.replace(/\/+$/,'');
                const wrapper = window.location.origin + norm + '/' + encodeURIComponent(name) + '/';
                openLink(wrapper);
            }catch(e){ openLink(url); }
        } else {
            openLink(url);
        }
    }catch(e){ openLink(url); }
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

function bulkResetAll(){
    var promptMsg = 'Reset ALL VMs? This will permanently remove all instance data and recreate each VM. Type RESET_ALL to confirm.';
    var conf = prompt(promptMsg);
    if(conf !== 'RESET_ALL') return;
    try{
        fetch('/dashboard/api/reset-all-instances',{method:'POST'}).then(()=>{
            alert('Reset ALL requested. VMs will be recreated shortly.');
            load();
        }).catch(e=>{ showErr('Reset ALL request failed: '+e); });
    }catch(e){ showErr('Reset ALL error: '+e); }
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

    async function loadSettings(){
        try{
            const r = await fetch('/dashboard/api/settings');
            if(!r.ok) return;
            const j = await r.json().catch(()=>({}));
            document.getElementById('setting-title').value = j.title || '';
            document.getElementById('setting-favicon').value = j.favicon_url || j.favicon || '';
            // Do not populate the v2 admin password for security; leave blank.
            const v2pwEl = document.getElementById('setting-v2pw');
            if(v2pwEl) v2pwEl.value = '';
        }catch(e){ console.error('loadSettings', e); }
    }

    async function saveSettings(){
        try{
            const title = document.getElementById('setting-title').value || '';
            const fav = document.getElementById('setting-favicon').value || '';
            const newpw = (document.getElementById('setting-v2pw') && document.getElementById('setting-v2pw').value) || '';
            const body = new URLSearchParams();
            body.append('title', title);
            body.append('favicon', fav);
            // Only send the new password when provided; empty means no change
            if(newpw !== '') body.append('new_dashboard_admin_password', newpw);
            const r = await fetch('/dashboard/api/settings', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: body});
            const j = await r.json().catch(()=>({}));
            const msg = document.getElementById('settings-msg');
            if(j && j.ok){
                if(msg) msg.textContent = 'Saved.';
                document.getElementById('dash-title').textContent = title || 'BlobeVM Dashboard';
                // update the browser tab title immediately
                try{ document.title = title || 'BlobeVM Dashboard'; }catch(e){}
                // If a favicon was saved locally, reload page to pick it up
                if(fav){
                    // small delay then reload to update favicon
                    setTimeout(()=> location.reload(), 600);
                }
            } else {
                if(msg) msg.textContent = 'Save failed';
            }
        }catch(e){ console.error('saveSettings', e); }
    }

    async function clearFavicon(){
        try{
            const title = document.getElementById('setting-title').value || '';
            const body = new URLSearchParams();
            body.append('title', title);
            body.append('favicon', '');
            const r = await fetch('/dashboard/api/settings', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: body});
            const j = await r.json().catch(()=>({}));
            if(j && j.ok){
                document.getElementById('setting-favicon').value = '';
                document.getElementById('settings-msg').textContent = 'Favicon cleared.';
                setTimeout(()=> location.reload(), 400);
            }
        }catch(e){ console.error('clearFavicon', e); }
    }

    async function uploadGlobalFavicon(){
        try{
            const inp = document.getElementById('setting-favicon-file');
            if(!inp || !inp.files || inp.files.length===0){ alert('Select a file first'); return; }
            const fd = new FormData();
            fd.append('file', inp.files[0]);
            const r = await fetch('/dashboard/api/upload-favicon', {method:'POST', body: fd});
            const j = await r.json().catch(()=>({}));
            const msg = document.getElementById('settings-msg');
            if(j && j.ok){ if(msg) msg.textContent = 'Uploaded.'; setTimeout(()=> location.reload(), 500); } else { if(msg) msg.textContent = 'Upload failed'; }
        }catch(e){ console.error('uploadGlobalFavicon', e); }
    }

    async function uploadVMFavicon(ev, name){
        try{
            const files = ev && ev.target && ev.target.files ? ev.target.files : null;
            if(!files || files.length===0){ alert('No file selected'); return; }
            const fd = new FormData();
            fd.append('file', files[0]);
            const r = await fetch('/dashboard/api/upload-vm-favicon/' + encodeURIComponent(name), {method:'POST', body: fd});
            const j = await r.json().catch(()=>({}));
            if(j && j.ok){ setTimeout(()=> location.reload(), 500); } else { alert('Upload failed'); }
        }catch(e){ console.error('uploadVMFavicon', e); }
    }

    loadSettings();

    async function saveVMTitle(name){
        try{
            const el = document.getElementById('vmtitle-' + name);
            if(!el) return;
            const title = el.value || '';
            const body = new URLSearchParams();
            body.append('title', title);
            const r = await fetch('/dashboard/api/set-vm-title/' + encodeURIComponent(name), {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: body});
            const j = await r.json().catch(()=>({}));
            if(j && j.ok){
                el.style.border = '1px solid #10b981';
                // Update the browser tab title to the saved VM title
                try{ document.title = title || 'BlobeVM'; }catch(e){}
                // Update the small per-VM title display in the list
                try{ const disp = document.getElementById('vmtitle-display-' + name); if(disp) disp.textContent = title || ''; }catch(e){}
                setTimeout(()=> el.style.border='', 900);
            }
            else { alert('Save failed'); }
        }catch(e){ console.error('saveVMTitle', e); }
    }

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

            // Update small status spans with current values/stats
            const memStat = document.getElementById('guard-memory-stat');
            const cpuStat = document.getElementById('guard-cpu-stat');
            const swapStat = document.getElementById('guard-swap-stat');
            const healthStat = document.getElementById('guard-health-stat');
            const strictMemStat = document.getElementById('guard-strictmem-stat');
            try{
                const stats = (j && j.stats) ? j.stats : null;
                if(stats && stats.mem && stats.mem.total){
                    const used = stats.mem.used || 0; const total = stats.mem.total || 0;
                    const pct = total? Math.round(100*used/total): 0;
                    if(memStat) memStat.textContent = `${(used/1024/1024).toFixed(0)}MiB / ${(total/1024/1024).toFixed(0)}MiB (${pct}%)`;
                } else {
                    if(memStat) memStat.textContent = '';
                }
                if(stats && Array.isArray(stats.containers) && stats.containers.length){
                    // find top CPU consumer among blobevm_ containers if possible
                    let top = null;
                    for(const c of stats.containers){
                        if(!top || (c.cpu || 0) > (top.cpu || 0)) top = c;
                    }
                    if(top && cpuStat) cpuStat.textContent = `${top.name}: ${ (top.cpu||0).toFixed(1) }%`;
                } else {
                    if(cpuStat) cpuStat.textContent = '';
                }
                if(stats && stats.swap && stats.swap.total){
                    const sused = stats.swap.used || 0; const stotal = stats.swap.total || 0;
                    const spct = stotal? Math.round(100*sused/stotal): 0;
                    if(swapStat) swapStat.textContent = `${(sused/1024/1024).toFixed(0)}MiB (${spct}%)`;
                } else { if(swapStat) swapStat.textContent = ''; }
                if(j && j.cfg && j.cfg.strictMemoryLimit){
                    if(strictMemStat) strictMemStat.textContent = `limit=${j.cfg.memoryLimit||'1g'}`;
                } else { if(strictMemStat) strictMemStat.textContent = ''; }
            }catch(e){ console.error('update optimizer stats', e); }
        }catch(e){ console.error('loadOptimizer', e); }
    }

    async function optimizerSet(key, val){
        await fetch('/dashboard/api/optimizer/set', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key, val})});
        await loadOptimizer();
    }

    function setRestartInterval(){
        const el = document.getElementById('restart-interval');
        if(!el) return;
        const v = parseInt(el.value);
        if(!v || v <= 0){ showErr('Enter a positive number of hours'); return; }
        optimizerSet('restartIntervalHours', v);
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
        <label><input id="optimizer-enabled" type="checkbox" onchange="optimizerSet('enabled', this.checked)"> Optimizer Enabled <span id="opt-enabled-stat" class="muted"></span></label>
        <label><input id="guard-memory" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {memory:this.checked}))"> Memory Guard <span id="guard-memory-stat" class="muted"></span></label>
        <label><input id="guard-cpu" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {cpu:this.checked}))"> CPU Guard <span id="guard-cpu-stat" class="muted"></span></label>
        <label><input id="guard-swap" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {swap:this.checked}))"> Swap Guard <span id="guard-swap-stat" class="muted"></span></label>
        <label><input id="guard-health" type="checkbox" onchange="optimizerSet('guards', Object.assign(({}), {health:this.checked}))"> Health Guard <span id="guard-health-stat" class="muted"></span></label>
        <label><input id="guard-strictmem" type="checkbox" onchange="optimizerSet('strictMemoryLimit', this.checked)"> Strict Memory Limits <span id="guard-strictmem-stat" class="muted"></span></label>
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
            # Allow basic auth via BUSER/BPASS or a valid v2 token (so dashboard_v2 can reuse existing APIs)
            auth_header = request.headers.get('Authorization')
            basic_ok = check_auth(auth_header)
            token_ok = False
            try:
                if auth_header and auth_header.lower().startswith('bearer '):
                    token = auth_header.split(None,1)[1].strip()
                    token_ok = _verify_v2_token(token)
                else:
                    # also accept token via cookie
                    token_cookie = request.cookies.get('Dashboard-Auth')
                    if token_cookie:
                        token_ok = _verify_v2_token(token_cookie)
            except Exception:
                token_ok = False
            if not (basic_ok or token_ok):
                return Response('Auth required', 401, {'WWW-Authenticate':'Basic realm="BlobeVM Dashboard"'})
        return fn(*args, **kwargs)
    return wrapper

# --- New dashboard (v2) auth helpers ---
_DASH_V2_SECRET = os.environ.get('DASH_V2_SECRET') or os.environ.get('SECRET_KEY') or 'blobevm-secret'

def _get_v2_password():
    # read password stored in dashboard settings (managed by old dashboard)
    cfg = _load_dashboard_settings()
    return cfg.get('new_dashboard_admin_password')

def _sign_v2_token(payload: str) -> str:
    mac = hmac.new(_DASH_V2_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    token = f"{payload}:{mac}"
    return base64.urlsafe_b64encode(token.encode('utf-8')).decode('utf-8')

def _verify_v2_token(token_b64: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token_b64.encode('utf-8')).decode('utf-8')
        parts = raw.rsplit(':', 1)
        if len(parts) != 2:
            return False
        payload, mac = parts
        expected = hmac.new(_DASH_V2_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return False
        # payload format: expiry:random
        exp_str = payload.split(':',1)[0]
        exp = int(exp_str)
        return time.time() < exp
    except Exception:
        return False

def v2_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Accept Bearer token in Authorization header or X-Auth-Token
        auth = request.headers.get('Authorization','')
        token = None
        if auth.lower().startswith('bearer '):
            token = auth.split(None,1)[1].strip()
        if not token:
            token = request.headers.get('X-Auth-Token') or request.cookies.get('Dashboard-Auth')
        if not token or not _verify_v2_token(token):
            return Response('Unauthorized', 401)
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


def _settings_path():
    return os.path.join(_state_dir(), 'dashboard_settings.json')


def _load_dashboard_settings():
    p = _settings_path()
    try:
        if os.path.isfile(p):
            with open(p, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    # defaults
    return {'title': 'BlobeVM Dashboard', 'favicon': ''}


def _save_dashboard_settings(cfg: dict):
    p = _settings_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            json.dump(cfg, f)
        return True
    except Exception:
        return False


@app.get('/dashboard/favicon.ico')
def dashboard_favicon():
    # Serve a saved favicon if present under state_dir/dashboard/favicon.ico
    fav_path = os.path.join(_state_dir(), 'dashboard', 'favicon.ico')
    if os.path.isfile(fav_path):
        return send_from_directory(os.path.dirname(fav_path), os.path.basename(fav_path))
    # If no local file, try to redirect to configured favicon URL
    cfg = _load_dashboard_settings()
    if cfg.get('favicon'):
        return '', 302, {'Location': cfg.get('favicon')}
    # Not found
    abort(404)


@app.get('/dashboard/api/settings')
@auth_required
def api_get_settings():
    cfg = _load_dashboard_settings()
    # Provide a favicon URL that the template can consume: prefer local served path if file exists
    fav_local = os.path.join(_state_dir(), 'dashboard', 'favicon.ico')
    if os.path.isfile(fav_local):
        cfg['favicon_url'] = '/dashboard/favicon.ico'
    else:
        cfg['favicon_url'] = cfg.get('favicon','')
    return jsonify(cfg)


@app.post('/dashboard/api/settings')
@auth_required
def api_set_settings():
    title = request.values.get('title','').strip()
    favicon = request.values.get('favicon','').strip()
    new_pw = request.values.get('new_dashboard_admin_password', None)
    cfg = _load_dashboard_settings()
    if title:
        cfg['title'] = title
    # Allow setting the v2 dashboard admin password from the old dashboard settings UI
    try:
        if new_pw is not None:
            # store raw (it is only editable from the old dashboard as requested)
            cfg['new_dashboard_admin_password'] = new_pw.strip()
    except Exception:
        pass
    # If favicon is empty string, clear both saved file and url
    if favicon == '':
        cfg['favicon'] = ''
        # remove local file if exists
        try:
            fav_local = os.path.join(_state_dir(), 'dashboard', 'favicon.ico')
            if os.path.isfile(fav_local):
                os.remove(fav_local)
        except Exception:
            pass
    else:
        # treat favicon as URL: try to download and save as favicon.ico under state_dir/dashboard/
        if favicon.lower().startswith('http://') or favicon.lower().startswith('https://'):
            try:
                resp = urlrequest.urlopen(favicon, timeout=8)
                data = resp.read()
                try:
                    ddir = os.path.join(_state_dir(), 'dashboard')
                    os.makedirs(ddir, exist_ok=True)
                    with open(os.path.join(ddir, 'favicon.ico'), 'wb') as f:
                        f.write(data)
                    # prefer local serve
                    cfg['favicon'] = ''
                except Exception:
                    # fallback to storing URL
                    cfg['favicon'] = favicon
            except Exception:
                # if download failed, just store URL so template can reference it
                cfg['favicon'] = favicon
        else:
            # treat as direct URL or path; store it
            cfg['favicon'] = favicon
        
    ok = _save_dashboard_settings(cfg)
    return jsonify({'ok': bool(ok)})


# Upload endpoints: accept multipart file uploads for global and per-VM favicons
@app.post('/dashboard/api/upload-favicon')
@auth_required
def api_upload_favicon():
    # Expect a form file field named 'file'
    f = None
    try:
        f = request.files.get('file')
    except Exception:
        pass
    if not f:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400
    try:
        ddir = os.path.join(_state_dir(), 'dashboard')
        os.makedirs(ddir, exist_ok=True)
        outp = os.path.join(ddir, 'favicon.ico')
        # Save file bytes
        f.save(outp)
        # clear stored URL in settings so local file is preferred
        cfg = _load_dashboard_settings()
        cfg['favicon'] = ''
        _save_dashboard_settings(cfg)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/upload-vm-favicon/<name>')
@auth_required
def api_upload_vm_favicon(name):
    f = None
    try:
        f = request.files.get('file')
    except Exception:
        pass
    if not f:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400
    try:
        ddir = os.path.join(_state_dir(), 'dashboard', 'vm-fav')
        os.makedirs(ddir, exist_ok=True)
        # normalize name
        safe = re.sub(r'[^A-Za-z0-9_-]', '_', name)
        outp = os.path.join(ddir, f"{safe}.ico")
        f.save(outp)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


    # --- Dashboard v2 static serving and auth endpoints ---
    @app.post('/Dashboard/api/auth/login')
    def dashboard_v2_login():
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({'ok': False}), 400
        pw = data.get('password','')
        cfg_pw = _get_v2_password()
        if not cfg_pw:
            # not configured
            return jsonify({'ok': False, 'error': 'not-configured'}), 404
        if pw != cfg_pw:
            return jsonify({'ok': False, 'error': 'invalid'}), 401
        exp = int(time.time() + 24*3600)
        payload = f"{exp}:{os.urandom(8).hex()}"
        token = _sign_v2_token(payload)
        resp = jsonify({'ok': True, 'token': token, 'expiry': exp})
        # Also set cookie for browser convenience
        resp.set_cookie('Dashboard-Auth', token, httponly=True, samesite='Lax')
        return resp


    @app.get('/Dashboard/api/auth/status')
    def dashboard_v2_status():
        # Check Authorization header or cookie
        auth = request.headers.get('Authorization','')
        token = None
        if auth.lower().startswith('bearer '):
            token = auth.split(None,1)[1].strip()
        if not token:
            token = request.cookies.get('Dashboard-Auth')
        ok = bool(token and _verify_v2_token(token))
        return jsonify({'ok': ok})


    @app.route('/Dashboard/', defaults={'path': ''})
    @app.route('/Dashboard/<path:path>')
    def serve_dashboard_v2(path):
        # Serve built files from dashboard_v2/dist if present, otherwise serve dev index
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dashboard_v2'))
        dist = os.path.join(base, 'dist')
        # If requested file exists under dist, serve it
        if path:
            cand = os.path.join(dist, path)
            if os.path.isfile(cand):
                return send_from_directory(dist, path)
            # try nested static path
            static_dir = os.path.join(base, 'src')
            cand2 = os.path.join(static_dir, path)
            if os.path.isfile(cand2):
                return send_from_directory(static_dir, path)
        # Serve dist index if available
        indexcand = os.path.join(dist, 'index.html')
        if os.path.isfile(indexcand):
            return send_from_directory(dist, 'index.html')
        # Fallback to dev index.html in project
        dev_index = os.path.join(base, 'index.html')
        if os.path.isfile(dev_index):
            return send_from_directory(base, 'index.html')
        return 'Dashboard v2 not built', 404


    # Serve dashboard v2 production assets requested from absolute `/assets/*` paths
    @app.route('/assets/<path:path>')
    def serve_dashboard_v2_root_assets(path):
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dashboard_v2'))
        assets_dir = os.path.join(base, 'dist', 'assets')
        cand = os.path.join(assets_dir, path)
        if os.path.isfile(cand):
            return send_from_directory(assets_dir, path)
        # not found here - 404 and let other handlers handle it if needed
        return 'Not found', 404


    # Also handle requests that include the Dashboard prefix explicitly
    @app.route('/Dashboard/assets/<path:path>')
    def serve_dashboard_v2_prefixed_assets(path):
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dashboard_v2'))
        assets_dir = os.path.join(base, 'dist', 'assets')
        cand = os.path.join(assets_dir, path)
        if os.path.isfile(cand):
            return send_from_directory(assets_dir, path)
        return 'Not found', 404


@app.post('/dashboard/api/set-vm-title/<name>')
@auth_required
def api_set_vm_title(name):
    title = request.values.get('title','').strip()
    cfg = _load_dashboard_settings()
    vm_titles = cfg.get('vm_titles', {}) if isinstance(cfg.get('vm_titles', {}), dict) else {}
    if title:
        vm_titles[name] = title
    else:
        # clear title
        if name in vm_titles:
            vm_titles.pop(name, None)
    cfg['vm_titles'] = vm_titles
    ok = _save_dashboard_settings(cfg)
    # Attempt to propagate the title into instance metadata so the VM container
    # can be recreated with updated TITLE env. This uses the `blobe-vm-manager`
    # CLI if available. Run asynchronously and tolerate failures.
    def _propagate_title(n, t):
        try:
            mgr = shutil.which(MANAGER) or '/usr/local/bin/blobe-vm-manager' or os.path.join(APP_ROOT,'server','blobe-vm-manager')
            if not mgr or not os.path.exists(mgr):
                # manager not found; nothing to do
                return
            # Call manager set-title; allow empty title to clear
            # Use Popen so we don't block the request
            subprocess.Popen([mgr, 'set-title', n, t], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    try:
        # Fire-and-forget propagation
        threading.Thread(target=_propagate_title, args=(name, title), daemon=True).start()
    except Exception:
        pass

    return jsonify({'ok': bool(ok)})


@app.get('/dashboard/vm-favicon/<name>.ico')
def dashboard_vm_favicon(name):
    # Serve per-VM favicon if exists, otherwise redirect to main favicon (which may itself redirect)
    ddir = os.path.join(_state_dir(), 'dashboard', 'vm-fav')
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', name)
    candidate = os.path.join(ddir, f"{safe}.ico")
    if os.path.isfile(candidate):
        return send_from_directory(ddir, os.path.basename(candidate))
    # fallback to global favicon route
    return '', 302, {'Location': '/dashboard/favicon.ico'}


@app.get('/dashboard/vm/<name>/')
def dashboard_vm_wrapper(name):
    # Public wrapper page that opens the VM inside an iframe while setting the tab title and favicon.
    # Resolve VM URL and per-VM title/fav
    url = _build_vm_url(name) or ''
    cfg = _load_dashboard_settings()
    vm_titles = cfg.get('vm_titles', {}) if isinstance(cfg.get('vm_titles', {}), dict) else {}
    # Default VM title: use saved per-VM title if present, else 'BlobeVM - <name>'
    title = vm_titles.get(name) or f"EpicVM - {name}"
    # prefer per-vm favicon if exists
    vm_fav_path = os.path.join(_state_dir(), 'dashboard', 'vm-fav', f"{re.sub(r'[^A-Za-z0-9_-]', '_', name)}.ico")
    if os.path.isfile(vm_fav_path):
        # append server-side cache-bust using timestamp so updated favicons are picked up
        fav_url = f'/dashboard/vm-favicon/{name}.ico?v={int(time.time())}'
    else:
        # fall back to global
        fav_local = os.path.join(_state_dir(), 'dashboard', 'favicon.ico')
        if os.path.isfile(fav_local):
            fav_url = '/dashboard/favicon.ico'
        else:
            fav_url = cfg.get('favicon','')
    # Render simple page with title, favicon and iframe
    # Use json to safely embed title and fav_url in inline script
    page = f'''<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'''
    if fav_url:
        page += f"<link rel=\"icon\" href=\"{fav_url}\" />"
    # Inline script ensures the wrapper page sets title + favicon even if browser cached or modified
    try:
        js_title = json.dumps(title)
        js_fav = json.dumps(fav_url)
    except Exception:
        js_title = '"%s"' % (title.replace('"','\"'))
        js_fav = '"%s"' % (fav_url.replace('"','\"'))
    # Build script using concatenation to avoid f-string brace escaping issues
    page += (
        "<script>try{document.title=" + js_title +
        ";var __fav=" + js_fav +
        ";if(__fav){var l=document.querySelector('link[rel=\"icon\"]')||document.createElement('link');l.rel='icon';l.href=__fav; if(!document.head.contains(l))document.head.appendChild(l);} }catch(e){}</script>"
    )
    page += f"</head><body style='margin:0;padding:0;overflow:hidden;background:#000'><iframe src=\"{url}\" style=\"position:fixed;top:0;left:0;width:100%;height:100%;border:none;background:#000\"></iframe></body></html>"
    return page


# Register an alias route under the configured base path (e.g. /vm/<name>/) so merged-mode
# users who visit /vm/<name>/ get the same wrapper behaviour. We read BASE_PATH from state .env
# and add a rule at import time after the function exists.
try:
    try:
        envbp = _read_env().get('BASE_PATH', '/vm')
    except Exception:
        envbp = '/vm'
    if not envbp:
        envbp = '/vm'
    bp = envbp.rstrip('/')
    if not bp.startswith('/'):
        bp = '/' + bp
    # Avoid adding duplicate rule for same path
    alias_rule = f"{bp}/<name>/"
    # Only add if different from /dashboard/vm
    if alias_rule != '/dashboard/vm/<name>/':
        try:
            app.add_url_rule(alias_rule, endpoint=f'dashboard_vm_wrapper_alias', view_func=dashboard_vm_wrapper, methods=['GET'])
        except Exception:
            pass
except Exception:
    pass


def python_gather_stats():
    out = {'mem': {}, 'swap': {}, 'containers': []}
    try:
        free = subprocess.check_output(['free', '-b'], text=True)
        lines = free.split('\n')
        memLine = next((l for l in lines if l.lower().startswith('mem:')), '')
        parts = re.split(r'\s+', memLine.strip()) if memLine else []
        if len(parts) >= 3:
            out['mem']['total'] = int(parts[1])
            out['mem']['used'] = int(parts[2])
        swapLine = next((l for l in lines if l.lower().startswith('swap:')), '')
        sp = re.split(r'\s+', swapLine.strip()) if swapLine else []
        if len(sp) >= 3:
            out['swap']['total'] = int(sp[1])
            out['swap']['used'] = int(sp[2])
    except Exception:
        pass
    # docker stats fallback
    try:
        d = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', "{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}"], text=True)
        for l in d.splitlines():
            if not l.strip():
                continue
            parts = l.split('|')
            if len(parts) >= 4:
                name = parts[0]
                cpu = 0.0
                try:
                    cpu = float(parts[1].strip().replace('%',''))
                except Exception:
                    cpu = 0.0
                memperc = 0.0
                try:
                    memperc = float(parts[2].strip().replace('%',''))
                except Exception:
                    memperc = 0.0
                memusage = parts[3].strip()
                # attempt to parse bytes from memusage like '12.3MiB / 1.95GiB'
                m = re.search(r'([0-9.]+)\s*([KMG]i?)B', memusage)
                memBytes = 0
                if m:
                    n = float(m.group(1)); u = m.group(2).upper()
                    mul = 1024
                    if u.startswith('M'):
                        mul = 1024*1024
                    elif u.startswith('G'):
                        mul = 1024*1024*1024
                    memBytes = int(n * mul)
                out['containers'].append({'name': name, 'cpu': cpu, 'memperc': memperc, 'memBytes': memBytes})
    except Exception:
        pass
    return out

@app.post('/dashboard/api/set-domain')
@auth_required
def api_set_domain():
    dom = request.values.get('domain','').strip()
    if not dom:
        return jsonify({'ok': False, 'error': 'No domain'}), 400
    # Persist the domain
    _write_env_kv({'BLOBEVM_DOMAIN': dom})
    # Start/stop v2 dashboard container based on domain
    dashboard_v2_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'dashboard_v2'))
    dist_path = os.path.join(dashboard_v2_path, 'dist')
    def start_v2_dashboard():
        # Build if needed
        if not os.path.isdir(dist_path) or not os.path.isfile(os.path.join(dist_path, 'index.html')):
            try:
                subprocess.run(['npm', 'install'], cwd=dashboard_v2_path, check=True)
                subprocess.run(['npm', 'run', 'build'], cwd=dashboard_v2_path, check=True)
            except Exception as e:
                print(f"Failed to build dashboard_v2: {e}")
        # Remove any existing container
        subprocess.run(['docker', 'rm', '-f', 'blobedash-v2'], check=False)
        # Start container
        subprocess.run([
            'docker', 'run', '-d', '--name', 'blobedash-v2', '--restart', 'unless-stopped',
            '-p', '4173:4173',
            '-v', f'{dist_path}:/usr/share/nginx/html:ro',
            'nginx:alpine'
        ], check=False)
    def stop_v2_dashboard():
        subprocess.run(['docker', 'rm', '-f', 'blobedash-v2'], check=False)
    if dom:
        start_v2_dashboard()
    else:
        stop_v2_dashboard()
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

    # Start/recreate v2 dashboard as a Docker container in production mode
    port = dash_port or direct_start
    # Remove any existing v2 dashboard container
    _docker('rm', '-f', 'blobedash-v2')
    # Build the v2 dashboard if not already built (optional: could be handled elsewhere)
    dashboard_v2_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'dashboard_v2'))
    dist_path = os.path.join(dashboard_v2_path, 'dist')
    if not os.path.isdir(dist_path) or not os.path.isfile(os.path.join(dist_path, 'index.html')):
        # Try to build if not present
        try:
            subprocess.run(['npm', 'install'], cwd=dashboard_v2_path, check=True)
            subprocess.run(['npm', 'run', 'build'], cwd=dashboard_v2_path, check=True)
        except Exception as e:
            print(f"Failed to build dashboard_v2: {e}")
    # Start the v2 dashboard container to serve static files
    _docker('run', '-d', '--name', 'blobedash-v2', '--restart', 'unless-stopped',
        '-p', f'{port}:4173',
        '-v', f'{dist_path}:/usr/share/nginx/html:ro',
        'nginx:alpine')


@app.get('/dashboard')
@auth_required
def root():
    cfg = _load_dashboard_settings()
    fav = ''
    fav_local = os.path.join(_state_dir(), 'dashboard', 'favicon.ico')
    if os.path.isfile(fav_local):
        fav = '/dashboard/favicon.ico'
    else:
        fav = cfg.get('favicon','')
    title = cfg.get('title', 'BlobeVM Dashboard')

    # Only show v2 dashboard link if custom domain is set and container is running
    dashboard_v2_url = None
    try:
        env = _read_env()
        domain = env.get('BLOBEVM_DOMAIN', '')
        if domain:
            # Check if container is running
            r = subprocess.run([
                'docker', 'ps', '-q', '-f', 'name=^blobedash-v2$'
            ], capture_output=True, text=True)
            cid = r.stdout.strip()
            if cid:
                dashboard_v2_url = f'http://{domain}/Dashboard'
    except Exception:
        dashboard_v2_url = None

    return render_template_string(TEMPLATE, title=title, favicon_url=fav, dashboard_v2_url=dashboard_v2_url)

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


def _read_proc_stat_cpu():
    try:
        with open('/proc/stat', 'r') as f:
            for line in f:
                if line.startswith('cpu '):
                    parts = line.split()[1:]
                    vals = list(map(int, parts))
                    return vals
    except Exception:
        return None


def _calc_cpu_percent(interval=0.08):
    a = _read_proc_stat_cpu()
    if not a:
        return None
    time.sleep(interval)
    b = _read_proc_stat_cpu()
    if not b:
        return None
    suma = sum(a)
    sumb = sum(b)
    idle_a = a[3] if len(a) > 3 else 0
    idle_b = b[3] if len(b) > 3 else 0
    busy = (sumb - suma) - (idle_b - idle_a)
    total = sumb - suma
    try:
        pct = (busy / total) * 100.0 if total > 0 else 0.0
    except Exception:
        pct = 0.0
    return round(pct, 2)


def _get_system_stats():
    # Return a dict with cpu, memory, disk, network, uptime, temps
    try:
        stats = {}
        # CPU
        if psutil:
            per = psutil.cpu_percent(interval=0.08, percpu=True)
            stats['cpu'] = {
                'cores': psutil.cpu_count(logical=True),
                'usage': round(sum(per)/len(per),2) if per else 0.0,
                'per_core': [round(p,2) for p in per]
            }
        else:
            pct = _calc_cpu_percent()
            cores = os.cpu_count() or 1
            stats['cpu'] = {'cores': cores, 'usage': pct or 0.0, 'per_core': []}

        # Memory
        if psutil:
            vm = psutil.virtual_memory()
            stats['memory'] = {'total': vm.total, 'available': vm.available, 'used': vm.used, 'percent': vm.percent}
        else:
            mem = {}
            try:
                with open('/proc/meminfo','r') as f:
                    for line in f:
                        k,v = line.split(':',1)
                        mem[k.strip()] = int(re.findall(r'\d+', v)[0]) * 1024
                total = mem.get('MemTotal',0)
                free = mem.get('MemFree',0) + mem.get('Buffers',0) + mem.get('Cached',0)
                used = total - free
                pct = round((used/total)*100,2) if total>0 else 0.0
                stats['memory'] = {'total': total, 'available': free, 'used': used, 'percent': pct}
            except Exception:
                stats['memory'] = {'total': 0, 'available':0, 'used':0, 'percent':0}

        # Disk: list root and mounted partitions
        disks = []
        try:
            if psutil:
                for part in psutil.disk_partitions(all=False):
                    try:
                        u = psutil.disk_usage(part.mountpoint)
                        disks.append({'mountpoint': part.mountpoint, 'total': u.total, 'used': u.used, 'free': u.free, 'percent': u.percent})
                    except Exception:
                        pass
            else:
                root = shutil.disk_usage('/')
                disks.append({'mountpoint': '/', 'total': root.total, 'used': root.used, 'free': root.free, 'percent': round((root.used/root.total)*100,2) if root.total>0 else 0})
        except Exception:
            disks = []
        stats['disk'] = disks

        # Network
        try:
            if psutil:
                net = psutil.net_io_counters(pernic=False)
                stats['network'] = {'rx_bytes': net.bytes_recv, 'tx_bytes': net.bytes_sent}
            else:
                rx = 0; tx = 0
                with open('/proc/net/dev','r') as f:
                    for line in f.readlines()[2:]:
                        parts = line.split()
                        if len(parts) < 17:
                            continue
                        iface = parts[0].strip(':')
                        if iface == 'lo':
                            continue
                        rx += int(parts[1]); tx += int(parts[9])
                stats['network'] = {'rx_bytes': rx, 'tx_bytes': tx}
        except Exception:
            stats['network'] = {'rx_bytes':0,'tx_bytes':0}

        # Uptime and loadavg
        try:
            if psutil:
                stats['uptime'] = int(time.time() - psutil.boot_time())
            else:
                with open('/proc/uptime','r') as f:
                    stats['uptime'] = int(float(f.readline().split()[0]))
        except Exception:
            stats['uptime'] = 0
        try:
            stats['loadavg'] = list(os.getloadavg())
        except Exception:
            stats['loadavg'] = []

        # Temperatures: try psutil sensors, otherwise read thermal zones
        temps = {}
        try:
            if psutil:
                try:
                    st = psutil.sensors_temperatures()
                    for k,v in st.items():
                        temps[k] = [{'label': t.label or '', 'current': t.current} for t in v]
                except Exception:
                    temps = {}
            else:
                tzs = []
                base = '/sys/class/thermal'
                if os.path.isdir(base):
                    for name in os.listdir(base):
                        if name.startswith('thermal_zone'):
                            try:
                                p = os.path.join(base, name, 'temp')
                                with open(p,'r') as f:
                                    v = int(f.read().strip())
                                    temps[name] = [{'label':'', 'current': v/1000.0}]
                            except Exception:
                                pass
        except Exception:
            temps = {}
        stats['temps'] = temps

        return stats
    except Exception:
        return {'cpu':{}, 'memory':{}, 'disk':[], 'network':{}, 'uptime':0, 'loadavg':[], 'temps':{}}


@app.get('/Dashboard/api/stats')
@v2_auth_required
def dashboard_v2_stats():
    s = _get_system_stats()
    return jsonify(s)


@app.get('/dashboard/api/stats')
@v2_auth_required
def dashboard_v2_stats_alias():
    return dashboard_v2_stats()


@app.post('/dashboard/api/auth/login')
def dashboard_v2_login_alias():
    return dashboard_v2_login()


@app.get('/dashboard/api/auth/status')
def dashboard_v2_status_alias():
    return dashboard_v2_status()


@app.get('/Dashboard/api/vm/logs/<name>')
@v2_auth_required
def dashboard_v2_vm_logs(name):
    # Return last 400 lines of docker logs for the named VM container (blobevm_<name>)
    cname = f'blobevm_{name}'
    try:
        out = subprocess.check_output(['docker', 'logs', '--tail', '400', cname], stderr=subprocess.STDOUT, text=True)
        return jsonify({'ok': True, 'logs': out})
    except subprocess.CalledProcessError as e:
        # Return whatever output available
        return jsonify({'ok': False, 'error': str(e), 'logs': getattr(e, 'output', '')}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/vm/logs/<name>')
@v2_auth_required
def dashboard_v2_vm_logs_alias(name):
    return dashboard_v2_vm_logs(name)


@app.get('/Dashboard/api/vm/stats')
@v2_auth_required
def dashboard_v2_vm_stats():
    """Return per-VM CPU and memory percentages by calling `docker stats --no-stream`.
    The result maps VM name (without the `blobevm_` prefix) to {'cpu_percent': float, 'mem_percent': float}.
    """
    try:
        out = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', '{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}'], text=True)
    except subprocess.CalledProcessError as e:
        return jsonify({'ok': False, 'error': str(e), 'output': getattr(e, 'output', '')}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    stats = {}
    try:
        for line in out.splitlines():
            if not line.strip():
                continue
            try:
                parts = line.split('|')
                cname = parts[0].strip()
                cpu_raw = parts[1].strip() if len(parts) > 1 else ''
                mem_raw = parts[2].strip() if len(parts) > 2 else ''
                # strip trailing percent sign
                cpu = 0.0
                mem = 0.0
                try:
                    cpu = float(cpu_raw.strip().rstrip('%'))
                except Exception:
                    cpu = 0.0
                try:
                    mem = float(mem_raw.strip().rstrip('%'))
                except Exception:
                    mem = 0.0
                # Normalize VM name if container is named blobevm_<name>
                vmname = cname
                if vmname.startswith('blobevm_'):
                    vmname = vmname[len('blobevm_'):]
                stats[vmname] = {'cpu_percent': round(cpu,2), 'mem_percent': round(mem,2), 'container_name': cname}
            except Exception:
                continue
        return jsonify({'ok': True, 'vms': stats})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/vm/stats')
@v2_auth_required
def dashboard_v2_vm_stats_alias():
    return dashboard_v2_vm_stats()


@app.get('/dashboard/api/v2/info')
@auth_required
def dashboard_v2_info():
    """Return information about the v2 build files and any recorded last-error file.
    This is intended for the legacy dashboard UI to show why the new dashboard
    may not be available (e.g., not built or build errors).
    """
    try:
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dashboard_v2'))
        dist = os.path.join(base, 'dist')
        info = {'dist_exists': False, 'index_mtime': None, 'files_count': 0, 'last_error': None}
        indexcand = os.path.join(dist, 'index.html')
        if os.path.isfile(indexcand):
            info['dist_exists'] = True
            info['index_mtime'] = int(os.path.getmtime(indexcand))
            # count files under dist
            cnt = 0
            for root, dirs, files in os.walk(dist):
                for f in files:
                    cnt += 1
            info['files_count'] = cnt
        # include any last error file if present (created by build step or admin)
        lasterr = os.path.join(base, 'last_error.txt')
        if os.path.isfile(lasterr):
            try:
                with open(lasterr, 'r') as fh:
                    data = fh.read(4096)
                    info['last_error'] = data
            except Exception:
                info['last_error'] = 'failed to read last_error.txt'
        return jsonify({'ok': True, 'info': info})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/Dashboard/api/v2/info')
@auth_required
def dashboard_v2_info_alias():
    return dashboard_v2_info()


@app.post('/Dashboard/api/vm/exec/<name>')
@v2_auth_required
def dashboard_v2_vm_exec(name):
    """Execute a single command inside the VM container named `blobevm_<name>`.
    Expects JSON payload: {"cmd": "<command string>"} and returns stdout/stderr.
    This is intended for short-lived commands (timeout 10s) and requires the
    Flask process to have access to the host Docker CLI.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        cmd = data.get('cmd') if isinstance(data, dict) else None
        if not cmd or not isinstance(cmd, str):
            return jsonify({'ok': False, 'error': 'missing cmd'}), 400
        cname = f'blobevm_{name}'
        # Try bash first, fallback to sh
        exec_cmds = [
            ['docker', 'exec', cname, '/bin/bash', '-lc', cmd],
            ['docker', 'exec', cname, '/bin/sh', '-lc', cmd]
        ]
        last_exc = None
        for ec in exec_cmds:
            try:
                proc = subprocess.run(ec, capture_output=True, text=True, timeout=10)
                return jsonify({'ok': proc.returncode == 0, 'returncode': proc.returncode, 'output': proc.stdout, 'error_output': proc.stderr})
            except subprocess.TimeoutExpired as e:
                return jsonify({'ok': False, 'error': 'timeout', 'output': getattr(e, 'output', ''), 'stderr': getattr(e, 'stderr', '')}), 504
            except Exception as e:
                last_exc = e
                continue
        # If we get here, no exec succeeded
        return jsonify({'ok': False, 'error': str(last_exc) if last_exc else 'exec failed'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/vm/exec/<name>')
@v2_auth_required
def dashboard_v2_vm_exec_alias(name):
    return dashboard_v2_vm_exec(name)


@app.post('/dashboard/api/reset/<name>')
@auth_required
def api_reset(name):
    """Reset a VM by deleting it and creating a fresh instance.
    This runs in the background and returns immediately. Caller must ensure
    they really want to purge instance data.
    """
    try:
        def worker(vm_name):
            try:
                # Use manager delete which should remove container and instance data
                subprocess.run([MANAGER, 'delete', vm_name], capture_output=True, text=True)
            except Exception:
                pass
            try:
                # Create a fresh instance and start it
                subprocess.run([MANAGER, 'create', vm_name], capture_output=True, text=True)
            except Exception:
                pass
            try:
                subprocess.run([MANAGER, 'start', vm_name], capture_output=True, text=True)
            except Exception:
                pass
        threading.Thread(target=worker, args=(name,), daemon=True).start()
        return jsonify({'ok': True, 'started': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

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


@app.post('/dashboard/api/reset-all-instances')
@auth_required
def api_reset_all_instances():
    """Reset all known instances by deleting and recreating them in background."""
    try:
        # Determine instance names
        try:
            names = [i['name'] for i in manager_json_list()]
        except Exception:
            # fallback: scan instances directory
            inst_root = os.path.join(_state_dir(), 'instances')
            try:
                names = [n for n in os.listdir(inst_root) if os.path.isdir(os.path.join(inst_root, n))]
            except Exception:
                names = []

        def worker(all_names):
            for n in all_names:
                try:
                    subprocess.run([MANAGER, 'delete', n], capture_output=True, text=True)
                except Exception:
                    pass
                try:
                    subprocess.run([MANAGER, 'create', n], capture_output=True, text=True)
                except Exception:
                    pass
                try:
                    subprocess.run([MANAGER, 'start', n], capture_output=True, text=True)
                except Exception:
                    pass

        threading.Thread(target=worker, args=(names,), daemon=True).start()
        return jsonify({'ok': True, 'started': True, 'count': len(names)})
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
    """Return optimizer status and stats via embedded optimizer module."""
    try:
        s = dash_optimizer.status()
        return jsonify({'ok': True, 'cfg': s.get('cfg'), 'stats': s.get('stats'), 'lastRestart': s.get('lastRestart')})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/run-once')
@auth_required
def api_optimizer_run_once():
    # Start a background run of the embedded optimizer
    def worker():
        try:
            dash_optimizer.run_once()
        except Exception as e:
            dash_optimizer.log(f'run-once worker error {e}')
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
    try:
        dash_optimizer.set_config(key, val)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/optimizer/logs')
@auth_required
def api_optimizer_logs():
    try:
        t = dash_optimizer.tail_logs()
        if t is None or t == '':
            return jsonify({'ok': False, 'error': 'no logs'}), 404
        return Response(t, mimetype='text/plain')
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
    try:
        dash_optimizer.start_background_loop()
    except Exception:
        pass
    app.run(host='0.0.0.0', port=5000)
