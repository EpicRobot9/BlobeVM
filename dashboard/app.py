#!/usr/bin/env python3
import os, json, subprocess, shlex, base64, socket, threading, time, re
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
<table><thead><tr><th>Name</th><th>Status</th><th>Port/Path</th><th>Backend</th><th>URL</th><th>Tunnel</th><th>Actions</th></tr></thead><tbody id=tbody></tbody></table>
<div style="margin:1rem 0 2rem 0">
        <button onclick="bulkRecreate()">Recreate ALL VMs</button>
        <button onclick="bulkRebuildAll()">Rebuild ALL VMs</button>
        <button onclick="bulkUpdateAndRebuild()">Update & Rebuild ALL VMs</button>
        <button onclick="pruneDocker()" class="btn-gray">Prune Docker</button>
        <button onclick="bulkDeleteAll()" class="btn-red">Delete ALL VMs</button>
        <span class="muted" style="margin-left: .5rem">Shift+Click Check for report-only (no auto-fix)</span>
    </div>
<div style="margin:1.5rem 0 .5rem 0">
    <span class=badge>Public IP Dashboard:</span>
    <input id=publicip placeholder="e.g. 72.60.29.204" style="width:220px" />
    <input id=publicipdashport placeholder="port (e.g. 80)" style="width:80px; margin-left:.5rem" type="number" value="80" />
    <button onclick="setPublicIP()">Set public access</button>
    <button class="btn-gray" onclick="disablePublicIP()">Disable</button>
    <span id=publicipstatus style="margin-left:1rem" class=muted></span>
</div>
<div style="margin:1.5rem 0 .5rem 0">
    <span class=badge>Enable Merged Mode (Proxy):</span>
    <input id=mergedport placeholder="port (e.g. 80)" style="width:80px; margin-left:.5rem" type="number" value="80" />
    <button onclick="enableMergedMode()">Enable merged mode</button>
    <button class="btn-gray" onclick="disableMergedMode()">Disable</button>
    <span id=mergedstatus style="margin-left:1rem" class=muted></span>
    <div style="font-size:.85rem;color:#888;margin-top:.25rem">Enables Traefik proxy for http://domain/vm/nameofvm access</div>
</div>
<div style="margin:1.5rem 0 .5rem 0">
    <span class=badge>Custom domain (merged mode):</span>
    <input id=customdomain placeholder="e.g. techexplore.us" style="width:220px" />
    <button onclick="setCustomDomain()">Set domain</button>
    <span id=domainip style="margin-left:1.5rem"></span>
</div>
<div style="margin:1.5rem 0 .5rem 0">
        <span class=badge>Cloudflare Tunnel</span>
        <div style="margin:.5rem 0">
            <input id=cf_domain placeholder="CF hostname (e.g. dash.example.com)" style="width:260px" />
            <input id=cf_token placeholder="Tunnel token" style="width:340px; margin-left:.5rem" />
        </div>
        <div style="margin:.25rem 0">
            <button onclick="setCFTunnel()">Enable Cloudflare Tunnel</button>
            <button class="btn-gray" onclick="stopCFTunnel()">Disable Tunnel</button>
            <span id=cfstatus class=muted style="margin-left:1rem"></span>
        </div>
        <div style="margin:.5rem 0">
            <div style="margin:.25rem 0">
                <input id=cf_api_token placeholder="Cloudflare API Token (for DNS)" style="width:420px" />
                <button onclick="setCFApiToken()" class="btn-gray">Save API token</button>
            </div>
            <div class=muted style="font-size:.85rem;margin-top:.25rem">Provide a Cloudflare API Token with permissions to read zones and edit DNS. This allows the dashboard to automatically create the required CNAME when a tunnel is created. The token is stored in the dashboard environment (.env).</div>
        </div>
        <div style="margin:.5rem 0">
            <div style="margin:.25rem 0">
                <input id=cf_vm placeholder="VM name (e.g. epic)" style="width:140px" />
                <input id=cf_path placeholder="Path (e.g. /vm/epic)" style="width:160px; margin-left:.5rem" />
                <input id=cf_tunnel_name placeholder="Tunnel name (optional)" style="width:160px; margin-left:.5rem" />
            </div>
            <div style="margin:.25rem 0">
                <button onclick="cfMergeAdd()">Add merged mapping</button>
                <button class="btn-gray" onclick="cfMergeRemove()">Remove mapping</button>
                <button class="btn-gray" onclick="cfMergeList()">List mappings</button>
                <div id=cf_mappings style="margin-top:.5rem;color:#ddd"></div>
            </div>
        </div>
</div>
<script>
// Debug helpers: enable extra logs with ?debug=1
const DEBUG = new URLSearchParams(window.location.search).has('debug');
const dbg = (...args) => { if (DEBUG) console.log('[BLOBEDASH]', ...args); };
window.addEventListener('error', (e) => console.error('[BLOBEDASH] window error', e.message, e.error || e));
window.addEventListener('unhandledrejection', (e) => console.error('[BLOBEDASH] unhandledrejection', e.reason));

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
        // Load public IP settings
        if(info.publicIp) document.getElementById('publicip').value = info.publicIp;
        if(info.publicIpPort) document.getElementById('publicipdashport').value = info.publicIpPort;
        if(info.publicIpStatus) document.getElementById('publicipstatus').textContent = info.publicIpStatus;
        // Load merged mode status
        if(info.mergedPort) document.getElementById('mergedport').value = info.mergedPort;
        if(info.mergedStatus) document.getElementById('mergedstatus').textContent = info.mergedStatus;
            // Cloudflare Tunnel info
            try {
                const cf = info.cf_tunnel || {};
                document.getElementById('cf_domain').value = cf.domain || '';
                document.getElementById('cfstatus').textContent = cf.running ? 'Tunnel running' : (cf.enabled ? 'Configured (stopped)' : 'Not configured');
            } catch (e) {
                console.error('cf_tunnel parse error', e);
            }
    vms = data.instances || [];
    const tb=document.getElementById('tbody');
        tb.innerHTML='';
    const appOpts = (availableApps||[]).map(a=>`<option value="${a}">${a}</option>`).join('');
    vms.forEach(i=>{
            const tr=document.createElement('tr');
            const dot = statusDot(i.status);
            let portOrPath = '';
            let backendPort = '';
            let openUrl = i.url;
            if(mergedMode){
                // merged: show /vm/<name> or domain
                portOrPath = `${basePath}/${i.name}`;
                backendPort = info.httpPort || '80';
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
                    backendPort = portOrPath;
                } else {
                    openUrl = '';
                }
            }
            dbg('row', { name: i.name, status: i.status, rawUrl: i.url, mergedMode, portOrPath, openUrl });
                 // Build tunnel cell content with any errors
                 let tunnelCell = '';
                 if(i.tunnel){
                    if(i.tunnel.tunnel_status) tunnelCell += `<div style=\"margin-bottom:.25rem\">${i.tunnel.tunnel_status}</div>`;
                    if(i.tunnel.cf_tunnel_host) tunnelCell += `<div><a href=\"https://${i.tunnel.cf_tunnel_host}\" target=\"_blank\">https://${i.tunnel.cf_tunnel_host}</a></div>`;
                    // Show public service URL (if available) so users can access directly
                    if(i.tunnel.cf_service_url) tunnelCell += `<div><a href=\"${i.tunnel.cf_service_url}\" target=\"_blank\">${i.tunnel.cf_service_url}</a></div>`;
                    if(i.tunnel.tunnel_route_error) tunnelCell += `<div style=\"color:#fca5a5;margin-top:.25rem\">DNS error: ${i.tunnel.tunnel_route_error}</div>`;
                    if(i.tunnel.tunnel_runtime_error) tunnelCell += `<div style=\"color:#fca5a5;margin-top:.25rem\">Runtime error: ${i.tunnel.tunnel_runtime_error}</div>`;
                    tunnelCell += `<div style=\"margin-top:.25rem\"><button onclick=\"tunnelRecreate('${i.name}')\">Regenerate Tunnel</button> <button class=\"btn-gray\" onclick=\"tunnelDelete('${i.name}')\">Delete</button></div>`;
                    // CLI hint for manual tunnel creation via manager
                    tunnelCell += `<div style=\"margin-top:.25rem;font-size:.85rem;color:#cbd5e1\">CLI: blobe-vm-manager tunnel-create ${i.name}</div>`;
                 } else {
                     tunnelCell = `<button onclick=\"tunnelCreate('${i.name}')\">Create Tunnel</button>`;
                 }
                 tr.innerHTML=`<td>${i.name}</td><td>${dot}<span class=muted>${i.status||''}</span></td><td>${portOrPath}</td><td>${backendPort ? backendPort : ''}</td><td><a href="${openUrl}" target="_blank" rel="noopener noreferrer">${openUrl}</a></td>`+
                    `<td id="tunnel-${i.name}">${tunnelCell}</td>`+
                     `<td><button onclick="testBackend('${i.name}')">Test backend</button>`+
                      `<span id="test-${i.name}" style="margin-left:.5rem;color:#ddd"></span>`+
                      `</td>`+
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
            alert('Update failed:\n'+((j && (j.error||j.output))||'unknown error'));
        }
    }catch(e){
        alert('Update error: '+e);
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
            alert('Failed to start prune: ' + (j && (j.error||j.output) || 'unknown'));
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
    const dom = document.getElementById('customdomain').value.trim();
    if(!dom) return alert('Enter a domain.');
    const r = await fetch('/dashboard/api/set-domain', {method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`domain=${encodeURIComponent(dom)}`});
    const j = await r.json().catch(()=>({}));
    if(j && j.ip) document.getElementById('domainip').textContent = `Point domain to: ${j.ip}`;
    else alert('Saved, but could not resolve IP.');
}
async function setPublicIP(){
    const ip = document.getElementById('publicip').value.trim();
    const port = document.getElementById('publicipdashport').value.trim() || '80';
    if(!ip) return alert('Enter the public IP address.');
    try{
        const r = await fetch('/dashboard/api/set-public-ip', {method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`ip=${encodeURIComponent(ip)}&port=${encodeURIComponent(port)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert(`Dashboard will be accessible at http://${ip}:${port}/dashboard`);
        }else{
            alert('Failed to set public IP: ' + (j && (j.error||j.message) || 'unknown'));
        }
    }catch(e){alert('Error: '+e)}
    setTimeout(load, 2000);
}
async function disablePublicIP(){
    if(!confirm('Disable public IP access? Dashboard will revert to normal port mode.')) return;
    try{
        const r = await fetch('/dashboard/api/disable-public-ip',{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok) alert('Public IP access disabled.'); else alert('Failed to disable.');
    }catch(e){alert('Error: '+e)}
    setTimeout(load, 1000);
}
async function enableMergedMode(){
    const port = document.getElementById('mergedport').value.trim() || '80';
    const dom = document.getElementById('customdomain').value.trim();
    if(!dom) return alert('Set the custom domain first (techexplore.us)');
    try{
        const r = await fetch('/dashboard/api/enable-merged-mode', {method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`port=${encodeURIComponent(port)}&domain=${encodeURIComponent(dom)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert(`Merged mode enabled! VMs will be accessible at http://${dom}/vm/nameofvm`);
        }else{
            alert('Failed to enable merged mode: ' + (j && (j.error||j.message) || 'unknown'));
        }
    }catch(e){alert('Error: '+e)}
    setTimeout(load, 2000);
}
async function disableMergedMode(){
    if(!confirm('Disable merged mode? VMs will revert to direct port access.')) return;
    try{
        const r = await fetch('/dashboard/api/disable-merged-mode',{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok) alert('Merged mode disabled.'); else alert('Failed to disable.');
    }catch(e){alert('Error: '+e)}
    setTimeout(load, 1000);
}
function statusDot(st){
    const s=(st||'').toLowerCase();
    let cls='gray';
    if(s.includes('rebuilding') || s.includes('updating')) cls='amber';
    else if(s.includes('up')) cls='green';
    else if(s.includes('exited')||s.includes('stopped')||s.includes('dead')) cls='red';
    return `<span class="dot ${cls}"></span>`;
}
async function setCFTunnel(){
    const dom = document.getElementById('cf_domain').value.trim();
    const token = document.getElementById('cf_token').value.trim();
    if(!dom || !token) return alert('Enter Cloudflare hostname and tunnel token.');
    try{
        const r = await fetch('/dashboard/api/set-cftunnel',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`domain=${encodeURIComponent(dom)}&token=${encodeURIComponent(token)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){
            alert('Cloudflare Tunnel requested. It may take a few seconds to start.');
        }else{
            alert('Failed to enable tunnel: ' + (j && (j.error||j.message) || 'unknown'));
        }
    }catch(e){alert('Error: '+e)}
    setTimeout(load, 2000);
}

async function stopCFTunnel(){
    if(!confirm('Stop Cloudflare Tunnel?')) return;
    try{
        const r = await fetch('/dashboard/api/stop-cftunnel',{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok) alert('Tunnel stop requested.'); else alert('Failed to stop tunnel.');
    }catch(e){alert('Error: '+e)}
    setTimeout(load, 1000);
}

async function cfMergeAdd(){
    const name = document.getElementById('cf_vm').value.trim();
    const path = document.getElementById('cf_path').value.trim();
    const tunnel = document.getElementById('cf_tunnel_name').value.trim() || 'blobevm';
    const host = document.getElementById('cf_domain').value.trim();
    if(!name || !path || !host) return alert('Enter VM name, path, and CF hostname.');
    try{
        const r = await fetch('/dashboard/api/cf-merge-add',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}&host=${encodeURIComponent(host)}&tunnel=${encodeURIComponent(tunnel)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){ alert('Merged mapping added.'); } else { alert('Failed: ' + (j && (j.error||j.output) || 'unknown')); }
    }catch(e){ alert('Error: '+e); }
    setTimeout(load,1000);
}

async function cfMergeRemove(){
    const name = document.getElementById('cf_vm').value.trim();
    if(!name) return alert('Enter VM name to remove mapping for');
    if(!confirm('Remove merged mapping for '+name+'?')) return;
    try{
        const r = await fetch('/dashboard/api/cf-merge-remove',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`name=${encodeURIComponent(name)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok) alert('Removed mapping.'); else alert('Failed: ' + (j && (j.error||j.output) || 'unknown'));
    }catch(e){ alert('Error: '+e); }
    setTimeout(load,1000);
}

async function cfMergeList(){
    try{
        const r = await fetch('/dashboard/api/cf-merge-list');
        const j = await r.json().catch(()=>({}));
        const el = document.getElementById('cf_mappings');
        if(!j || !j.ok){ el.textContent = 'Unable to fetch mappings'; return; }
        if(!j.mappings || j.mappings.length===0){ el.textContent = '<none>'; return; }
        el.innerHTML = j.mappings.map(m=>`<div>${m.name} -> ${m.hostpath} (tunnel: ${m.tunnel||'blobevm'})</div>`).join('');
    }catch(e){ alert('Error: '+e); }
}
async function setCFApiToken(){
    const token = document.getElementById('cf_api_token').value.trim();
    if(!token) return alert('Enter an API token.');
    try{
        const r = await fetch('/dashboard/api/set-cf-api-token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`token=${encodeURIComponent(token)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){ alert('API token saved and verified.'); }
        else { alert('Failed to save token: '+(j && (j.error||j.message)||'unknown') + '\n\nSuggested fix: create a token with Zone:Read and Zone:DNS:Edit permissions, or provide account-level token.'); }
    }catch(e){ alert('Error: '+e); }
}
async function tunnelCreate(name){
    if(!name) return alert('Enter a VM name');
    try{
        const r = await fetch('/dashboard/api/tunnel/create',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`name=${encodeURIComponent(name)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){ alert('Tunnel creation requested.'); } else { alert('Failed: '+(j && (j.error||j.output)||'unknown')); }
    }catch(e){ alert('Error: '+e); }
    setTimeout(load,1000);
}
async function tunnelDelete(name){
    if(!name) return alert('Enter a VM name');
    if(!confirm('Delete tunnel for '+name+'?')) return;
    try{
        const r = await fetch('/dashboard/api/tunnel/delete',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`name=${encodeURIComponent(name)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){ alert('Tunnel deleted.'); } else { alert('Failed: '+(j && (j.error||j.output)||'unknown')); }
    }catch(e){ alert('Error: '+e); }
    setTimeout(load,1000);
}
async function tunnelRecreate(name){
    if(!name) return alert('Enter a VM name');
    if(!confirm('Regenerate tunnel for '+name+'?')) return;
    try{
        const r = await fetch('/dashboard/api/tunnel/recreate',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`name=${encodeURIComponent(name)}`});
        const j = await r.json().catch(()=>({}));
        if(j && j.ok){ alert('Tunnel regenerated.'); } else { alert('Failed: '+(j && (j.error||j.output)||'unknown')); }
    }catch(e){ alert('Error: '+e); }
    setTimeout(load,1500);
}
async function testBackend(name){
    const el = document.getElementById(`test-${name}`);
    if(el) el.textContent = 'Checking...';
    try{
        const r = await fetch(`/dashboard/api/test-backend/${encodeURIComponent(name)}`,{method:'POST'});
        const j = await r.json().catch(()=>({}));
        if(!j || !j.ok){
            if(el) el.textContent = `Failed: ${j && (j.error||'no response')}`;
            else alert('Failed: ' + (j && (j.error||'no response')));
            return;
        }
        const status = j.code || 'N/A';
        const url = j.url || '';
        if(el) el.textContent = `${status} — ${url}`;
        else alert(`Backend test: ${status} — ${url}`);
    }catch(e){ if(el) el.textContent = 'Error'; else alert('Error: '+e); }
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
            alert(j.error || 'Failed to create VM.');
        }
    } catch (err) {
        alert('Error creating VM: ' + err);
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
</script>
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
    # Default to Traefik-enabled mode unless NO_TRAEFIK is explicitly set to '1'.
    # Keep this consistent with the manager script which defaults NO_TRAEFIK to 0.
    return env.get('NO_TRAEFIK', '0') == '1'

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
            # Enrich with per-instance tunnel metadata if present
            try:
                for it in instances:
                    try:
                        mf = os.path.join(_state_dir(), 'instances', it['name'], 'instance.json')
                        if os.path.isfile(mf):
                            jd = json.load(open(mf))
                            tunnel = {}
                            for k in ('tunnel_id', 'tunnel_name', 'tunnel_status', 'cf_tunnel_host', 'cf_exposed', 'cf_service_url'):
                                if k in jd:
                                    tunnel[k] = jd.get(k)
                            if tunnel:
                                it['tunnel'] = tunnel
                    except Exception:
                        pass
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
    # Enrich with per-instance tunnel metadata if present (fallback path)
    try:
        for it in instances:
            try:
                mf = os.path.join(_state_dir(), 'instances', it['name'], 'instance.json')
                if os.path.isfile(mf):
                    jd = json.load(open(mf))
                    tunnel = {}
                    for k in ('tunnel_id', 'tunnel_name', 'tunnel_status', 'cf_tunnel_host', 'cf_exposed', 'cf_service_url'):
                        if k in jd:
                            tunnel[k] = jd.get(k)
                    if tunnel:
                        it['tunnel'] = tunnel
            except Exception:
                pass
    except Exception:
        pass
    return instances

def _resolve_public_host():
    """Resolve public host for backend URLs: prefer PUBLIC_HOST env, then request host, 
    then hostname resolution (skip Docker bridge IPs 172.17-31.x or 10.x.x)."""
    env = _read_env()
    if env.get('PUBLIC_HOST'):
        return env['PUBLIC_HOST']
    # Try request host
    req_host = _request_host()
    if req_host and not (req_host.startswith('172.') or req_host.startswith('10.')):
        return req_host
    # Try hostname resolution (skip Docker IPs)
    try:
        import socket
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in ips:
            if not (ip.startswith('172.') or ip.startswith('10.')):
                return ip
        # If all are Docker-ish, return first one anyway
        if ips:
            return ips[0]
    except Exception:
        pass
    return '127.0.0.1'

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
    # merged mode is simply the inverse of direct (NO_TRAEFIK) mode
    merged = not _is_direct_mode()
    base_path = env.get('BASE_PATH', '/vm')
    domain = env.get('BLOBEVM_DOMAIN', '')
    dash_port = env.get('DASHBOARD_PORT', '')
    http_port = env.get('HTTP_PORT', '80')
    # Show the host the user used to reach the dashboard
    ip = _request_host() or ''
    # Cloudflare Tunnel info
    cf_enabled = env.get('CF_TUNNEL_ENABLED', '0') == '1'
    cf_domain = env.get('CF_TUNNEL_DOMAIN', '')
    cf_running = False
    try:
        r = _docker('ps', '--filter', 'name=cloudflared', '--format', '{{.Names}}')
        if r.returncode == 0 and 'cloudflared' in r.stdout.splitlines():
            cf_running = True
    except Exception:
        cf_running = False
    # Public IP info
    public_ip = env.get('PUBLIC_IP', '')
    public_ip_port = env.get('PUBLIC_IP_PORT', '80')
    public_ip_status = ''
    if public_ip:
        public_ip_status = f'Listening on http://{public_ip}:{public_ip_port}/dashboard'
    # Merged mode info
    merged_port = env.get('HTTP_PORT', '80')
    merged_status = ''
    if merged and domain:
        merged_status = f'Merged mode enabled - VMs at http://{domain}/vm/nameofvm'
    return jsonify({'merged': merged, 'basePath': base_path, 'domain': domain, 'dashPort': dash_port, 'ip': ip, 'httpPort': http_port,
                    'cf_tunnel': {'enabled': cf_enabled, 'domain': cf_domain, 'running': cf_running},
                    'publicIp': public_ip, 'publicIpPort': public_ip_port, 'publicIpStatus': public_ip_status,
                    'mergedPort': merged_port, 'mergedStatus': merged_status})

@app.post('/dashboard/api/set-domain')
@auth_required
def api_set_domain():
    dom = request.values.get('domain','').strip()
    if not dom:
        return jsonify({'ok': False, 'error': 'No domain'}), 400
    _write_env_kv({'BLOBEVM_DOMAIN': dom})
    # Best-effort IP hint: show the host the user is using to reach the dashboard
    ip = _request_host() or ''
    if not ip:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = ''
    return jsonify({'ok': True, 'domain': dom, 'ip': ip})


@app.post('/dashboard/api/set-public-ip')
@auth_required
def api_set_public_ip():
    """Configure dashboard to run on public IP at specified port (typically port 80).
    This will recreate the dashboard container to bind to the public IP.
    """
    ip = request.values.get('ip', '').strip()
    port = request.values.get('port', '80').strip()
    if not ip:
        return jsonify({'ok': False, 'error': 'IP address required'}), 400
    try:
        port = int(port)
    except ValueError:
        return jsonify({'ok': False, 'error': 'Invalid port'}), 400
    
    # Validate IP format (basic check)
    if not all(part.isdigit() and 0 <= int(part) <= 255 for part in ip.split('.') if part):
        return jsonify({'ok': False, 'error': 'Invalid IP address format'}), 400
    
    # Persist values
    _write_env_kv({'PUBLIC_IP': ip, 'PUBLIC_IP_PORT': str(port)})
    
    # Restart dashboard container in background
    def worker():
        try:
            _docker('rm', '-f', 'blobedash')
            # Run dashboard bound to the public IP
            state_dir = _state_dir()
            _docker('run', '-d', '--name', 'blobedash', '--restart', 'unless-stopped',
                   '-p', f'{ip}:{port}:5000',
                   '-v', f'{state_dir}:/opt/blobe-vm',
                   '-v', '/usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro',
                   '-v', DOCKER_VOLUME_BIND,
                   '-v', '/var/run/docker.sock:/var/run/docker.sock',
                   '-v', f'{state_dir}/dashboard/app.py:/app/app.py:ro',
                   '-e', f'BLOBEDASH_USER={os.environ.get("BLOBEDASH_USER","")}',
                   '-e', f'BLOBEDASH_PASS={os.environ.get("BLOBEDASH_PASS","")}',
                   '-e', f'HOST_DOCKER_BIN={HOST_DOCKER_BIN}',
                   'python:3.11-slim',
                   'bash', '-c', 'pip install --no-cache-dir flask && python /app/app.py')
        except Exception as e:
            print(f'Error setting public IP: {e}', file=__import__('sys').stderr)
    
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'ip': ip, 'port': port, 'message': f'Dashboard configured to listen on http://{ip}:{port}/dashboard'})


@app.post('/dashboard/api/disable-public-ip')
@auth_required
def api_disable_public_ip():
    """Disable public IP access and revert to normal dashboard port mode."""
    _write_env_kv({'PUBLIC_IP': '', 'PUBLIC_IP_PORT': ''})
    
    def worker():
        try:
            _docker('rm', '-f', 'blobedash')
            # Restart with normal port binding
            state_dir = _state_dir()
            env = _read_env()
            dash_port = env.get('DASHBOARD_PORT', '20000') or '20000'
            _docker('run', '-d', '--name', 'blobedash', '--restart', 'unless-stopped',
                   '-p', f'{dash_port}:5000',
                   '-v', f'{state_dir}:/opt/blobe-vm',
                   '-v', '/usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro',
                   '-v', DOCKER_VOLUME_BIND,
                   '-v', '/var/run/docker.sock:/var/run/docker.sock',
                   '-v', f'{state_dir}/dashboard/app.py:/app/app.py:ro',
                   '-e', f'BLOBEDASH_USER={os.environ.get("BLOBEDASH_USER","")}',
                   '-e', f'BLOBEDASH_PASS={os.environ.get("BLOBEDASH_PASS","")}',
                   '-e', f'HOST_DOCKER_BIN={HOST_DOCKER_BIN}',
                   'python:3.11-slim',
                   'bash', '-c', 'pip install --no-cache-dir flask && python /app/app.py')
        except Exception as e:
            print(f'Error disabling public IP: {e}', file=__import__('sys').stderr)
    
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Public IP access disabled. Dashboard reverted to normal port mode.'})


@app.post('/dashboard/api/enable-merged-mode')
@auth_required
def api_enable_merged_mode():
    """Enable merged mode (Traefik proxy) for public domain access.
    VMs will be accessible at http://domain/vm/nameofvm
    """
    port = request.values.get('port', '80').strip()
    domain = request.values.get('domain', '').strip()
    
    if not domain:
        return jsonify({'ok': False, 'error': 'Domain is required'}), 400
    
    try:
        port = int(port)
    except ValueError:
        return jsonify({'ok': False, 'error': 'Invalid port'}), 400
    
    # Persist settings
    _write_env_kv({
        'NO_TRAEFIK': '0',
        'BLOBEVM_DOMAIN': domain,
        'HTTP_PORT': str(port),
        'MERGED_MODE': '1',
        'BASE_PATH': '/vm'
    })
    
    # Enable single-port mode using the existing function
    def worker():
        try:
            _enable_single_port(port)
        except Exception as e:
            print(f'Error enabling merged mode: {e}', file=__import__('sys').stderr)
    
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'domain': domain, 'port': port, 'message': f'Merged mode enabled! VMs will be accessible at http://{domain}/vm/nameofvm'})


@app.post('/dashboard/api/disable-merged-mode')
@auth_required
def api_disable_merged_mode():
    """Disable merged mode and revert to direct port access."""
    _write_env_kv({
        'NO_TRAEFIK': '1',
        'MERGED_MODE': '0'
    })
    
    def worker():
        try:
            _disable_single_port(None)
        except Exception as e:
            print(f'Error disabling merged mode: {e}', file=__import__('sys').stderr)
    
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Merged mode disabled. VMs reverted to direct port access.'})




@app.post('/dashboard/api/set-cftunnel')
@auth_required
def api_set_cftunnel():
    """Enable/configure Cloudflare Tunnel. Expects form values 'domain' and 'token'.
    This will persist CF_TUNNEL_* vars to the env and start a 'cloudflared' container in host network.
    """
    dom = request.values.get('domain','').strip()
    token = request.values.get('token','').strip()
    if not dom or not token:
        return jsonify({'ok': False, 'error': 'domain and token required'}), 400
    # Persist values
    ok = _write_env_kv({'CF_TUNNEL_ENABLED': '1', 'CF_TUNNEL_DOMAIN': dom, 'CF_TUNNEL_TOKEN': token})
    # Ensure directory for cloudflared if needed
    try:
        os.makedirs(os.path.join(_state_dir(), '.cloudflared'), exist_ok=True)
    except Exception:
        pass

    # Start cloudflared in background thread to avoid blocking
    def worker(d, t):
        try:
            # Remove existing container if present
            _docker('rm', '-f', 'cloudflared')
        except Exception:
            pass
        try:
            # Run cloudflared using host network and point to public backend.
            # Use configured HTTP_PORT if present, otherwise fall back to 80.
            http_port = _read_env().get('HTTP_PORT', '80') or '80'
            # Resolve public host (skip Docker IPs)
            host = _resolve_public_host()
            target_url = f'http://{host}:{http_port}'
            _docker('run', '-d', '--name', 'cloudflared', '--net', 'host', '--restart', 'unless-stopped',
                    'cloudflare/cloudflared:latest', 'tunnel', '--no-autoupdate', 'run', '--token', t, '--url', target_url)
        except Exception:
            pass

    threading.Thread(target=worker, args=(dom, token), daemon=True).start()
    return jsonify({'ok': True, 'domain': dom})


@app.post('/dashboard/api/cf-expose')
@auth_required
def api_cf_expose():
    name = request.values.get('name','').strip()
    host = request.values.get('host','').strip()
    token = request.values.get('token','').strip()
    if not name or not host or not token:
        return jsonify({'ok': False, 'error': 'name, host and token required'}), 400
    try:
        r = subprocess.run([MANAGER, 'cf-expose', name, host, token], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/cf-create-tunnel')
@auth_required
def api_cf_create_tunnel():
    tname = request.values.get('name','').strip()
    if not tname:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'cf-create-tunnel', tname], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/cf-remove')
@auth_required
def api_cf_remove():
    name = request.values.get('name','').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'cf-remove', name], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/tunnel/create')
@auth_required
def api_tunnel_create():
    name = request.values.get('name','').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'tunnel-create', name], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/tunnel/delete')
@auth_required
def api_tunnel_delete():
    name = request.values.get('name','').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'tunnel-delete', name], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/tunnel/recreate')
@auth_required
def api_tunnel_recreate():
    name = request.values.get('name','').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'tunnel-recreate', name], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/tunnel/status/<name>')
@auth_required
def api_tunnel_status(name):
    name = name.strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'tunnel-status', name], capture_output=True, text=True)
        if r.returncode != 0:
            # If manager returns non-zero but printed JSON to stdout, try to return it
            out = r.stdout.strip() or r.stderr.strip()
            return jsonify({'ok': False, 'error': out}), 500
        # Try to parse JSON output
        txt = r.stdout.strip()
        try:
            j = json.loads(txt)
            return jsonify({'ok': True, 'status': j})
        except Exception:
            return jsonify({'ok': True, 'output': txt})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/set-cf-api-token')
@auth_required
def api_set_cf_api_token():
    """Store and validate a Cloudflare API token. Returns helpful error messages if token invalid or lacks permissions."""
    token = request.values.get('token','').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'token required'}), 400
    # Persist token to .env
    ok = _write_env_kv({'CF_API_TOKEN': token})
    if not ok:
        return jsonify({'ok': False, 'error': 'Failed to persist token to .env (permission error?)'}), 500
    # Try to verify token via Cloudflare API (user tokens verify endpoint)
    verify_url = 'https://api.cloudflare.com/client/v4/user/tokens/verify'
    try:
        req = urlrequest.Request(verify_url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        with urlrequest.urlopen(req, timeout=8) as resp:
            body = resp.read().decode('utf-8')
            j = json.loads(body)
            if j.get('success'):
                return jsonify({'ok': True, 'message': 'Token verified'})
            else:
                return jsonify({'ok': False, 'error': 'Token verify failed', 'details': j}), 400
    except urlerror.HTTPError as e:
        try:
            txt = e.read().decode('utf-8')
        except Exception:
            txt = str(e)
        # Provide suggestions
        msg = 'Cloudflare API returned error while verifying token. Ensure the token is valid and has Zone:Read and Zone:DNS:Edit permissions.'
        return jsonify({'ok': False, 'error': msg, 'details': txt}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Error verifying token: {e}'}), 500


@app.post('/dashboard/api/cf-merge-add')
@auth_required
def api_cf_merge_add():
    name = request.values.get('name','').strip()
    path = request.values.get('path','').strip()
    host = request.values.get('host','').strip()
    tname = request.values.get('tunnel','').strip() or 'blobevm'
    if not name or not path or not host:
        return jsonify({'ok': False, 'error': 'name, path and host required'}), 400
    try:
        r = subprocess.run([MANAGER, 'cf-merge-add', name, path, host, tname], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/cf-merge-remove')
@auth_required
def api_cf_merge_remove():
    name = request.values.get('name','').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        r = subprocess.run([MANAGER, 'cf-merge-remove', name], capture_output=True, text=True)
        if r.returncode != 0:
            return jsonify({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}), 500
        return jsonify({'ok': True, 'output': r.stdout.strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/test-backend/<name>')
@auth_required
def api_test_backend(name):
    """Backend is already public and accessible, so just return OK.
    Returns JSON with {ok}.
    """
    name = name.strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    # Backend is already public, no need to check
    return jsonify({'ok': True})


@app.get('/dashboard/api/cf-merge-list')
@auth_required
def api_cf_merge_list():
    try:
        r = subprocess.run([MANAGER, 'cf-merge-list'], capture_output=True, text=True)
        out = r.stdout.strip()
        items = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith('<'):
                continue
            if line.startswith('- '):
                # parse "- name -> host/path (tunnel: name)"
                parts = line[2:].split('->')
                if len(parts) >= 2:
                    nm = parts[0].strip()
                    rest = parts[1].strip()
                    hostpath = rest.split('(')[0].strip()
                    tunnel = ''
                    if '(' in rest and 'tunnel:' in rest:
                        tunnel = rest.split('tunnel:')[-1].strip(' )')
                    items.append({'name': nm, 'hostpath': hostpath, 'tunnel': tunnel})
        return jsonify({'ok': True, 'mappings': items})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/diag')
@auth_required
def api_diag():
    """Diagnostic endpoint: summarize Traefik and Cloudflared state, key env vars,
    and recent cloudflared logs/config snippets to help debug merged-mode issues.
    """
    env = _read_env()
    # Basic env summary
    env_summary = {k: env.get(k, '') for k in ('NO_TRAEFIK', 'HTTP_PORT', 'BASE_PATH', 'BLOBEVM_DOMAIN', 'DASHBOARD_PORT')}

    # Traefik presence
    traefik_running = False
    try:
        r = _docker('ps', '--filter', 'name=traefik', '--format', '{{.Names}}')
        traefik_running = (r.returncode == 0 and bool(r.stdout.strip()))
    except Exception:
        traefik_running = False

    # Cloudflared containers
    cf_containers = []
    try:
        r2 = _docker('ps', '--filter', 'name=cloudflared', '--format', '{{.Names}}')
        if r2.returncode == 0 and r2.stdout:
            cf_containers = [ln.strip() for ln in r2.stdout.splitlines() if ln.strip()]
    except Exception:
        cf_containers = []

    # Tail logs for cloudflared (best-effort)
    cf_logs = ''
    try:
        cf_logs = subprocess.check_output(['docker', 'logs', 'cloudflared', '--tail', '200'], text=True, stderr=subprocess.STDOUT)
    except Exception as e:
        cf_logs = f'Error reading cloudflared logs: {e}'

    # Extract any 'Updated to new configuration' JSON blobs from logs
    ingresses = []
    try:
        for line in cf_logs.splitlines():
            if 'Updated to new configuration config=' in line:
                try:
                    cfg_txt = line.split('Updated to new configuration config=', 1)[1].strip()
                    # Try to find a JSON object in the remainder
                    first = cfg_txt.find('{')
                    last = cfg_txt.rfind('}')
                    if first != -1 and last != -1 and last > first:
                        jtxt = cfg_txt[first:last+1]
                        cfg = json.loads(jtxt)
                        for ing in cfg.get('ingress', []) if isinstance(cfg.get('ingress', []), list) else []:
                            hostname = ing.get('hostname') or ''
                            service = ing.get('service') or ''
                            ingresses.append({'hostname': hostname, 'service': service})
                except Exception:
                    # ignore parse errors
                    pass
    except Exception:
        pass

    # Also, try to read manager cf-merge-list for repo-managed mappings
    cf_merge = []
    try:
        r3 = subprocess.run([MANAGER, 'cf-merge-list'], capture_output=True, text=True)
        if r3.returncode == 0 and r3.stdout:
            for line in r3.stdout.splitlines():
                line = line.strip()
                if line.startswith('- '):
                    parts = line[2:].split('->')
                    if len(parts) >= 2:
                        nm = parts[0].strip()
                        hostpath = parts[1].split('(')[0].strip()
                        cf_merge.append({'name': nm, 'hostpath': hostpath})
    except Exception:
        pass

    return jsonify({
        'env': env_summary,
        'is_direct': _is_direct_mode(),
        'traefik': {'running': traefik_running},
        'cloudflared': {
            'containers': cf_containers,
            'ingress_rules_from_logs': ingresses,
            'recent_logs_tail': cf_logs[:8000]
        },
        'manager_cf_merge': cf_merge
    })


@app.post('/dashboard/api/stop-cftunnel')
@auth_required
def api_stop_cftunnel():
    try:
        _docker('rm', '-f', 'cloudflared')
    except Exception:
        pass
    _write_env_kv({'CF_TUNNEL_ENABLED': '0'})
    return jsonify({'ok': True})

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
