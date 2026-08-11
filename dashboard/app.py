#!/usr/bin/env python3
import os, json, subprocess, shlex, base64, socket, threading, time, sqlite3, secrets, platform
import shutil
import re
from urllib import request as urlrequest, error as urlerror
from urllib.parse import quote as url_quote, urlparse
from functools import wraps
from flask import Flask, jsonify, request, abort, send_from_directory, render_template_string, Response, send_file, redirect
from werkzeug.security import check_password_hash
import optimizer as dash_optimizer
from runtime_stats import get_docker_stats
import hmac, hashlib, time, base64
from branding import PRODUCT_NAME, DASHBOARD_TITLE, MANAGER_NAME, AUTH_REALM
try:
    from .vm_hosts import LocalDockerHost, VmHostRegistry, VmHostUnavailable
    from .remote_hosts import ConfiguredVmHostRegistry, RemoteHostConfigError, redact_host_record, upsert_remote_host_config
    from .guacamole_orchestrator import GuacamoleOrchestrator, ConsoleOrchestrationError
except ImportError:
    from vm_hosts import LocalDockerHost, VmHostRegistry, VmHostUnavailable
    from remote_hosts import ConfiguredVmHostRegistry, RemoteHostConfigError, redact_host_record, upsert_remote_host_config
    from guacamole_orchestrator import GuacamoleOrchestrator, ConsoleOrchestrationError
try:
    import psutil
except Exception:
    psutil = None

app = Flask(__name__)
 
# --- Admin authentication helpers (must be defined before route decorators) ---
# BLOBEDASH_USER/PASS are the sole credentials for new installs. The legacy
# dashboard password remains read-only migration support for existing hosts.
_LOGIN_ATTEMPTS = {}
_LOGIN_LOCK = threading.Lock()

# Dashboard escalation is an expensive, state-changing AI recovery request.
# Keep the gate in-process so repeated clicks cannot launch concurrent agents.
_HERMES_ESCALATION_COOLDOWN_SECONDS = 300
_HERMES_ESCALATIONS = {}
_HERMES_ESCALATION_LOCK = threading.Lock()

def _claim_hermes_escalation(name: str, now=None):
    now = time.time() if now is None else float(now)
    with _HERMES_ESCALATION_LOCK:
        previous = _HERMES_ESCALATIONS.get(name)
        if previous is not None:
            elapsed = max(0.0, now - previous)
            remaining = int(max(0, _HERMES_ESCALATION_COOLDOWN_SECONDS - elapsed))
            if remaining > 0:
                return {'allowed': False, 'retry_after': remaining}
        _HERMES_ESCALATIONS[name] = now
    return {'allowed': True, 'retry_after': 0}

def _allow_insecure_dashboard() -> bool:
    return os.environ.get('BLOBEVM_ALLOW_INSECURE_DASHBOARD') == '1'

def _admin_credentials():
    user = os.environ.get('BLOBEDASH_USER', '').strip()
    password_hash = os.environ.get('BLOBEDASH_PASS_HASH', '').strip()
    if user and password_hash:
        return user, password_hash
    password = os.environ.get('BLOBEDASH_PASS', '')
    if user and password:
        return user, password
    legacy = _get_legacy_dashboard_password()
    if legacy:
        return 'admin', legacy
    return None, None

def _admin_password_matches(candidate: str, expected: str) -> bool:
    password_hash = os.environ.get('BLOBEDASH_PASS_HASH', '').strip()
    if password_hash and hmac.compare_digest(expected, password_hash):
        try:
            return bool(check_password_hash(password_hash, candidate))
        except (TypeError, ValueError):
            return False
    return hmac.compare_digest(candidate, expected)

def need_auth():
    """Whether the dashboard is configured for protected access."""
    return not _allow_insecure_dashboard()

def _dashboard_secret() -> str:
    # Never mint or verify a protected session with a public/default secret.
    return os.environ.get('DASH_V2_SECRET', '').strip()

def _request_is_https() -> bool:
    forwarded = request.headers.get('X-Forwarded-Proto', '').split(',', 1)[0].strip().lower()
    return bool(request.is_secure or forwarded == 'https')

def _same_origin_request() -> bool:
    origin = request.headers.get('Origin')
    if not origin:
        # Basic auth remains supported for existing scripts and non-browser CLI
        # clients. Cookie sessions require a browser Origin on mutations.
        return request.headers.get('Authorization', '').lower().startswith('basic ')
    parsed = urlparse(origin)
    return parsed.scheme in ('http', 'https') and parsed.netloc == request.host

def _csrf_token_for_session(session_token: str | None = None) -> str:
    session_token = session_token if session_token is not None else request.cookies.get('Dashboard-Auth', '')
    secret = _dashboard_secret()
    if not secret or not session_token:
        return ''
    digest = hmac.new(secret.encode('utf-8'), ('csrf:' + session_token).encode('utf-8'), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')

def _csrf_request_valid() -> bool:
    session_token = request.cookies.get('Dashboard-Auth', '')
    # Basic-auth automation has no cookie session to bind a CSRF token to.
    if not session_token:
        return request.headers.get('Authorization', '').lower().startswith('basic ')
    provided = request.headers.get('X-CSRF-Token', '')
    expected = _csrf_token_for_session(session_token)
    return bool(provided and expected and hmac.compare_digest(provided, expected))

def check_auth(header: str) -> bool:
    user_expected, password_expected = _admin_credentials()
    if not user_expected or not password_expected:
        return False
    if not header or not header.lower().startswith('basic '):
        return False
    try:
        raw = base64.b64decode(header.split(None,1)[1]).decode('utf-8')
        user, pw = raw.split(':',1)
        return hmac.compare_digest(user, user_expected) and _admin_password_matches(pw, password_expected)
    except Exception:
        return False

def admin_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == 'OPTIONS':
            response = Response(status=204)
            if _same_origin_request():
                response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', request.host_url.rstrip('/'))
                response.headers['Access-Control-Allow-Credentials'] = 'true'
                response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE'
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Requested-With, X-CSRF-Token'
            return response
        if _allow_insecure_dashboard():
            return fn(*args, **kwargs)
        user, password = _admin_credentials()
        if not user or not password or not _dashboard_secret():
            return jsonify({'ok': False, 'error': 'Dashboard authentication is not configured'}), 503
        basic_ok = check_auth(request.headers.get('Authorization'))
        token_ok = _verify_v2_token(request.cookies.get('Dashboard-Auth', ''))
        if not (basic_ok or token_ok):
            return Response('Auth required', 401, {'WWW-Authenticate': f'Basic realm="{AUTH_REALM}"'})
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _same_origin_request():
            return jsonify({'ok': False, 'error': 'Cross-origin request rejected'}), 403
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _csrf_request_valid():
            return jsonify({'ok': False, 'error': 'CSRF validation failed'}), 403
        return fn(*args, **kwargs)
    return wrapper

# Compatibility name used by the legacy dashboard routes. New routes use the
# explicit policy name above; there is deliberately no separate v2 policy.
auth_required = admin_auth_required

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
        else:
            # If no docker container, allow detecting a local dev server (Vite) for development.
            # Use env var DASHBOARD_DEV_PORT to override default (5173).
            try:
                dev_port = int(env.get('DASHBOARD_DEV_PORT', '5173'))
                # Prefer explicit host if provided, else detect server's outward-facing IP
                host_to_check = env.get('DASHBOARD_DEV_HOST', '')
                if not host_to_check:
                    try:
                        # determine outward-facing IP by creating a UDP socket
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.connect(('8.8.8.8', 80))
                        host_to_check = s.getsockname()[0]
                        s.close()
                    except Exception:
                        host_to_check = '127.0.0.1'
                # try connecting to host_to_check:dev_port
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                    s2.settimeout(0.5)
                    res = s2.connect_ex((host_to_check, dev_port))
                    if res == 0 and domain:
                        running = True
                        # The dashboard is being served by Vite on dev_port; report the same public domain path
                        url = f'http://{domain}/Dashboard'
            except Exception:
                pass
    except Exception:
        running = False
        url = None
    return jsonify({'running': running, 'url': url})

APP_ROOT = '/opt/blobe-vm'
MANAGER = os.environ.get('BLOBEVM_MANAGER') or os.path.join(APP_ROOT, 'server', 'blobe-vm-manager')
if not os.path.isfile(MANAGER):
    MANAGER = 'blobe-vm-manager'
HOST_DOCKER_BIN = os.environ.get('HOST_DOCKER_BIN') or '/usr/bin/docker'
CONTAINER_DOCKER_BIN = os.environ.get('CONTAINER_DOCKER_BIN') or '/usr/bin/docker'
DOCKER_VOLUME_BIND = f'{HOST_DOCKER_BIN}:{CONTAINER_DOCKER_BIN}:ro'

# The local provider is the compatibility default.  A host id may be supplied
# by newer callers, while legacy requests that only contain a VM name remain
# local by default.
LOCAL_VM_HOST = LocalDockerHost(manager=MANAGER)
VM_HOST_REGISTRY = ConfiguredVmHostRegistry(LOCAL_VM_HOST)
VM_HOSTS = VM_HOST_REGISTRY
_CONSOLE_ORCHESTRATOR = None

def _console_orchestrator():
    global _CONSOLE_ORCHESTRATOR
    if _CONSOLE_ORCHESTRATOR is None:
        _CONSOLE_ORCHESTRATOR = GuacamoleOrchestrator(
            root=os.environ.get('EPICVM_CONSOLE_ROOT', '/opt/epicvm/instances'),
            proxy_network=os.environ.get('EPICVM_TRAEFIK_NETWORK', 'proxy'),
            public_host=os.environ.get('EPICVM_PUBLIC_HOST', ''),
            tls_resolver=os.environ.get('EPICVM_TRAEFIK_CERTRESOLVER', ''),
            auth_middleware=os.environ.get('EPICVM_TRAEFIK_AUTH_MIDDLEWARE', ''),
            router_priority=os.environ.get('EPICVM_TRAEFIK_ROUTER_PRIORITY', ''),
            credential_secret=_dashboard_secret(),
        )
    return _CONSOLE_ORCHESTRATOR

def _vm_host(host_id=None):
    VM_HOST_REGISTRY.refresh()
    if host_id is None:
        host_id = 'local'
        try:
            host_id = request.values.get('host_id') or request.values.get('host') or host_id
            if request.is_json:
                payload = request.get_json(silent=True) or {}
                if isinstance(payload, dict):
                    host_id = payload.get('host_id') or payload.get('host') or host_id
        except RuntimeError:
            # Helpers used by background jobs have no request context.
            pass
    return VM_HOST_REGISTRY.get(host_id)


def _vm_host_error_response(exc):
    """Normalize provider errors without turning remote 404/409 into 500s."""
    status = int(getattr(exc, 'status', 503) or 503)
    status = status if 400 <= status <= 599 else 503
    code = str(getattr(exc, 'code', 'host_unavailable') or 'host_unavailable')
    messages = {
        'invalid_request': 'The remote VM request is invalid.',
        'authentication_failed': 'The remote VM host rejected authentication.',
        'forbidden': 'The remote VM operation is not permitted.',
        'not_found': 'The requested remote VM resource was not found.',
        'conflict': 'The remote VM request conflicts with existing state.',
        'claim_failed': 'The one-time guest claim was rejected.',
        'host_unavailable': 'The remote VM host is unavailable.',
    }
    response = jsonify({'ok': False, 'error': messages.get(code, 'The remote VM request failed.'), 'code': code})
    response.headers['Cache-Control'] = 'no-store'
    return response, status


def _ensure_remote_vm_exists(host, name):
    """Verify a selected remote host actually owns the VM being mutated.

    A browser may select a host, but it is not authoritative for VM ownership.
    When duplicate names are visible across remote hosts, fail closed instead of
    allowing a destructive request to mutate the wrong machine.
    """
    if getattr(host, 'kind', 'local') != 'remote':
        return
    selected_id = str(getattr(host, 'host_id', '') or '')
    selected_inventory = host.list_vms()
    selected_has_vm = any(str(item.get('name', '')) == str(name) for item in selected_inventory)
    registry = VM_HOST_REGISTRY
    providers = getattr(registry, 'providers', {})
    owners = []
    provider_items = providers.items() if isinstance(providers, dict) else []
    for candidate_id, candidate in provider_items:
        candidate_id = str(candidate_id)
        if candidate_id in {'local', selected_id} or getattr(candidate, 'kind', 'remote') != 'remote':
            continue
        inventories = []
        cached_inventory = getattr(registry, 'cached_inventory', None)
        if callable(cached_inventory):
            try:
                inventories.append(cached_inventory(candidate_id))
            except Exception:
                pass
        try:
            inventories.append(candidate.list_vms())
        except Exception:
            pass
        if any(str(item.get('name', '')) == str(name) for inventory in inventories for item in inventory):
            owners.append(candidate_id)
    if owners:
        owner_list = ', '.join(sorted(set(owners + ([selected_id] if selected_has_vm else []))))
        raise VmHostUnavailable(
            f"VM name is ambiguous across remote hosts: {owner_list}",
            status=409,
            code='ambiguous_vm_owner',
        )
    if not selected_has_vm:
        raise VmHostUnavailable(
            f"VM is not present on host {selected_id or 'remote'}",
            status=404,
            code='not_found',
        )

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
                 `<button onclick="openVM('${i.name}')">Open</button>`+
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
    fetch('/dashboard/api/delete-all-instances',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:'DELETE'})
    }).then(load);
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
                document.getElementById('dash-title').textContent = title || '{{ title }}';
                // update the browser tab title immediately
                try{ document.title = title || '{{ title }}'; }catch(e){}
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
                try{ document.title = title || '{{ manager_name }}'; }catch(e){}
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

# --- Dashboard session helpers ---
def _get_legacy_dashboard_password():
    # Compatibility-only fallback for installations that predate BLOBEDASH_*.
    # The old settings endpoint no longer writes this value.
    cfg = _load_dashboard_settings()
    return cfg.get('new_dashboard_admin_password')

def _sign_v2_token(payload: str) -> str:
    secret = _dashboard_secret()
    if not secret:
        raise ValueError('Dashboard session secret is not configured')
    mac = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    token = f"{payload}:{mac}"
    return base64.urlsafe_b64encode(token.encode('utf-8')).decode('utf-8')

def _verify_v2_token(token_b64: str) -> bool:
    try:
        secret = _dashboard_secret()
        if not secret:
            return False
        raw = base64.urlsafe_b64decode(token_b64.encode('utf-8')).decode('utf-8')
        parts = raw.rsplit(':', 1)
        if len(parts) != 2:
            return False
        payload, mac = parts
        expected = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return False
        # payload format: expiry:random
        exp_str = payload.split(':',1)[0]
        exp = int(exp_str)
        return time.time() < exp
    except Exception:
        return False

v2_auth_required = admin_auth_required

# --- Portal / per-VM user auth helpers ---
def _portal_secret() -> str:
    return os.environ.get('BLOBEVM_USER_SECRET', '').strip()

def _users_db_path():
    return os.path.join(_state_dir(), 'dashboard_users.sqlite3')

def _users_conn():
    conn = sqlite3.connect(_users_db_path())
    conn.row_factory = sqlite3.Row
    return conn

def _init_users_db():
    conn = _users_conn()
    try:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS user_vm_access (
            user_id INTEGER NOT NULL,
            vm_name TEXT NOT NULL,
            PRIMARY KEY (user_id, vm_name),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            vm_name TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_access_request
        ON access_requests(username, vm_name) WHERE status = 'pending';
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            targets TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            finished_at INTEGER,
            progress TEXT NOT NULL DEFAULT '',
            output TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT ''
        );
        ''')
        conn.commit()
    finally:
        conn.close()

def _hash_user_password(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 260000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(dk).decode('ascii')}"

def _verify_user_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split('$', 2)
        if algo != 'pbkdf2_sha256':
            return False
        actual = _hash_user_password(password, salt).split('$', 2)[2]
        return hmac.compare_digest(actual, digest)
    except Exception:
        return False

def _normalize_vm_names(vms):
    out = []
    seen = set()
    for vm in (vms or []):
        name = str(vm or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out

def _known_vm_names():
    try:
        return {str(item.get('name')) for item in manager_json_list() if item.get('name')}
    except Exception:
        return set()

def _validate_known_vm_names(names):
    names = _normalize_vm_names(names)
    if not names:
        return
    known = _known_vm_names()
    missing = sorted(set(names) - known)
    if missing:
        raise ValueError('Unknown VM: ' + ', '.join(missing))

def _job_row_to_dict(row):
    result = dict(row)
    result['targets'] = json.loads(result.get('targets') or '[]')
    return result

def _create_job(job_type: str, targets=None):
    _init_users_db()
    job_id = secrets.token_urlsafe(18)
    now = int(time.time())
    conn = _users_conn()
    try:
        conn.execute(
            'INSERT INTO jobs (id, type, targets, status, created_at, progress) VALUES (?, ?, ?, ?, ?, ?)',
            (job_id, job_type, json.dumps(_normalize_vm_names(targets)), 'queued', now, 'Queued'),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id

def _update_job(job_id: str, *, status=None, progress=None, output=None, error=None):
    fields, values = [], []
    for key, value in (('status', status), ('progress', progress), ('output', output), ('error', error)):
        if value is not None:
            fields.append(f'{key} = ?')
            values.append(str(value)[:16000])
    if status == 'running':
        fields.append('started_at = ?')
        values.append(int(time.time()))
    if status in ('succeeded', 'failed'):
        fields.append('finished_at = ?')
        values.append(int(time.time()))
    if not fields:
        return
    _init_users_db()
    conn = _users_conn()
    try:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", [*values, job_id])
        conn.commit()
    finally:
        conn.close()

def _start_job(job_type: str, targets, work):
    job_id = _create_job(job_type, targets)
    def runner():
        _update_job(job_id, status='running', progress='Running')
        try:
            result = work()
            if isinstance(result, tuple):
                ok, output = result
            else:
                ok, output = True, result or ''
            _update_job(job_id, status='succeeded' if ok else 'failed', progress='Completed' if ok else 'Failed', output=output or '', error='' if ok else output or 'Operation failed')
        except Exception as exc:
            _update_job(job_id, status='failed', progress='Failed', error=str(exc))
    threading.Thread(target=runner, daemon=True).start()
    return job_id

def _user_row_to_dict(row, vm_names=None):
    return {
        'id': row['id'],
        'username': row['username'],
        'isAdmin': bool(row['is_admin']),
        'disabled': bool(row['disabled']),
        'createdAt': int(row['created_at'] or 0),
        'assignedVms': sorted(_normalize_vm_names(vm_names or [])),
    }

def _list_users():
    _init_users_db()
    conn = _users_conn()
    try:
        rows = conn.execute('SELECT * FROM users ORDER BY username COLLATE NOCASE').fetchall()
        access = conn.execute('SELECT user_id, vm_name FROM user_vm_access ORDER BY vm_name COLLATE NOCASE').fetchall()
        vm_map = {}
        for r in access:
            vm_map.setdefault(r['user_id'], []).append(r['vm_name'])
        return [_user_row_to_dict(r, vm_map.get(r['id'], [])) for r in rows]
    finally:
        conn.close()

def _get_user_by_username(username: str):
    _init_users_db()
    conn = _users_conn()
    try:
        row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if not row:
            return None
        vms = [r['vm_name'] for r in conn.execute('SELECT vm_name FROM user_vm_access WHERE user_id = ? ORDER BY vm_name COLLATE NOCASE', (row['id'],)).fetchall()]
        return _user_row_to_dict(row, vms) | { 'password_hash': row['password_hash'] }
    finally:
        conn.close()

def _create_user(username: str, password: str, assigned_vms=None, is_admin: bool=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username or ''):
        raise ValueError('Username must be 3-64 chars using letters, numbers, dot, underscore, or dash')
    if not password or len(password) < 12:
        raise ValueError('Password must be at least 12 characters')
    assigned_vms = _normalize_vm_names(assigned_vms)
    _validate_known_vm_names(assigned_vms)
    _init_users_db()
    conn = _users_conn()
    try:
        cur = conn.execute('INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)', (username, _hash_user_password(password), 1 if is_admin else 0))
        uid = cur.lastrowid
        conn.executemany('INSERT OR IGNORE INTO user_vm_access (user_id, vm_name) VALUES (?, ?)', [(uid, vm) for vm in assigned_vms])
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError('Username already exists')
    finally:
        conn.close()
    return _get_user_by_username(username)

def _update_user(username: str, assigned_vms=None, password=None, disabled=None):
    _init_users_db()
    conn = _users_conn()
    try:
        row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if not row:
            raise ValueError('User not found')
        if password is not None:
            if len(password) < 12:
                raise ValueError('Password must be at least 12 characters')
            conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (_hash_user_password(password), row['id']))
        if disabled is not None:
            conn.execute('UPDATE users SET disabled = ? WHERE id = ?', (1 if disabled else 0, row['id']))
        if assigned_vms is not None:
            assigned_vms = _normalize_vm_names(assigned_vms)
            _validate_known_vm_names(assigned_vms)
            conn.execute('DELETE FROM user_vm_access WHERE user_id = ?', (row['id'],))
            conn.executemany('INSERT OR IGNORE INTO user_vm_access (user_id, vm_name) VALUES (?, ?)', [(row['id'], vm) for vm in assigned_vms])
        conn.commit()
    finally:
        conn.close()
    return _get_user_by_username(username)

def _delete_user(username: str):
    _init_users_db()
    conn = _users_conn()
    try:
        row = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if not row:
            return False
        conn.execute('DELETE FROM user_vm_access WHERE user_id = ?', (row['id'],))
        conn.execute('DELETE FROM users WHERE id = ?', (row['id'],))
        conn.commit()
        return True
    finally:
        conn.close()

def _create_portal_token(username: str, is_admin: bool=False) -> str:
    secret = _portal_secret()
    if not secret:
        raise ValueError('Portal session secret is not configured')
    exp = int(time.time() + 30*24*3600)
    payload = json.dumps({'u': username, 'exp': exp, 'admin': bool(is_admin)}, separators=(',', ':'))
    mac = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{mac}".encode('utf-8')).decode('utf-8')

def _verify_portal_token(token: str):
    try:
        secret = _portal_secret()
        if not secret:
            return None
        raw = base64.urlsafe_b64decode(token.encode('utf-8')).decode('utf-8')
        payload, mac = raw.rsplit('.', 1)
        expected = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return None
        data = json.loads(payload)
        if int(data.get('exp') or 0) < int(time.time()):
            return None
        user = _get_user_by_username(data.get('u') or '')
        if not user or user.get('disabled'):
            return None
        return user
    except Exception:
        return None

def _current_portal_user():
    token = request.cookies.get('Portal-Auth')
    if not token:
        return None
    return _verify_portal_token(token)

def portal_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_portal_user()
        if not user:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _same_origin_request():
            return jsonify({'ok': False, 'error': 'Cross-origin request rejected'}), 403
        request.portal_user = user
        return fn(*args, **kwargs)
    return wrapper

def _vm_access_mode(name: str) -> str:
    meta = _instance_meta(name)
    mode = str(meta.get('access_mode') or 'public').strip().lower()
    return mode if mode in ('public', 'restricted') else 'public'


def _admin_vm_sso_enabled() -> bool:
    """Whether a valid Dashboard-Auth session may open restricted VMs."""
    cfg = _load_dashboard_settings()
    value = cfg.get('admin_vm_sso', True) if isinstance(cfg, dict) else True
    return value is not False


def _admin_vm_sso_authenticated() -> bool:
    return _admin_vm_sso_enabled() and _verify_v2_token(request.cookies.get('Dashboard-Auth', ''))


def _user_can_access_vm(user, name: str) -> bool:
    if _vm_access_mode(name) == 'public':
        return True
    if not user:
        return False
    return name in set(user.get('assignedVms') or [])

def _portal_login_redirect(next_url: str):
    return redirect('/portal/login?next=' + urlrequest.quote(_safe_portal_next(next_url), safe='/:?=&%'))

def _safe_portal_next(next_url: str) -> str:
    value = str(next_url or '/portal')
    parsed = urlparse(value)
    if not value.startswith('/') or value.startswith('//') or parsed.scheme or parsed.netloc:
        return '/portal'
    return value

def _render_vm_denied(name: str, user):
    username = (user or {}).get('username', '')
    page = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VM Login</title><style>body{{margin:0;font-family:Inter,system-ui,Arial;background:radial-gradient(circle at top,#101933 0%,#050816 58%,#03050d 100%);color:#e7f0f4;display:grid;place-items:center;min-height:100vh;padding:24px}}.card{{max-width:430px;width:100%;box-sizing:border-box;background:#071117;border:1px solid #1a2b33;border-top:2px solid #02bdf3;border-radius:5px;padding:34px;box-shadow:none}}.brand{{display:flex;align-items:center;gap:11px;border-bottom:1px solid #1a2b33;padding-bottom:20px;margin-bottom:26px}}.brand-mark{{display:grid;place-items:center;width:32px;height:32px;border-radius:5px;background:#02bdf3;color:#00131b;font-weight:900}}.brand-name{{font-size:18px;font-weight:700}}h1{{margin:0 0 8px;font-size:26px;font-weight:500;letter-spacing:-.025em}}.muted{{color:#9fb0b8;line-height:1.55}}input,button{{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:4px;border:1px solid #29404a;background:#061016;color:#e7f0f4;margin-top:10px;font:inherit}}input:focus{{outline:2px solid rgba(2,189,243,.25);border-color:#02bdf3}}button{{background:#02bdf3;color:#00131b;border-color:#34cdf8;cursor:pointer;font-weight:700}}button:hover{{background:#35cdf6}}#err{{color:#ff9ab0!important;margin-top:10px}}</style></head><body><div class=card><div class=brand><span class=brand-mark>B</span><span class=brand-name>BlobeVM</span></div><h1>VM Login</h1><div class=muted>Sign in to access restricted BlobeVM instances.</div><form onsubmit="return doLogin(event)"><input id=u placeholder="Username" autocomplete="username" /><input id=p type=password placeholder="Password" autocomplete="current-password" /><button>Sign in</button><div id=err style="color:#fca5a5;margin-top:10px"></div></form></div><script>async function doLogin(e){{e.preventDefault();const r=await fetch('/portal/api/auth/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{username:document.getElementById('u').value,password:document.getElementById('p').value}})}});const j=await r.json().catch(()=>({{}}));if(j.ok){{location.href={next_js};return false}}document.getElementById('err').textContent=j.error||'Login failed';return false}}</script></body></html>'''
    return Response(page, mimetype='text/html')

def _enforce_vm_user_access(name: str):
    if _vm_access_mode(name) == 'public' or _admin_vm_sso_authenticated():
        return None
    user = _current_portal_user()
    next_url = request.path
    if request.query_string:
        next_url += '?' + request.query_string.decode('utf-8', 'ignore')
    if not user:
        return _portal_login_redirect(next_url)
    if not _user_can_access_vm(user, name):
        return _render_vm_denied(name, user)
    request.portal_user = user
    return None

def _portal_vm_payload(name: str):
    items = [i for i in manager_json_list() if i.get('name') == name]
    vm = items[0] if items else {'name': name, 'status': 'Unknown', 'url': _build_vm_url(name)}
    vm['accessMode'] = _vm_access_mode(name)
    return vm

def _state_dir():
    return os.environ.get('BLOBEDASH_STATE', '/opt/blobe-vm')

def _repo_manager_path():
    # Fallback path to the repo-managed CLI inside the mounted state dir
    return os.path.join(_state_dir(), 'server', 'blobe-vm-manager')

def _inst_dir():
    return os.path.join(_state_dir(), 'instances')


def _instance_meta_path(name: str):
    return os.path.join(_inst_dir(), name, 'instance.json')


def _instance_meta(name: str):
    path = _instance_meta_path(name)
    try:
        if os.path.isfile(path):
            with open(path, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _set_instance_meta(name: str, key: str, value):
    path = _instance_meta_path(name)
    data = _instance_meta(name)
    if value in (None, ''):
        data.pop(key, None)
    else:
        data[key] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    return data


def _guess_icon_mimetype(path: str) -> str:
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        if header.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        if header.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            return 'image/webp'
        if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
            return 'image/gif'
        if header[:4] == b'\x00\x00\x01\x00':
            return 'image/x-icon'
    except Exception:
        pass
    return 'application/octet-stream'


def _send_icon_file(path: str):
    mimetype = _guess_icon_mimetype(path)
    resp = send_file(path, mimetype=mimetype, conditional=False)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

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
    """Run the selected host manager with given args.

    If the primary manager doesn't support the command (prints Usage/unknown),
    fall back to the repo script. Returns (ok, stdout, stderr, returncode).
    """
    host = _vm_host()
    try:
        r = host.run_manager(*args, capture_output=True, text=True)
    except VmHostUnavailable:
        r = subprocess.CompletedProcess(host.command(*args), 127, '', 'not found')
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
    return env.get('NO_TRAEFIK', '0') == '1'

def _request_host():
    try:
        host = request.headers.get('X-Forwarded-Host') or request.host or ''
        return (host.split(',')[0].strip().split(':')[0] if host else '')
    except Exception:
        return ''


def _external_base_url():
    try:
        proto = (request.headers.get('X-Forwarded-Proto') or request.scheme or 'http').split(',')[0].strip()
        host = (request.headers.get('X-Forwarded-Host') or request.headers.get('Host') or request.host or '').split(',')[0].strip()
        if host:
            return f'{proto}://{host}'
    except Exception:
        pass
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

def _vm_backend_url(name: str, subpath: str = '') -> str:
    prefix = _vm_path_prefix(name)
    suffix = '/' + (subpath or '').lstrip('/') if subpath else '/'
    return f'http://127.0.0.1:20000{prefix}{suffix}' if _is_direct_mode() else f'http://127.0.0.1:3000{prefix}{suffix}'

def _vm_path_prefix(name: str) -> str:
    base_path = _read_env().get('BASE_PATH', '/vm') or '/vm'
    if not base_path.startswith('/'):
        base_path = '/' + base_path
    base_path = base_path.rstrip('/')
    inst_dir = os.path.join(_state_dir(), 'instances', name)
    override = ''
    try:
        meta = os.path.join(inst_dir, '.meta.json')
        if os.path.isfile(meta):
            data = json.load(open(meta, 'r'))
            override = (data.get('path_override') or '').strip()
    except Exception:
        override = ''
    prefix = override or f'{base_path}/{name}'
    if not prefix.startswith('/'):
        prefix = '/' + prefix
    return prefix.rstrip('/')


def _build_vm_embed_url(name: str) -> str:
    if _is_direct_mode():
        return _build_vm_url(name)
    base = _external_base_url()
    root = f"{base}/vmraw/{name}" if base else f"/vmraw/{name}"
    return f"{root}/vnc/index.html?autoconnect=1&resize=remote&clipboard_up=true&clipboard_down=true&clipboard_seamless=true&show_control_bar=true&path=/vmraw/{name}/websockify"


def _build_vm_url(name: str, host_id: str | None = None) -> str:
    """Build a browser URL for a local VM or a host-qualified RemoteVM."""
    if host_id and host_id != 'local':
        prefix = _vm_path_prefix(name)
        base = _external_base_url()
        root = f'{base}{prefix}' if base else prefix
        return f'{root}/?host_id={url_quote(str(host_id), safe="")}'
    if _is_direct_mode():
        host = _request_host()
        if not host:
            return ''
        cname = f'blobevm_{name}'
        hp = _vm_host_port(cname)
        if hp:
            return f'http://{host}:{hp}/'
    prefix = _vm_path_prefix(name)
    base = _external_base_url()
    if base:
        return f'{base}{prefix}/'
    return f'{prefix}/'

def manager_json_list(host_id=None):
    """Return a list of instances with best-effort status and URL.
    Tries the selected provider's manager list first. Falls back to scanning
    the instances directory and asking the provider for each URL individually.
    """
    host_provider = _vm_host(host_id)
    instances = []
    try:
        # Fast path: let the provider parse its inventory while preserving the
        # existing manager command and output format.
        instances = host_provider.list_vms()
        if host_id and host_id != 'local':
            # A remote provider owns its inventory; never fall back to the
            # dashboard server's local instance directory for an empty result.
            normalized = host_provider.normalize_inventory(instances)
            for item in normalized:
                if item.get('name'):
                    item['url'] = _build_vm_url(item['name'], host_id=host_id)
            if hasattr(VM_HOST_REGISTRY, 'remember_inventory'):
                try:
                    VM_HOST_REGISTRY.remember_inventory(host_id, normalized)
                except Exception:
                    pass
            return normalized
        if instances:
            # In direct mode, override URL with host:published-port (or manager port) to avoid container IPs
            if _is_direct_mode():
                host = _request_host()
                for it in instances:
                    cname = f"blobevm_{it['name']}"
                    hp = _vm_host_port(cname)
                    if not hp:
                        try:
                            hp = host_provider.check_output('port', it['name'], text=True).strip()
                        except Exception:
                            hp = ''
                    # Record explicit port for frontend
                    if hp and hp.isdigit():
                        it['port'] = hp
                    if hp and host:
                        it['url'] = f"http://{host}:{hp}/"
            else:
                for it in instances:
                    it['url'] = _build_vm_url(it['name'])
            # Apply transient statuses (e.g., rebuilding/updating)
            for it in instances:
                try:
                    if _has_flag(it['name'], 'rebuilding'):
                        it['status'] = 'Rebuilding...'
                    elif _has_flag(it['name'], 'updating'):
                        it['status'] = 'Updating...'
                except Exception:
                    pass
            return host_provider.normalize_inventory(instances)
    except VmHostUnavailable:
        # A remote host owns its inventory. Falling back to this server's
        # instance directory would relabel local VMs as remote and can send
        # later actions to the wrong destination.
        if host_id and host_id != 'local':
            cached = VM_HOST_REGISTRY.cached_inventory(host_id) if hasattr(VM_HOST_REGISTRY, 'cached_inventory') else []
            if cached:
                for item in cached:
                    item['host_online'] = False
                    item['status'] = 'offline'
                    if item.get('name'):
                        item['url'] = _build_vm_url(item['name'], host_id=host_id)
                return cached
            raise
    except Exception:
        # Local discovery remains best-effort for legacy deployments. A remote
        # provider must never fall back to this server's instance directory.
        if host_id and host_id != 'local':
            raise
        # likely docker CLI or manager is not present -> fall back
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
                    hp = host_provider.check_output('port', name, text=True).strip()
                except Exception:
                    hp = ''
            if hp and host:
                url = f"http://{host}:{hp}/"
            else:
                # Fallback to manager per-VM URL (may be container IP, but last resort)
                try:
                    url = host_provider.check_output('url', name, text=True).strip()
                except Exception:
                    url = ''
            if hp and hp.isdigit():
                port = hp
        else:
            url = _build_vm_url(name)
        # Transient status override
        if _has_flag(name, 'rebuilding'):
            status = 'Rebuilding...'
        elif _has_flag(name, 'updating'):
            status = 'Updating...'
        inst = {'name': name, 'status': status, 'url': url}
        if port:
            inst['port'] = port
        instances.append(inst)
    return host_provider.normalize_inventory(instances)

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


def _docker_inspect_vm(name: str):
    cname = f'blobevm_{name}'
    r = _docker('inspect', cname)
    if r.returncode != 0:
        return None, r.stderr.strip() or 'inspect failed'
    try:
        data = json.loads(r.stdout or '[]')
        if not data:
            return None, 'not-found'
        return data[0], None
    except Exception as e:
        return None, str(e)


def _vm_status_payload(name: str):
    info, err = _docker_inspect_vm(name)
    url = _build_vm_url(name) or ''
    payload = {
        'ok': True,
        'name': name,
        'url': url,
        'exists': False,
        'running': False,
        'healthy': False,
        'state': 'not-found',
        'status': 'not-found',
        'detail': err or 'VM container was not found',
        'crashed': False,
        'startedAt': None,
        'finishedAt': None,
        'exitCode': None,
        'error': '',
        'port': _vm_host_port(f'blobevm_{name}') or ''
    }
    if not info:
        return payload
    state = (info.get('State') or {}) if isinstance(info, dict) else {}
    payload['exists'] = True
    payload['running'] = bool(state.get('Running'))
    payload['healthy'] = (state.get('Health') or {}).get('Status') == 'healthy' if isinstance(state.get('Health'), dict) else payload['running']
    payload['status'] = state.get('Status') or ('running' if payload['running'] else 'stopped')
    payload['state'] = payload['status']
    payload['detail'] = state.get('Error') or state.get('Status') or ''
    payload['startedAt'] = state.get('StartedAt')
    payload['finishedAt'] = state.get('FinishedAt')
    payload['exitCode'] = state.get('ExitCode')
    payload['error'] = state.get('Error') or ''
    oomkilled = bool(state.get('OOMKilled'))
    restarting = bool(state.get('Restarting'))
    payload['oomKilled'] = oomkilled
    payload['restarting'] = restarting
    payload['crashed'] = (not payload['running']) and (
        (payload['exitCode'] not in (None, 0)) or
        oomkilled or
        bool(payload['error']) or
        restarting or
        payload['status'] == 'dead'
    )
    try:
        opt = dash_optimizer.status()
        vm_states = ((opt.get('stats') or {}).get('vmStates') or []) if isinstance(opt, dict) else []
        vm_meta = next((v for v in vm_states if v.get('name') == name), None)
        if vm_meta:
            payload['optimizer'] = vm_meta
            payload['recoveryState'] = vm_meta.get('recoveryState')
            payload['protectedVm'] = bool(vm_meta.get('protected'))
            payload['activityClass'] = vm_meta.get('activityClass')
            payload['profile'] = vm_meta.get('profile')
            payload['unstable'] = bool(vm_meta.get('unstable'))
        try:
            payload['notifications'] = dash_optimizer.get_vm_notifications(name)
        except Exception:
            payload['notifications'] = []
    except Exception:
        pass
    return payload


def _tail_vm_logs(name: str, lines: int = 160) -> str:
    cname = f'blobevm_{name}'
    try:
        r = _docker('logs', '--tail', str(lines), cname)
        if r.returncode == 0:
            return (r.stdout or '') + (("\n" + r.stderr) if r.stderr else '')
        return r.stderr.strip() or r.stdout.strip() or ''
    except Exception as e:
        return str(e)


def _recover_vm(name: str, source: str = 'manual', aggressive: bool = True, mode: str = 'standard'):
    attempts = []
    before = _vm_status_payload(name)
    recovery_state = str(before.get('recoveryState') or '').lower()
    protected_vm = bool(before.get('protectedVm'))
    if before.get('running') and not before.get('crashed'):
        return {'ok': True, 'recovered': True, 'attempts': attempts, 'status': before, 'message': 'VM already running', 'mode': mode}
    if mode == 'cautious' or protected_vm:
        sequence = ['start']
        if before.get('crashed'):
            sequence.append('restart')
    elif mode == 'aggressive' or recovery_state == 'restart-loop':
        sequence = ['start', 'restart', 'recreate']
    else:
        sequence = ['start']
        if before.get('crashed') or aggressive:
            sequence.append('restart')
        if aggressive:
            sequence.append('recreate')
    for action in sequence:
        try:
            proc = _vm_host().run_manager(action, name, capture_output=True, text=True, timeout=90)
            attempt = {
                'action': action,
                'ok': proc.returncode == 0,
                'stdout': (proc.stdout or '').strip()[-2000:],
                'stderr': (proc.stderr or '').strip()[-2000:],
                'returncode': proc.returncode,
            }
        except Exception as e:
            attempt = {'action': action, 'ok': False, 'stdout': '', 'stderr': str(e), 'returncode': None}
        attempts.append(attempt)
        time.sleep(2.5)
        current = _vm_status_payload(name)
        if current.get('running') and (current.get('healthy') or current.get('state') == 'running'):
            return {'ok': True, 'recovered': True, 'attempts': attempts, 'status': current, 'message': f'VM recovered via {action}', 'source': source, 'mode': mode}
    final = _vm_status_payload(name)
    return {'ok': False, 'recovered': False, 'attempts': attempts, 'status': final, 'message': 'VM recovery failed', 'source': source, 'mode': mode}


def _escalate_vm_to_hermes(name: str, reason: str, extra=None):
    extra = extra or {}
    payload = {
        'vm': name,
        'reason': reason,
        'status': _vm_status_payload(name),
        'logs': _tail_vm_logs(name, 120),
        'extra': extra,
        'ts': int(time.time()),
        'host': socket.gethostname(),
    }
    esc_dir = os.path.join(_state_dir(), 'dashboard', 'escalations')
    os.makedirs(esc_dir, exist_ok=True)
    esc_path = os.path.join(esc_dir, f"{name}-{payload['ts']}.json")
    with open(esc_path, 'w') as f:
        json.dump(payload, f, indent=2)
    msg = (
        f"{MANAGER_NAME} recovery request from the dashboard. Act as the recovery operator: "
        "inspect the VM and its recent logs, determine why it is down, and recover it "
        "if safe and possible. Verify the result instead of assuming success. "
        f"Host: {payload['host']}. VM: '{name}'. Reason: {reason}. "
        f"Status: {json.dumps(payload['status'])}. Recent logs:\n{payload['logs'][:3000]}"
    )
    delivered = False
    cli_error = ''
    try:
        proc = subprocess.run(
            ['hermes', 'chat', '-q', msg, '--toolsets', 'terminal', '--max-turns', '20', '--source', 'blobevm-dashboard', '--quiet'],
            capture_output=True,
            text=True,
            timeout=120,
        )
        delivered = proc.returncode == 0
        if not delivered:
            cli_error = (proc.stderr or proc.stdout or '').strip()[:1200]
    except Exception as e:
        cli_error = str(e)
    return {'ok': True, 'queued': delivered, 'path': esc_path, 'cliError': cli_error, 'payload': payload}

@app.get('/dashboard/api/modeinfo')
@auth_required
def api_modeinfo():
    env = _read_env()
    merged = env.get('NO_TRAEFIK', '0') == '0'
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
    return {'title': DASHBOARD_TITLE, 'favicon': ''}


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
        return _send_icon_file(fav_path)
    # If no local file, try to redirect to configured favicon URL
    cfg = _load_dashboard_settings()
    if cfg.get('favicon'):
        return '', 302, {'Location': cfg.get('favicon'), 'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0', 'Pragma': 'no-cache', 'Expires': '0'}
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
    data = request.get_json(silent=True) if request.is_json else None
    data = data if isinstance(data, dict) else request.values
    title = str(data.get('title','') or '').strip()
    favicon = str(data.get('favicon','') or '').strip()
    cfg = _load_dashboard_settings()
    if title:
        cfg['title'] = title
    if 'adminVmSso' in data or 'admin_vm_sso' in data:
        raw_sso = data.get('adminVmSso', data.get('admin_vm_sso'))
        if isinstance(raw_sso, str):
            raw_sso = raw_sso.strip().lower() in ('1', 'true', 'yes', 'on')
        cfg['admin_vm_sso'] = bool(raw_sso)
    # Allow setting the v2 dashboard admin password from the old dashboard settings UI
    # Do not write the legacy dashboard password. New credentials are managed
    # exclusively by BLOBEDASH_USER/BLOBEDASH_PASS in /opt/blobe-vm/.env.
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


    # Serve dashboard v2 production assets requested from absolute `/assets/*` paths
    @app.route('/assets/<path:path>')
    def serve_dashboard_v2_root_assets(path):
        base = os.path.join(_state_dir(), 'dashboard_v2')
        assets_dir = os.path.join(base, 'dist', 'assets')
        cand = os.path.join(assets_dir, path)
        if os.path.isfile(cand):
            return send_from_directory(assets_dir, path)
        # not found here - 404 and let other handlers handle it if needed
        return 'Not found', 404


    # Also handle requests that include the Dashboard prefix explicitly
    @app.route('/Dashboard/assets/<path:path>')
    def serve_dashboard_v2_prefixed_assets(path):
        base = os.path.join(_state_dir(), 'dashboard_v2')
        assets_dir = os.path.join(base, 'dist', 'assets')
        cand = os.path.join(assets_dir, path)
        if os.path.isfile(cand):
            return send_from_directory(assets_dir, path)
        return 'Not found', 404


@app.get('/dashboard/auth/vm/<name>')
def dashboard_vm_forward_auth(name):
    if _admin_vm_sso_authenticated():
        return Response('OK', 200)
    user = _current_portal_user()
    next_url = request.headers.get('X-Forwarded-Uri') or request.args.get('next') or f'/dashboard/vm/{name}/'
    ext_base = _external_base_url()
    if not user:
        login_path = '/portal/login?next=' + urlrequest.quote(next_url, safe='/:?=&%')
        login_url = f'{ext_base}{login_path}' if ext_base else login_path
        return Response('', 302, {'Location': login_url})
    if not _user_can_access_vm(user, name):
        denied_path = '/dashboard/vm/' + urlrequest.quote(name, safe='') + '/'
        denied_url = f'{ext_base}{denied_path}' if ext_base else denied_path
        return Response('', 302, {'Location': denied_url})
    return Response('OK', 200)


@app.get('/dashboard/console/<name>/')
def dashboard_console_launch(name):
    denied = _enforce_vm_user_access(name)
    if denied is not None:
        return denied
    try:
        safe = str(name or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,62}', safe):
            return Response('Invalid VM name.', 400)
        orchestrator = _console_orchestrator()
        if not orchestrator.has_auto_login(safe):
            if _admin_vm_sso_authenticated():
                return redirect(f'/dashboard/console/{url_quote(safe, safe="")}/setup')
            return Response('Automatic console login must be configured by an administrator.', 409)
        data = orchestrator.build_json_auth_data(safe)
        client_id = base64.urlsafe_b64encode(f'{safe}\0c\0json'.encode('utf-8')).decode('ascii').rstrip('=')
        prefix = f'/vm/{url_quote(safe, safe="")}'
        token_endpoint = f'{prefix}/api/tokens'
        target = f'{prefix}/#/client/{client_id}'
        # Exchange the short-lived JSON assertion before navigation, then keep
        # it out of browser history.  Guacamole's resulting session token is
        # sufficient for ordinary refreshes of the clean client URL.
        response = Response(
            '<!doctype html><meta charset="utf-8"><title>Opening console</title>'
            '<body style="background:#000;color:#ddd;font:16px system-ui">Opening secure console…'
            '<script>(async()=>{'
            'localStorage.removeItem("GUAC_AUTH_TOKEN");sessionStorage.removeItem("GUAC_AUTH_TOKEN");'
            f'const body=new URLSearchParams({{data:{json.dumps(data)}}});'
            f'const response=await fetch({json.dumps(token_endpoint)},{{method:"POST",headers:{{"Content-Type":"application/x-www-form-urlencoded"}},body}});'
            'const result=await response.json();if(!response.ok||!result.authToken)throw new Error("authentication failed");'
            'localStorage.setItem("GUAC_AUTH_TOKEN",JSON.stringify(result.authToken));'
            f'location.replace({json.dumps(target)});'
            '})().catch(()=>{document.body.textContent="Secure console authentication failed. Return to EpicVM and try again."});</script>',
            mimetype='text/html',
        )
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
    except ConsoleOrchestrationError as exc:
        return Response(str(exc), int(getattr(exc, 'status', 502) or 502))


@app.get('/dashboard/console/<name>/setup')
@admin_auth_required
def dashboard_console_setup(name):
    safe = str(name or '').strip().lower()
    csrf = _csrf_token_for_session()
    return Response(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Enable automatic console login</title><style>body{{margin:0;background:#050816;color:#e7f0f4;font:15px Inter,system-ui;display:grid;place-items:center;min-height:100vh}}form{{width:min(420px,calc(100vw - 48px));background:#071117;border:1px solid #1a2b33;padding:30px}}input,button{{display:block;box-sizing:border-box;width:100%;margin-top:12px;padding:12px;background:#061016;color:#e7f0f4;border:1px solid #29404a}}button{{background:#02bdf3;color:#00131b;font-weight:700}}#error{{color:#ff9ab0;margin-top:12px}}</style></head><body><form id="setup"><h1>Enable automatic login</h1><p>Enter the Windows credentials once. EpicVM will encrypt them at rest and use short-lived Guacamole launch tokens.</p><input id="username" autocomplete="username" placeholder="Windows username" required><input id="password" type="password" autocomplete="current-password" placeholder="Windows password" required><button>Enable and open desktop</button><div id="error"></div></form><script>document.getElementById('setup').addEventListener('submit',async(e)=>{{e.preventDefault();const r=await fetch('/dashboard/api/console-credentials/{url_quote(safe, safe="")}',{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':'{csrf}'}},body:JSON.stringify({{username:document.getElementById('username').value,password:document.getElementById('password').value}})}});const j=await r.json().catch(()=>({{}}));document.getElementById('password').value='';if(r.ok&&j.ok)location.href=j.launchUrl;else document.getElementById('error').textContent=j.error?.message||j.error||'Setup failed';}});</script></body></html>''', mimetype='text/html', headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@app.post('/dashboard/api/console-credentials/<name>')
@admin_auth_required
def dashboard_console_credentials(name):
    if not _request_is_https():
        return jsonify({'ok': False, 'error': 'Automatic console login setup requires HTTPS'}), 426
    payload = request.get_json(silent=True) or {}
    username = str(payload.get('username') or '')
    password = str(payload.get('password') or '')
    try:
        _console_orchestrator().enable_auto_login(name=name, username=username, password=password)
        response = jsonify({'ok': True, 'launchUrl': f'/dashboard/console/{url_quote(str(name).lower(), safe="")}/'})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ConsoleOrchestrationError as exc:
        response = jsonify({'ok': False, 'error': {'code': str(getattr(exc, 'code', 'console_error')), 'message': str(exc)}})
        response.headers['Cache-Control'] = 'no-store'
        return response, int(getattr(exc, 'status', 502) or 502)
    finally:
        username = password = ''


@app.post('/portal/api/auth/login')
def portal_login_api():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    if not _same_origin_request():
        return jsonify({'ok': False, 'error': 'Cross-origin request rejected'}), 403
    user = _get_user_by_username(username)
    if not user or user.get('disabled') or not _verify_user_password(password, user.get('password_hash') or ''):
        return jsonify({'ok': False, 'error': 'invalid'}), 401
    try:
        token = _create_portal_token(user['username'], bool(user.get('isAdmin')))
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 503
    resp = jsonify({'ok': True, 'user': {k:v for k,v in user.items() if k != 'password_hash'}})
    resp.set_cookie('Portal-Auth', token, httponly=True, samesite='Strict', secure=_request_is_https(), max_age=30*24*3600, path='/')
    return resp

@app.post('/portal/api/auth/logout')
def portal_logout_api():
    if not _same_origin_request():
        return jsonify({'ok': False, 'error': 'Cross-origin request rejected'}), 403
    resp = jsonify({'ok': True})
    resp.delete_cookie('Portal-Auth', path='/')
    return resp

@app.get('/portal/api/auth/status')
def portal_auth_status_api():
    user = _current_portal_user()
    return jsonify({'ok': bool(user), 'user': ({k:v for k,v in user.items() if k != 'password_hash'} if user else None)})

@app.get('/portal/api/vms')
@portal_auth_required
def portal_vms_api():
    user = request.portal_user
    assigned = _normalize_vm_names(user.get('assignedVms') or [])
    all_vms = {item.get('name'): item for item in manager_json_list() if item.get('name')}
    visible = []
    for name in all_vms:
        if _vm_access_mode(name) == 'public' or name in assigned:
            visible.append(name)
    vms = []
    for name in visible:
        try:
            status = _vm_status_payload(name)
        except Exception:
            status = {'ok': False, 'name': name, 'status': 'unknown', 'state': 'unknown', 'running': False, 'healthy': False, 'crashed': False, 'exists': False}
        item = {
            'name': name,
            'url': _build_vm_url(name),
            'accessMode': _vm_access_mode(name),
            'allowed': True,
            'status': status.get('status') or status.get('state') or 'Unknown',
            'state': status.get('state') or 'unknown',
            'running': bool(status.get('running')),
            'healthy': bool(status.get('healthy')),
            'crashed': bool(status.get('crashed')),
            'exists': bool(status.get('exists', True)),
            'recoveryState': status.get('recoveryState') or 'healthy',
            'profile': status.get('profile') or 'desktop',
        }
        vms.append(item)
    return jsonify({'ok': True, 'user': {k:v for k,v in user.items() if k != 'password_hash'}, 'vms': vms})

@app.post('/portal/api/start/<name>')
@portal_auth_required
def portal_start_vm(name):
    if not _user_can_access_vm(request.portal_user, name):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403
    try:
        _vm_host().check_call('start', name)
        try:
            dash_optimizer.note_vm_activity(name, 'portal-start')
        except Exception:
            pass
        return jsonify({'ok': True, 'wrapperUrl': f'/vm/{name}/', 'openUrl': _build_vm_url(name)})
    except subprocess.CalledProcessError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/portal/api/stop/<name>')
@portal_auth_required
def portal_stop_vm(name):
    if not _user_can_access_vm(request.portal_user, name):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403
    try:
        _vm_host().check_call('stop', name)
        return jsonify({'ok': True})
    except subprocess.CalledProcessError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/portal/api/request-access/<name>')
@portal_auth_required
def portal_request_access(name):
    user = request.portal_user
    if name not in _known_vm_names():
        return jsonify({'ok': False, 'error': 'VM not found'}), 404
    if _user_can_access_vm(user, name):
        return jsonify({'ok': False, 'error': 'You already have access'}), 400
    note = ''
    try:
        data = request.get_json(silent=True) or {}
        note = str(data.get('note') or '').strip()[:1000]
    except Exception:
        note = ''
    _init_users_db()
    conn = _users_conn()
    try:
        existing = conn.execute('SELECT id FROM access_requests WHERE username = ? AND vm_name = ? AND status = ?', (user['username'], name, 'pending')).fetchone()
        if existing:
            return jsonify({'ok': True, 'alreadyPending': True})
        conn.execute('INSERT INTO access_requests (username, vm_name, note, status) VALUES (?, ?, ?, ?)', (user['username'], name, note, 'pending'))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})

@app.get('/portal')
@app.get('/portal/')
def portal_home():
    user = _current_portal_user()
    if not user:
        return redirect('/portal/login')
    page = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VM Portal</title><style>:root{color-scheme:dark}body{margin:0;font-family:Inter,system-ui,Arial;background:radial-gradient(circle at top,#101933 0%,#050816 58%,#03050d 100%);color:#eef4ff;padding:24px;min-height:100vh;box-sizing:border-box}.wrap{max-width:1180px;margin:0 auto}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:18px}.eyebrow{letter-spacing:.18em;text-transform:uppercase;font-size:12px;color:#93c5fd;margin-bottom:8px}.hero{padding:24px;border-radius:24px;border:1px solid rgba(255,255,255,.1);background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.03));box-shadow:0 30px 80px rgba(0,0,0,.34)}h1{margin:0 0 8px;font-size:clamp(32px,5vw,50px)}.sub{color:#b9c7e5;max-width:780px;line-height:1.6}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin-top:24px}.card{background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.03));border:1px solid rgba(255,255,255,.08);border-radius:20px;padding:20px;box-shadow:0 18px 50px rgba(0,0,0,.24)}.muted{opacity:.78}.status{display:inline-flex;padding:8px 11px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.03em;background:rgba(255,255,255,.07);margin-bottom:12px}.status.live{color:#bbf7d0;background:rgba(22,163,74,.18)}.status.down{color:#fde68a;background:rgba(245,158,11,.16)}button,a.btn{display:inline-block;margin:10px 10px 0 0;padding:11px 14px;border-radius:12px;border:none;background:linear-gradient(135deg,#3b82f6,#7c3aed);color:#fff;text-decoration:none;cursor:pointer;font-weight:700}.alt{background:rgba(255,255,255,.08)}.ghost{background:transparent;border:1px solid rgba(255,255,255,.12)}.home-style{color:#e7f0f4}.hero{border-radius:5px;border:1px solid #1a2b33;border-top:2px solid #02bdf3;background:#071117;box-shadow:none;padding:28px}.card{border-radius:5px;background:#071117;border:1px solid #1a2b33;box-shadow:none}.eyebrow{color:#6e8791;letter-spacing:.18em}.sub,.muted{color:#9fb0b8;opacity:1}.status{border-radius:4px;background:#0b1820;border:1px solid #263b44}.status.live{color:#83eb9b;background:#0b1d17}.status.down{color:#f7c948;background:#17170f}button,a.btn{border-radius:4px;background:#02bdf3;color:#00131b;box-shadow:none}button:hover,a.btn:hover{background:#35cdf6}.alt,.ghost{background:#0b1820;color:#dce7ec;border:1px solid #29404a}.top{border-bottom:1px solid #1a2b33;padding-bottom:20px}.grid{gap:16px}.card h3{font-weight:600}.card strong{color:#e7f0f4}input,button{font:inherit}@media (max-width:720px){body{padding:14px}.hero,.card{padding:18px}}</style></head><body><div class=wrap><div class=top><div class=hero style="flex:1 1 740px"><div class=eyebrow>VM Portal</div><h1>Your VMs</h1><div class=sub id=welcome>Loading your VM access…</div></div><div><button class="alt" onclick="logoutUser()">Log out</button></div></div><div id=list class=grid></div></div><script>async function logoutUser(){await fetch('/portal/api/auth/logout',{method:'POST'}).catch(()=>null);location.href='/portal/login'} function esc(s){return String(s??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))} function actionButton(label, cls, act, name){return '<button'+(cls?' class="'+cls+'"':'')+' onclick='+JSON.stringify("vmAction('"+act+"', "+JSON.stringify(String(name))+")")+'>'+label+'</button>'} async function load(){const r=await fetch('/portal/api/vms'); if(r.status===401){location.href='/portal/login'; return} const j=await r.json().catch(()=>({})); const user=(j.user&&j.user.username)||'user'; document.getElementById('welcome').textContent='Signed in as '+user+'. Open, start, or stop any VM your account can access.'; const list=document.getElementById('list'); list.innerHTML=''; const vms=(j.vms||[]); if(!vms.length){list.innerHTML='<div class="card"><h3 style="margin-top:0">No VMs available</h3><div class="muted">You do not currently have any public or assigned VMs available in the portal.</div></div>'; return} vms.forEach(vm=>{const div=document.createElement('div'); div.className='card'; const live=String(vm.status||'').toLowerCase()==='running' || vm.running; const actions=['<a class="btn" href="/vm/'+encodeURIComponent(vm.name)+'/">Open</a>']; if(vm.allowed){actions.push(actionButton('Start','', 'start', vm.name)); actions.push(actionButton('Stop','alt', 'stop', vm.name))} div.innerHTML='<div class="status '+(live?'live':'down')+'">'+(live?'Online':'Offline')+'</div><h3 style="margin:0 0 8px">'+esc(vm.name)+'</h3><div class="muted">Access mode: '+esc(vm.accessMode||'public')+'</div><div class="muted" style="margin-top:6px">Current state: '+esc(vm.status||'Unknown')+'</div><div>'+actions.join('')+'</div>'; list.appendChild(div)})} async function waitForVm(name,maxMs){const deadline=Date.now()+(maxMs||45000); while(Date.now()<deadline){ try{ const r=await fetch('/dashboard/api/vm/'+encodeURIComponent(name)+'/status',{cache:'no-store'}); const j=await r.json().catch(()=>({})); if(j && j.ok && j.running) return true; }catch(_){ } await new Promise(r=>setTimeout(r,1500)); } return false } async function vmAction(act,name){const r=await fetch('/portal/api/'+act+'/'+encodeURIComponent(name),{method:'POST'}); const j=await r.json().catch(()=>({})); if(!j.ok){ alert('Failed: '+(j.error||r.status)); load(); return } if(act==='start'){ const ready=await waitForVm(name,45000); const target=j.wrapperUrl||('/vm/'+encodeURIComponent(name)+'/'); if(!ready){ alert('VM start request sent. Opening the wrapper while it finishes waking up.'); } window.location.href=target; return } alert(act+' request sent'); load()} load()</script></body></html>'''
    return Response(page, mimetype='text/html')

@app.get('/portal/login')
def portal_login_page():
    if _current_portal_user():
        return redirect(_safe_portal_next(request.args.get('next')))
    next_url = _safe_portal_next(request.args.get('next'))
    next_js = json.dumps(next_url)
    page = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VM Login</title><style>body{{margin:0;font-family:Inter,system-ui,Arial;background:radial-gradient(circle at top,#101933 0%,#050816 58%,#03050d 100%);color:#e7f0f4;display:grid;place-items:center;min-height:100vh;padding:24px}}.card{{max-width:430px;width:100%;box-sizing:border-box;background:#071117;border:1px solid #1a2b33;border-top:2px solid #02bdf3;border-radius:5px;padding:34px;box-shadow:none}}.brand{{display:flex;align-items:center;gap:11px;border-bottom:1px solid #1a2b33;padding-bottom:20px;margin-bottom:26px}}.brand-mark{{display:grid;place-items:center;width:32px;height:32px;border-radius:5px;background:#02bdf3;color:#00131b;font-weight:900}}.brand-name{{font-size:18px;font-weight:700}}h1{{margin:0 0 8px;font-size:26px;font-weight:500;letter-spacing:-.025em}}.muted{{color:#9fb0b8;line-height:1.55}}input,button{{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:4px;border:1px solid #29404a;background:#061016;color:#e7f0f4;margin-top:10px;font:inherit}}input:focus{{outline:2px solid rgba(2,189,243,.25);border-color:#02bdf3}}button{{background:#02bdf3;color:#00131b;border-color:#34cdf8;cursor:pointer;font-weight:700}}button:hover{{background:#35cdf6}}#err{{color:#ff9ab0!important;margin-top:10px}}</style></head><body><div class=card><div class=brand><span class=brand-mark>B</span><span class=brand-name>BlobeVM</span></div><h1>VM Login</h1><div class=muted>Sign in to access restricted BlobeVM instances.</div><form onsubmit="return doLogin(event)"><input id=u placeholder="Username" autocomplete="username" /><input id=p type=password placeholder="Password" autocomplete="current-password" /><button>Sign in</button><div id=err style="color:#fca5a5;margin-top:10px"></div></form></div><script>async function doLogin(e){{e.preventDefault();const r=await fetch('/portal/api/auth/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{username:document.getElementById('u').value,password:document.getElementById('p').value}})}});const j=await r.json().catch(()=>({{}}));if(j.ok){{location.href={next_js};return false}}document.getElementById('err').textContent=j.error||'Login failed';return false}}</script></body></html>'''
    return Response(page, mimetype='text/html')

@app.get('/dashboard/api/users')
@v2_auth_required
def dashboard_users_list():
    _init_users_db()
    conn = _users_conn()
    try:
        reqs = [dict(r) for r in conn.execute('SELECT id, username, vm_name, note, status, created_at FROM access_requests ORDER BY created_at DESC LIMIT 200').fetchall()]
    finally:
        conn.close()
    return jsonify({'ok': True, 'users': _list_users(), 'requests': reqs, 'vms': manager_json_list()})

@app.post('/dashboard/api/users')
@v2_auth_required
def dashboard_users_create():
    data = request.get_json(silent=True) or {}
    try:
        user = _create_user(str(data.get('username') or '').strip(), str(data.get('password') or ''), data.get('assignedVms') or [])
        return jsonify({'ok': True, 'user': {k:v for k,v in user.items() if k != 'password_hash'}})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

@app.post('/dashboard/api/users/<username>')
@v2_auth_required
def dashboard_users_update(username):
    data = request.get_json(silent=True) or {}
    try:
        user = _update_user(username, assigned_vms=(data.get('assignedVms') if 'assignedVms' in data else None), password=(str(data.get('password')) if data.get('password') else None), disabled=(data.get('disabled') if 'disabled' in data else None))
        return jsonify({'ok': True, 'user': {k:v for k,v in user.items() if k != 'password_hash'}})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

@app.post('/dashboard/api/users/<username>/delete')
@v2_auth_required
def dashboard_users_delete(username):
    ok = _delete_user(username)
    return jsonify({'ok': ok})

@app.post('/dashboard/api/access-requests/<int:req_id>/action')
@v2_auth_required
def dashboard_access_request_action(req_id):
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '').strip().lower()
    if action not in ('approve', 'deny', 'dismiss'):
        return jsonify({'ok': False, 'error': 'Invalid action'}), 400
    _init_users_db()
    conn = _users_conn()
    try:
        row = conn.execute('SELECT id, username, vm_name, note, status FROM access_requests WHERE id = ?', (req_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Request not found'}), 404
        if action == 'approve':
            user = _get_user_by_username(row['username'])
            if not user:
                return jsonify({'ok': False, 'error': 'User no longer exists'}), 404
            assigned = sorted(set((user.get('assignedVms') or []) + [row['vm_name']]))
            _update_user(row['username'], assigned_vms=assigned)
            new_status = 'approved'
        elif action == 'deny':
            new_status = 'denied'
        else:
            new_status = 'dismissed'
        conn.execute('UPDATE access_requests SET status = ? WHERE id = ?', (new_status, req_id))
        conn.commit()
        return jsonify({'ok': True, 'status': new_status, 'request': {'id': row['id'], 'username': row['username'], 'vm_name': row['vm_name']}})
    finally:
        conn.close()

# --- Dashboard v2 auth routes (top-level) ---
@app.post('/Dashboard/api/auth/login')
def dashboard_v2_login_public():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'ok': False}), 400
    if not _same_origin_request():
        return jsonify({'ok': False, 'error': 'Cross-origin request rejected'}), 403
    if _allow_insecure_dashboard():
        return jsonify({'ok': True, 'authRequired': False})
    user, expected_password = _admin_credentials()
    if not user or not expected_password or not _dashboard_secret():
        return jsonify({'ok': False, 'error': 'Dashboard authentication is not configured'}), 503
    username = str(data.get('username', '')) if isinstance(data, dict) else ''
    pw = str(data.get('password', '')) if isinstance(data, dict) else ''
    remote = request.remote_addr or 'unknown'
    now = time.time()
    with _LOGIN_LOCK:
        attempt = _LOGIN_ATTEMPTS.get(remote, {'count': 0, 'until': 0})
        if attempt['until'] > now:
            return jsonify({'ok': False, 'error': 'Try again shortly'}), 429
    if not hmac.compare_digest(username, user) or not _admin_password_matches(pw, expected_password):
        with _LOGIN_LOCK:
            count = _LOGIN_ATTEMPTS.get(remote, {}).get('count', 0) + 1
            _LOGIN_ATTEMPTS[remote] = {'count': count, 'until': now + min(30, 2 ** min(count, 5))}
        return jsonify({'ok': False, 'error': 'invalid'}), 401
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(remote, None)
    exp = int(time.time() + 24*3600)
    payload = f"{exp}:{os.urandom(8).hex()}"
    token = _sign_v2_token(payload)
    resp = jsonify({'ok': True, 'expiry': exp, 'authRequired': True})
    resp.set_cookie('Dashboard-Auth', token, httponly=True, samesite='Strict', secure=_request_is_https(), max_age=24*3600, path='/')
    return resp


@app.get('/Dashboard/api/auth/status')
def dashboard_v2_status_public():
    if _allow_insecure_dashboard():
        return jsonify({'ok': True, 'authRequired': False})
    user, password = _admin_credentials()
    if not user or not password or not _dashboard_secret():
        return jsonify({'ok': False, 'authRequired': True, 'configured': False}), 503
    token = request.cookies.get('Dashboard-Auth')
    ok = bool(token and _verify_v2_token(token))
    return jsonify({'ok': ok, 'authRequired': True, 'configured': True, 'username': user if ok else None})


@app.get('/dashboard/api/auth/csrf')
@auth_required
def dashboard_v2_csrf():
    token = _csrf_token_for_session()
    response = jsonify({'ok': bool(token), 'csrfToken': token if token else None})
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.post('/Dashboard/api/auth/logout')
def dashboard_v2_logout_public():
    if not _same_origin_request():
        return jsonify({'ok': False, 'error': 'Cross-origin request rejected'}), 403
    resp = jsonify({'ok': True})
    resp.delete_cookie('Dashboard-Auth', path='/')
    return resp


# --- Dashboard v2 static page routes (top-level) ---
@app.route('/Dashboard')
@app.route('/Dashboard/', defaults={'path': ''})
@app.route('/Dashboard/<path:path>')
def serve_dashboard_v2_public(path=''):
    base = os.path.join(_state_dir(), 'dashboard_v2')
    dist = os.path.join(base, 'dist')
    if path:
        cand = os.path.join(dist, path)
        if os.path.isfile(cand):
            return send_from_directory(dist, path)
        static_dir = os.path.join(base, 'src')
        cand2 = os.path.join(static_dir, path)
        if os.path.isfile(cand2):
            return send_from_directory(static_dir, path)
    indexcand = os.path.join(dist, 'index.html')
    if os.path.isfile(indexcand):
        return send_from_directory(dist, 'index.html')
    dev_index = os.path.join(base, 'index.html')
    if os.path.isfile(dev_index):
        return send_from_directory(base, 'index.html')
    return 'Dashboard v2 not built', 404


@app.route('/assets/<path:path>')
def serve_dashboard_v2_root_assets_public(path):
    base = os.path.join(_state_dir(), 'dashboard_v2')
    assets_dir = os.path.join(base, 'dist', 'assets')
    cand = os.path.join(assets_dir, path)
    if os.path.isfile(cand):
        return send_from_directory(assets_dir, path)
    return 'Not found', 404


@app.route('/Dashboard/assets/<path:path>')
def serve_dashboard_v2_prefixed_assets_public(path):
    base = os.path.join(_state_dir(), 'dashboard_v2')
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
            subprocess.Popen(_vm_host().command('set-title', n, t), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    try:
        # Fire-and-forget propagation
        threading.Thread(target=_propagate_title, args=(name, title), daemon=True).start()
    except Exception:
        pass

    return jsonify({'ok': bool(ok)})


@app.get('/dashboard/api/vm-settings/<name>')
@auth_required
def api_get_vm_settings(name):
    cfg = _load_dashboard_settings()
    vm_titles = cfg.get('vm_titles', {}) if isinstance(cfg.get('vm_titles', {}), dict) else {}
    meta = _instance_meta(name)
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', name)
    fav_path = os.path.join(_state_dir(), 'dashboard', 'vm-fav', f"{safe}.ico")
    assigned_users = [u['username'] for u in _list_users() if name in (u.get('assignedVms') or [])]
    return jsonify({
        'ok': True,
        'name': name,
        'title': vm_titles.get(name, ''),
        'hostOverride': meta.get('host_override', ''),
        'pathOverride': meta.get('path_override', ''),
        'faviconUrl': f'/dashboard/vm-favicon/{name}' if os.path.isfile(fav_path) else '',
        'accessMode': _vm_access_mode(name),
        'assignedUsers': assigned_users,
    })


@app.post('/dashboard/api/vm-settings/<name>')
@auth_required
def api_set_vm_settings(name):
    try:
        data = request.get_json(silent=True) or {}
        host_override = (data.get('hostOverride') if isinstance(data, dict) else None)
        title = (data.get('title') if isinstance(data, dict) else None)
        access_mode = (data.get('accessMode') if isinstance(data, dict) else None)
        changed_runtime = False

        if title is not None:
            cfg = _load_dashboard_settings()
            vm_titles = cfg.get('vm_titles', {}) if isinstance(cfg.get('vm_titles', {}), dict) else {}
            title = str(title).strip()
            if title:
                vm_titles[name] = title
            else:
                vm_titles.pop(name, None)
            cfg['vm_titles'] = vm_titles
            _save_dashboard_settings(cfg)
            _set_instance_meta(name, 'title', title)
            changed_runtime = True

        if host_override is not None:
            host_override = str(host_override).strip()
            _set_instance_meta(name, 'host_override', host_override)
            changed_runtime = True

        if access_mode is not None:
            access_mode = str(access_mode).strip().lower()
            if access_mode not in ('public', 'restricted'):
                return jsonify({'ok': False, 'error': 'Invalid access mode'}), 400
            _set_instance_meta(name, 'access_mode', access_mode)
            changed_runtime = True

        if changed_runtime:
            ok, out, err, rc = _run_manager('recreate', name)
            if not ok:
                return jsonify({'ok': False, 'error': err or out or 'Failed recreating VM with updated settings', 'returncode': rc}), 500

        return api_get_vm_settings(name)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/vm-favicon/<name>')
@app.get('/dashboard/vm-favicon/<name>.ico')
def dashboard_vm_favicon(name):
    # Serve per-VM favicon if exists, otherwise redirect to main favicon (which may itself redirect)
    ddir = os.path.join(_state_dir(), 'dashboard', 'vm-fav')
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', name)
    candidate = os.path.join(ddir, f"{safe}.ico")
    if os.path.isfile(candidate):
        return _send_icon_file(candidate)
    # fallback to global favicon route
    return '', 302, {'Location': '/dashboard/favicon.ico', 'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0', 'Pragma': 'no-cache', 'Expires': '0'}


@app.get('/dashboard/vm/<name>/')
def dashboard_vm_wrapper(name):
        gate = _enforce_vm_user_access(name)
        if gate is not None:
                return gate
        try:
                dash_optimizer.note_vm_activity(name, 'wrapper-open')
        except Exception:
                pass
        # Public wrapper page that opens the VM inside an iframe while setting the tab title and favicon.
        # We render a small client-side React app that will show either the iframe (when VM is running)
        # or a full-screen fallback UI when the VM is stopped/unreachable.
        host_id = str(request.args.get('host_id') or 'local').strip().lower()
        is_remote_wrapper = host_id != 'local'
        if is_remote_wrapper:
                try:
                        remote_host = _vm_host(host_id)
                        _ensure_remote_vm_exists(remote_host, name)
                        remote_status_fn = getattr(remote_host, 'status', None)
                        if not callable(remote_status_fn):
                                raise RuntimeError('selected remote host does not support status')
                        remote_response = remote_status_fn(name)
                        remote_vm = remote_response.get('vm') if isinstance(remote_response, dict) else {}
                        remote_vm = remote_vm if isinstance(remote_vm, dict) else {}
                        remote_state = str(remote_vm.get('state') or remote_vm.get('status') or 'unknown')
                        remote_running = remote_state.lower() in {'running', 'on', 'poweredon', 'started'}
                        initial_status = {
                                'ok': bool(remote_response.get('ok', True)) if isinstance(remote_response, dict) else True,
                                'name': name,
                                'url': '',
                                'exists': bool(remote_vm),
                                'running': remote_running,
                                'healthy': remote_running,
                                'state': remote_state,
                                'status': str(remote_vm.get('status') or remote_state),
                                'detail': str(remote_vm.get('status') or remote_state),
                                'crashed': False,
                                'host_id': host_id,
                                'placement': 'remote',
                                'consoleAvailable': False,
                        }
                except Exception as exc:
                        initial_status = {
                                'ok': False,
                                'name': name,
                                'url': '',
                                'exists': False,
                                'running': False,
                                'healthy': False,
                                'state': 'unavailable',
                                'status': 'RemoteVM unavailable',
                                'detail': str(exc)[:300],
                                'crashed': False,
                                'host_id': host_id,
                                'placement': 'remote',
                                'consoleAvailable': False,
                        }
                url = ''
        else:
                url = _build_vm_embed_url(name) or ''
                initial_status = _vm_status_payload(name)
        cfg = _load_dashboard_settings()
        vm_titles = cfg.get('vm_titles', {}) if isinstance(cfg.get('vm_titles', {}), dict) else {}
        title = vm_titles.get(name) or f"EpicVM - {name}"
        vm_fav_path = os.path.join(_state_dir(), 'dashboard', 'vm-fav', f"{re.sub(r'[^A-Za-z0-9_-]', '_', name)}.ico")
        if os.path.isfile(vm_fav_path):
                fav_url = f'/dashboard/vm-favicon/{name}?v={int(time.time())}'
        else:
                fav_local = os.path.join(_state_dir(), 'dashboard', 'favicon.ico')
                if os.path.isfile(fav_local):
                        fav_url = '/dashboard/favicon.ico'
                else:
                        fav_url = cfg.get('favicon','')

        asset_ver = str(int(time.time()))
        # Safely embed necessary values for the client script
        try:
                js_title = json.dumps(title)
                js_fav = json.dumps(fav_url)
                js_url = json.dumps(url)
                js_name = json.dumps(name)
                js_status = json.dumps(initial_status)
        except Exception:
                js_title = '"%s"' % (title.replace('"','\"'))
                js_fav = '"%s"' % (fav_url.replace('"','\"'))
                js_url = '"%s"' % (url.replace('"','\"'))
                js_name = '"%s"' % (name.replace('"','\"'))
                js_status = '{"ok":true,"status":"unknown","state":"unknown","running":false,"healthy":false,"crashed":false,"exists":false}'

        # The page includes React + Babel via CDN so we can write a compact React component
        # for the fallback UI without changing the project's build pipeline.
        fav_link = ''
        if fav_url:
                fav_link = (
                        f'<link rel="icon" href="{fav_url}" />'
                        f'<link rel="shortcut icon" href="{fav_url}" />'
                        f'<link rel="apple-touch-icon" href="{fav_url}" />'
                )
        tmpl = '''<!doctype html>
    <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>__TITLE__</title>
            __FAV__
            <style>
                :root{color-scheme:dark;--bg:#050816;--bg2:#0b1226;--card:rgba(12,18,38,.72);--line:rgba(255,255,255,.08);--text:#eef4ff;--muted:#9db0d1;--primary:#5ea2ff;--primary2:#7c3aed;--danger:#ff5c7a;--success:#22c55e;--warning:#f59e0b}
                html,body,#root{height:100%;margin:0}
                body{font-family:Inter,system-ui,Arial,sans-serif;background:radial-gradient(circle at top,#101933 0%,#050816 58%,#03050d 100%);color:var(--text);overflow:hidden}
                .vm-iframe{position:fixed;top:-2px;left:-2px;width:calc(100vw + 4px);height:calc(100vh + 4px);display:block;border:none;background:#000;overflow:hidden;scrollbar-width:none;-ms-overflow-style:none}
                .vm-controls-handle{position:fixed;right:18px;bottom:18px;z-index:58;width:46px;height:46px;border-radius:16px;border:1px solid rgba(255,255,255,.12);background:linear-gradient(180deg,rgba(12,18,38,.78),rgba(5,8,22,.82));color:#eef4ff;display:flex;align-items:center;justify-content:center;font-size:21px;font-weight:900;cursor:pointer;box-shadow:0 16px 34px rgba(0,0,0,.28);backdrop-filter:blur(16px);opacity:.68;transition:transform .18s ease, box-shadow .18s ease, opacity .18s ease}
                .vm-controls-handle:hover{transform:translateY(-1px);box-shadow:0 20px 42px rgba(0,0,0,.34);opacity:.92}
                .vm-controls-shell{position:fixed;right:18px;bottom:76px;z-index:60;pointer-events:none}
                .vm-controls-panel{width:min(420px,calc(100vw - 32px));padding:18px;border-radius:22px;border:1px solid rgba(255,255,255,.12);background:linear-gradient(180deg,rgba(10,16,34,.88),rgba(6,10,22,.92));backdrop-filter:blur(24px);box-shadow:0 24px 80px rgba(0,0,0,.42);color:var(--text);pointer-events:auto;transform-origin:top right;transition:opacity .2s ease, transform .2s ease}
                .vm-controls-panel.open{opacity:1;transform:translateY(0) scale(1)}
                .vm-controls-panel.closed{opacity:0;transform:translateY(-10px) scale(.96);pointer-events:none}
                .vm-controls-title{font-size:16px;font-weight:800;margin:0 0 6px}
                .vm-controls-copy{font-size:13px;color:var(--muted);line-height:1.45;margin-bottom:14px}
                .vm-controls-row{display:flex;flex-wrap:wrap;gap:10px}
                .vm-toast{margin-top:12px;padding:10px 12px;border-radius:14px;font-size:13px;line-height:1.4;border:1px solid rgba(255,255,255,.08)}
                .vm-toast.ok{background:rgba(22,101,52,.35);color:#dcfce7}
                .vm-toast.err{background:rgba(127,29,29,.45);color:#ffe4e6}
                .fallback{display:flex;align-items:center;justify-content:center;height:100%;padding:28px;background:radial-gradient(circle at 20% 20%,rgba(94,162,255,.12),transparent 35%),radial-gradient(circle at 80% 0%,rgba(124,58,237,.12),transparent 28%),linear-gradient(180deg,var(--bg2),var(--bg));color:var(--text)}
                .shell{width:min(100%,1040px);position:relative}
                .status-pill{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:rgba(255,255,255,.05);padding:10px 14px;border-radius:999px;font-size:13px;color:#dbeafe;backdrop-filter:blur(16px);margin-bottom:18px;box-shadow:0 12px 30px rgba(0,0,0,.25)}
                .hero-card{position:relative;overflow:hidden;padding:32px;border-radius:28px;border:1px solid var(--line);background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.03));backdrop-filter:blur(22px);box-shadow:0 30px 80px rgba(0,0,0,.42)}
                .hero-content{position:relative;z-index:2}
                .orb{position:absolute;border-radius:50%;filter:blur(14px);opacity:.6}
                .orb-a{width:220px;height:220px;right:-50px;top:-40px;background:radial-gradient(circle,#5ea2ff,transparent 65%)}
                .orb-b{width:260px;height:260px;left:-80px;bottom:-120px;background:radial-gradient(circle,#7c3aed,transparent 65%)}
                .vm-name{font-size:14px;letter-spacing:.24em;text-transform:uppercase;color:#b6c7e6;margin-bottom:10px}
                .hero-title{font-size:clamp(32px,5vw,56px);line-height:1.02;margin:0 0 14px;font-weight:800}
                .hero-subtitle{max-width:760px;font-size:17px;line-height:1.6;color:var(--muted);margin:0 0 18px}
                .meta-row{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 24px}
                .meta-chip{padding:10px 12px;border-radius:999px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.06);font-size:13px;color:#dce8ff}
                .actions{display:flex;flex-wrap:wrap;gap:12px}
                .btn{appearance:none;border:none;border-radius:14px;padding:13px 18px;font-size:15px;font-weight:700;cursor:pointer;transition:transform .18s ease,opacity .18s ease,box-shadow .18s ease}
                .btn:hover{transform:translateY(-1px)}
                .btn-primary{background:linear-gradient(135deg,var(--primary),var(--primary2));color:white;box-shadow:0 18px 40px rgba(94,162,255,.22)}
                .btn-secondary{background:rgba(255,255,255,.08);color:#eef4ff;border:1px solid rgba(255,255,255,.08)}
                .btn-ghost{background:transparent;color:#d8e6ff;border:1px solid rgba(255,255,255,.12)}
                .btn-danger{background:linear-gradient(135deg,#fb7185,var(--danger));color:white;box-shadow:0 18px 40px rgba(255,92,122,.2)}
                .loading-wrap{display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:16px 0}
                .loading-title{font-size:18px;font-weight:700}
                .loading-subtitle{color:var(--muted)}
                .spinner{width:52px;height:52px;border-radius:50%;border:5px solid rgba(255,255,255,.12);border-top-color:var(--primary);animation:spin 1s linear infinite;margin:6px 0}
                .error-box,.sent-box,.details-box{margin-top:18px}
                .error-box,.sent-box{padding:16px 18px;border-radius:18px;border:1px solid rgba(255,255,255,.08)}
                .error-box{background:linear-gradient(180deg,rgba(127,29,29,.55),rgba(60,12,20,.7));color:#ffe4e6}
                .sent-box{background:linear-gradient(180deg,rgba(22,101,52,.45),rgba(8,35,25,.7));color:#dcfce7}
                .error-title{font-weight:800;font-size:16px;margin-bottom:8px}
                .error-message{white-space:pre-wrap;line-height:1.5}
                .subactions{margin-top:14px}
                .details-box{padding:14px 16px;white-space:pre-wrap;overflow:auto;max-height:240px;border-radius:18px;background:rgba(3,8,23,.75);border:1px solid rgba(255,255,255,.07);color:#d9e7ff}
                .tone-crashed .status-pill{border-color:rgba(255,92,122,.25);color:#ffd5de}
                .tone-down .status-pill{border-color:rgba(245,158,11,.25);color:#ffe8ba}
                .tone-live .status-pill{border-color:rgba(34,197,94,.25);color:#dcfce7}
                @keyframes spin{to{transform:rotate(360deg)}}
                .dashboard-style{--surface:#071117;--line-home:#1a2b33}
                .fallback{background:radial-gradient(circle at 10% 0%,rgba(2,189,243,.08),transparent 30%),#02080c;padding:30px;color:#e7f0f4}
                .shell{width:min(100%,1180px);padding:0 0 34px}
                .fallback-topbar{padding:0 0 20px;margin-bottom:24px;border-bottom:1px solid #1a2b33}
                .brand{color:#e7f0f4;font-size:14px;letter-spacing:.01em}.brand-mark{width:32px;height:32px;border-radius:5px;background:#02bdf3;color:#00131b;box-shadow:none}
                .checked-at{color:#788991;font-size:12px}.checked-dot{background:#83eb9b;box-shadow:none}
                .status-pill{border-radius:4px;background:#0b1820;border-color:#263b44;box-shadow:none;padding:7px 10px;margin-bottom:12px;letter-spacing:.05em}
                .hero-card{padding:28px;border-radius:5px;border:1px solid #1a2b33;border-top:2px solid var(--primary);background:#071117;backdrop-filter:none;box-shadow:none}
                .orb{display:none}.vm-name{color:#6e8791;letter-spacing:.18em}.vm-name-value{color:#e7f0f4;font-size:18px}.state-summary{color:#788991}
                .hero-title{font-size:clamp(30px,5vw,48px);font-weight:500;letter-spacing:-.035em}.hero-subtitle{color:#9fb0b8;font-size:15px;line-height:1.55}
                .meta-grid{gap:0;margin:18px 0 24px;border:1px solid #1a2b33}.meta-card{border:0;border-right:1px solid #1a2b33;border-radius:0;background:#08141b;padding:14px}.meta-card:last-child{border-right:0}.meta-card span{color:#6e8791}.meta-card strong{color:#e7f0f4}
                .actions{gap:9px}.btn{border-radius:4px;padding:11px 15px;font-size:14px}.btn-primary{background:#02bdf3;color:#00131b;box-shadow:none}.btn-primary:hover{background:#35cdf6}.btn-secondary{background:#0b1820;color:#dce7ec;border:1px solid #29404a}.btn-ghost{color:#9fb0b8;border:1px solid #29404a}.btn-danger{background:#8d2841;box-shadow:none}
                .meaning-card{border-radius:4px;border-color:rgba(245,158,11,.3);background:#17170f;padding:14px}.meaning-card p{color:#b9b08c}.meaning-note{color:#8f896d}
                .loading-wrap{border-top:1px solid #1a2b33;border-bottom:1px solid #1a2b33;padding:18px 0;margin:8px 0}.spinner{border-top-color:#02bdf3}.loading-subtitle{color:#788991}
                .error-box,.sent-box{border-radius:4px;box-shadow:none}.details-box{border-radius:4px;background:#030a0e;border-color:#1a2b33;color:#b8d2da}
                @media (max-width: 720px){.fallback{padding:20px 16px}.shell{padding-top:0}.fallback-topbar{margin-bottom:20px}.checked-at{font-size:12px}.hero-card{padding:22px;border-radius:24px}.vm-heading-row{display:block}.state-summary{text-align:left;margin-top:7px}.meta-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.actions{flex-direction:column}.btn{width:100%}.hero-subtitle{font-size:15px}.vm-controls-shell{right:12px;left:12px;bottom:68px}.vm-controls-panel{width:auto}.vm-controls-handle{right:12px;bottom:12px;width:46px;height:46px;border-radius:14px}}
                @media (max-width: 720px){.fallback{padding:16px}.hero-card{padding:20px;border-radius:22px}.actions{flex-direction:column}.btn{width:100%}.hero-subtitle{font-size:15px}.vm-controls-shell{right:12px;left:12px;bottom:68px}.vm-controls-panel{width:auto}.vm-controls-handle{right:12px;bottom:12px;width:46px;height:46px;border-radius:14px}}
            </style>
        </head>
        <body>
            <div id="root"></div>
            <iframe id="vmframe" class="vm-iframe" src="about:blank" data-vm-src=__JS_URL__ style="display:none" scrolling="no" sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-pointer-lock allow-popups"></iframe>
            <script>
              window.__VM_WRAPPER_INIT = { vmname: __JS_NAME__, vmurl: __JS_URL__, initialStatus: __JS_STATUS__ };
              window.__VM_WRAPPER_FAVICON = __JS_FAVICON__;
              (function(){
                try {
                  var href = window.__VM_WRAPPER_FAVICON;
                  if(!href) return;
                  var rels = ['icon', 'shortcut icon', 'apple-touch-icon'];
                  rels.forEach(function(rel){
                    var link = document.querySelector('link[rel="' + rel + '"]');
                    if(!link){
                      link = document.createElement('link');
                      link.setAttribute('rel', rel);
                      document.head.appendChild(link);
                    }
                    link.setAttribute('href', href);
                  });
                } catch(e) {}
              })();
            </script>
            <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
            <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
            <script src="https://unpkg.com/babel-standalone@6.26.0/babel.min.js"></script>
            <script>window.__VM_WRAPPER_ASSET_VER = Date.now().toString();</script>
            <script type="text/babel" src="/static/js/api/vms.js?v=__ASSET_VER__"></script>
            <script type="text/babel" src="/static/js/hooks/useVMStatus.js?v=__ASSET_VER__"></script>
            <script type="text/babel" src="/static/js/components/VMFallback.jsx?v=__ASSET_VER__"></script>
            <script type="text/babel" src="/static/js/main_vm_wrapper.jsx?v=__ASSET_VER__"></script>
        </body>
    </html>
    '''
        page = tmpl.replace('__TITLE__', title).replace('__FAV__', fav_link).replace('__JS_URL__', js_url).replace('__JS_NAME__', js_name).replace('__JS_FAVICON__', json.dumps(fav_url)).replace('__JS_STATUS__', js_status).replace('__ASSET_VER__', asset_ver)
        resp = Response(page, mimetype='text/html')
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp


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
    # Docker stats are shared with the VM endpoint and optimizer.
    try:
        for record in get_docker_stats():
            memusage = str(record['mem_usage'])
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
            out['containers'].append({'name': record['name'], 'cpu': record['cpu_percent'],
                                      'memperc': record['mem_percent'], 'memBytes': memBytes})
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
        # Also attempt to start a dev compose service if present so Traefik labels are applied
        dev_compose = os.path.join(dashboard_v2_path, 'docker-compose.dev.yml')
        if os.path.isfile(dev_compose):
            try:
                # ensure proxy network exists
                r = _docker('network', 'inspect', 'proxy')
                if r.returncode != 0:
                    _docker('network', 'create', 'proxy')
                # run docker compose to start the dev service (detached)
                envc = os.environ.copy()
                envc['BLOBEVM_DOMAIN'] = dom
                subprocess.run(['docker', 'compose', '-f', 'docker-compose.dev.yml', 'up', '--build', '-d'], cwd=dashboard_v2_path, check=False, env=envc)
            except Exception as e:
                print(f"Failed to start dashboard_v2 dev compose: {e}")
    def stop_v2_dashboard():
        subprocess.run(['docker', 'rm', '-f', 'blobedash-v2'], check=False)
        # Also try to stop any dev compose service
        try:
            dc = os.path.join(dashboard_v2_path, 'docker-compose.dev.yml')
            if os.path.isfile(dc):
                envc = os.environ.copy()
                envc['BLOBEVM_DOMAIN'] = ''
                subprocess.run(['docker', 'compose', '-f', 'docker-compose.dev.yml', 'down', '-v'], cwd=dashboard_v2_path, check=False, env=envc)
        except Exception:
            pass
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
                        _vm_host().run_manager('start', name, check=False)
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
            _vm_host().run_manager('start', name, check=False)
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
            _vm_host().run_manager('start', name, check=False)
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
    title = cfg.get('title', DASHBOARD_TITLE)

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

    return render_template_string(TEMPLATE, title=title, manager_name=MANAGER_NAME, favicon_url=fav, dashboard_v2_url=dashboard_v2_url)


def manager_json_fleet_list():
    """Return VM inventory from every configured host, omitting unavailable hosts."""
    VM_HOST_REGISTRY.refresh()
    instances = []
    for host_id in VM_HOST_REGISTRY.providers:
        try:
            # Keep the no-argument local call as a compatibility seam for
            # existing overview tests and integrations; remote providers need
            # an explicit host id.
            listed = manager_json_list() if host_id == 'local' else manager_json_list(host_id)
            instances.extend(listed)
        except VmHostUnavailable:
            continue
        except Exception:
            # A single remote host must not make local inventory disappear.
            if host_id == 'local':
                raise
    return instances


@app.get('/dashboard/api/list')
@auth_required
def api_list():
    # Historical callers receive the local manager inventory exactly as
    # before.  The modern placement-aware dashboard opts into fleet mode so
    # legacy action URLs cannot accidentally act on a remote card as local.
    if request.args.get('fleet', '').lower() in {'1', 'true', 'yes'}:
        return jsonify({'instances': manager_json_fleet_list()})
    return jsonify({'instances': manager_json_list()})


@app.get('/dashboard/api/hosts')
@auth_required
def api_hosts():
    """Return redacted local/remote host inventory for dashboard placement UI."""
    try:
        VM_HOST_REGISTRY.refresh()
        hosts = [redact_host_record(record) for record in VM_HOST_REGISTRY.public_records()]
        payload = {'ok': True, 'hosts': hosts}
        if getattr(VM_HOST_REGISTRY, 'config_error', ''):
            payload['warning'] = VM_HOST_REGISTRY.config_error
        return jsonify(payload)
    except RemoteHostConfigError as exc:
        return jsonify({'ok': False, 'hosts': [], 'error': str(exc)}), 500


@app.route('/dashboard/api/hosts/enroll', methods=['POST', 'OPTIONS'], provide_automatic_options=False)
@app.route('/dashboard/api/remote-hosts/enroll', methods=['POST', 'OPTIONS'], provide_automatic_options=False)
@auth_required
def api_remote_host_enroll():
    """Enroll or replace a RemoteVM host from an authenticated dashboard upload."""
    if request.content_length and request.content_length > 32768:
        return jsonify({'ok': False, 'error': 'Enrollment payload is too large'}), 413
    try:
        host_id = str(request.form.get('host_id', '')).strip().lower()
        display_name = str(request.form.get('display_name', '')).strip()
        agent_url = str(request.form.get('agent_url', '')).strip()
        token_upload = request.files.get('token_file')
        if token_upload is not None:
            token_bytes = token_upload.stream.read(4097)
            if len(token_bytes) > 4096:
                return jsonify({'ok': False, 'error': 'Token file is too large'}), 413
            token = token_bytes.decode('utf-8').strip()
        else:
            token = str(request.form.get('token', '')).strip()
        if not token or any(char.isspace() for char in token):
            return jsonify({'ok': False, 'error': 'A single-line agent token file is required'}), 400
        record = upsert_remote_host_config({
            'id': host_id,
            'display_name': display_name,
            'agent_url': agent_url,
            'token': token,
            'provider': 'hyperv',
            'platform': 'windows',
        }, VM_HOST_REGISTRY.path)
        VM_HOST_REGISTRY.refresh(force=True)
        return jsonify({'ok': True, 'host': redact_host_record(record)}), 201
    except UnicodeDecodeError:
        return jsonify({'ok': False, 'error': 'Token file must be UTF-8 text'}), 400
    except RemoteHostConfigError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except OSError:
        return jsonify({'ok': False, 'error': 'Unable to store remote host enrollment'}), 500


@app.get('/dashboard/api/overview')
@auth_required
def api_overview():
    # Progressive loading: `?parts=host` returns only the cheap host/stats/
    # activity block (no VM manager shell-out), so the dashboard can paint
    # immediately and fill in the fleet from a second `?parts=instances` call.
    parts = {p.strip() for p in (request.args.get('parts') or '').split(',') if p.strip()}
    try:
        if not parts or parts == {'all'}:
            return jsonify({'ok': True, **_dashboard_overview_payload()})
        payload = {'ok': True, 'partial': True, 'parts': sorted(parts)}
        if 'host' in parts:
            payload.update(_dashboard_overview_host_payload())
        if 'instances' in parts:
            payload['instances'] = manager_json_fleet_list()
        return jsonify(payload)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/Dashboard/api/<path:subpath>', methods=['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'])
def dashboard_api_case_compat(subpath):
    """Dispatch capitalized public API paths to legacy lower-case routes.

    The public deployment is mounted at /Dashboard, while older routes use
    /dashboard. Keep both spellings functional without duplicating every
    route decorator or changing legacy URLs.
    """
    from werkzeug.exceptions import NotFound
    adapter = app.url_map.bind_to_environ(request.environ)
    try:
        endpoint, values = adapter.match('/dashboard/api/' + subpath, method=request.method)
    except NotFound:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    view = app.view_functions.get(endpoint)
    if view is None:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    return view(**values)


@app.post('/dashboard/api/create')
@auth_required
def api_create():
    payload = request.get_json(silent=True) if request.is_json else None
    payload = payload if isinstance(payload, dict) else request.form.to_dict(flat=True)
    name = str(payload.get('name', '')).strip()
    if not name:
        return jsonify({'ok': False, 'error': 'No name provided'}), 400
    requested_host_id = str(payload.get('host_id') or payload.get('host') or 'local').strip() or 'local'
    requested_placement = str(payload.get('placement') or ('local' if requested_host_id == 'local' else 'remote')).strip().lower()
    if requested_placement not in {'local', 'remote'}:
        return jsonify({'ok': False, 'error': 'placement must be local or remote'}), 400
    if (requested_placement == 'local') != (requested_host_id == 'local'):
        return jsonify({'ok': False, 'error': 'placement and host_id do not agree'}), 400
    try:
        host = _vm_host(requested_host_id)
        if getattr(host, 'kind', 'local') == 'remote':
            host_record = next(
                (item for item in VM_HOST_REGISTRY.public_records() if item.get('id') == requested_host_id),
                None,
            )
            if not host_record or not host_record.get('online'):
                return jsonify({'ok': False, 'error': 'Selected VM host is offline.', 'code': 'host_offline'}), 409
            if not (host_record.get('capabilities') or {}).get('create_vm'):
                return jsonify({'ok': False, 'error': 'Selected VM host cannot create VMs.', 'code': 'capability_unavailable'}), 409
            spec = {
                key: payload[key]
                for key in ('image', 'cpu', 'memory', 'disk', 'profile')
                if payload.get(key) not in (None, '')
            }
            result = host.create(name, spec)
        else:
            result = host.run_manager('create', name, capture_output=True, text=True)
        if result.returncode == 125:
            # Docker exit 125: container name conflict or similar
            msg = result.stderr.strip() or 'VM already exists or container conflict.'
            # Try to start anyway
            host.run_manager('start', name, capture_output=True)
            return jsonify({'ok': False, 'error': msg})
        elif result.returncode != 0:
            return jsonify({'ok': False, 'error': result.stderr.strip() or 'Error creating VM.'}), 500
        # Preserve the local manager's auto-start behavior for remote agents.
        host.run_manager('start', name, capture_output=True)
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except FileNotFoundError:
        return jsonify({'ok': False, 'error': 'blobe-vm-manager not found in container. Make sure it is installed and mounted.'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Error creating VM: {e}'}), 500
    return jsonify({'ok': True, 'host_id': getattr(host, 'host_id', 'local'), 'placement': getattr(host, 'kind', 'local')})


@app.post('/dashboard/api/provisioning-jobs')
@auth_required
def api_provisioning_job_create():
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    payload = payload if isinstance(payload, dict) else {}
    host_id = str(payload.get('host_id') or payload.get('host') or '').strip()
    name = str(payload.get('name') or '').strip()
    profile = str(payload.get('profile') or 'standard').strip().lower()
    if not host_id or not name or profile not in {'standard', 'gaming'}:
        response = jsonify({'ok': False, 'error': 'host_id, name, and a valid profile are required'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 400
    try:
        host = _vm_host(host_id)
        if getattr(host, 'kind', 'local') != 'remote' or not hasattr(host, 'provision'):
            response = jsonify({'ok': False, 'error': 'Provisioning is available only on an enrolled Windows host'})
            response.headers['Cache-Control'] = 'no-store'
            return response, 409
        host_record = host.public_record() if hasattr(host, 'public_record') else {}
        if not (host_record.get('online') and (host_record.get('capabilities') or {}).get('provisioning')):
            response = jsonify({'ok': False, 'error': 'Provisioning prerequisites are not ready on this host', 'code': 'provisioning_unavailable'})
            response.headers['Cache-Control'] = 'no-store'
            return response, 409
        result = host.provision(name, profile, idempotency_key=request.headers.get('Idempotency-Key'))
        response = jsonify({'ok': True, 'host_id': host_id, **result})
        response.headers['Cache-Control'] = 'no-store'
        return response, 202
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception:
        response = jsonify({'ok': False, 'error': 'Unable to start the provisioning job'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 502


@app.get('/dashboard/api/provisioning-jobs/<job_id>')
@auth_required
def api_provisioning_job_status(job_id):
    host_id = str(request.args.get('host_id') or '').strip()
    if not host_id:
        return jsonify({'ok': False, 'error': 'host_id is required'}), 400
    try:
        host = _vm_host(host_id)
        if not hasattr(host, 'provisioning_status'):
            return jsonify({'ok': False, 'error': 'Provisioning is unavailable on this host'}), 409
        response = jsonify({'ok': True, 'host_id': host_id, **host.provisioning_status(job_id)})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception:
        response = jsonify({'ok': False, 'error': 'Unable to read the provisioning job'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 502


@app.post('/dashboard/api/provisioning-jobs/<job_id>/claim')
@auth_required
def api_provisioning_job_claim(job_id):
    if not _request_is_https():
        response = jsonify({'ok': False, 'error': 'Guest claims require HTTPS'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 426
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    payload = payload if isinstance(payload, dict) else {}
    host_id = str(payload.get('host_id') or '').strip()
    username = str(payload.get('username') or '')
    password = str(payload.get('password') or '')
    claim_token = str(payload.get('claimToken') or payload.get('claim_token') or '')
    if not host_id or not username or not password or not claim_token:
        response = jsonify({'ok': False, 'error': 'host_id and claim fields are required'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 400
    try:
        host = _vm_host(host_id)
        if not hasattr(host, 'claim'):
            response = jsonify({'ok': False, 'error': 'Guest claiming is unavailable on this host'})
            response.headers['Cache-Control'] = 'no-store'
            return response, 409
        result = host.claim(job_id, username, password, claim_token)
        job = result.get('job') if isinstance(result, dict) else None
        if not isinstance(job, dict) or job.get('state') != 'awaiting_console':
            raise ConsoleOrchestrationError('The Windows host did not reach the console gate.', status=422, code='console_gate_missing')
        guest_ip = str(job.get('tailnetIp') or '')
        name = str(job.get('name') or '')
        orchestrator = _console_orchestrator()
        plan = orchestrator.build_plan(name=name, guest_ip=guest_ip, username=username, password=password)
        orchestrator.stage_plan(plan)
        started = orchestrator.start_staged(name)
        result = host.console_complete(
            job_id,
            route_prefix=str(started.get('routePrefix') or plan.route_prefix),
            guest_tcp_verified=bool(started.get('guestTcpVerified')),
        )
        response = jsonify({'ok': True, 'host_id': host_id, **result})
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        return response
    except ConsoleOrchestrationError as exc:
        try:
            if 'orchestrator' in locals() and 'name' in locals():
                orchestrator.stop_staged(name)
            if 'host' in locals() and hasattr(host, 'console_failed'):
                host.console_failed(job_id, code=str(getattr(exc, 'code', 'console_failed')))
        except Exception:
            pass
        response = jsonify({'ok': False, 'error': {'code': str(getattr(exc, 'code', 'console_failed')), 'message': str(exc)}})
        response.headers['Cache-Control'] = 'no-store'
        return response, int(getattr(exc, 'status', 502) or 502)
    except VmHostUnavailable as exc:
        response, status = _vm_host_error_response(exc)
        response.headers['Cache-Control'] = 'no-store'
        return response, status
    except Exception:
        # Never reflect exception text from a credential-bearing request.
        try:
            if 'orchestrator' in locals() and 'name' in locals():
                orchestrator.stop_staged(name)
            if 'host' in locals() and hasattr(host, 'console_failed'):
                host.console_failed(job_id, code='console_failed')
        except Exception:
            pass
        response = jsonify({'ok': False, 'error': 'Unable to complete the one-time guest claim'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 502
    finally:
        # Drop local references after the transport call. The request body is
        # never logged or returned, and the agent owns the one-time verifier.
        username = password = claim_token = ''


@app.post('/dashboard/api/provisioning-jobs/<job_id>/retry-console')
@auth_required
def api_provisioning_job_retry_console(job_id):
    if not _request_is_https():
        response = jsonify({'ok': False, 'error': 'Console retry requires HTTPS'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 426
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    payload = payload if isinstance(payload, dict) else {}
    host_id = str(payload.get('host_id') or '').strip()
    username = str(payload.get('username') or '')
    password = str(payload.get('password') or '')
    if not host_id or not username or not password:
        return jsonify({'ok': False, 'error': 'host_id and console credentials are required'}), 400
    try:
        host = _vm_host(host_id)
        current = host.provisioning_status(job_id)
        job = current.get('job') if isinstance(current, dict) else None
        if not isinstance(job, dict) or job.get('state') != 'console_failed':
            raise ConsoleOrchestrationError('Only a failed console step may be retried.', status=409, code='console_retry_not_allowed')
        name = str(job.get('name') or '')
        guest_ip = str(job.get('tailnetIp') or '')
        orchestrator = _console_orchestrator()
        orchestrator.quarantine_staged(name)
        plan = orchestrator.build_plan(name=name, guest_ip=guest_ip, username=username, password=password)
        orchestrator.stage_plan(plan)
        started = orchestrator.start_staged(name)
        result = host.console_complete(job_id, route_prefix=plan.route_prefix, guest_tcp_verified=bool(started.get('guestTcpVerified')))
        response = jsonify({'ok': True, 'host_id': host_id, **result})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ConsoleOrchestrationError as exc:
        try:
            if 'orchestrator' in locals() and 'name' in locals():
                orchestrator.stop_staged(name)
            if 'host' in locals() and hasattr(host, 'console_failed'):
                host.console_failed(job_id, code=str(getattr(exc, 'code', 'console_failed')))
        except Exception:
            pass
        response = jsonify({'ok': False, 'error': {'code': str(getattr(exc, 'code', 'console_failed')), 'message': str(exc)}})
        response.headers['Cache-Control'] = 'no-store'
        return response, int(getattr(exc, 'status', 502) or 502)
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception:
        try:
            if 'orchestrator' in locals() and 'name' in locals():
                orchestrator.stop_staged(name)
            if 'host' in locals() and hasattr(host, 'console_failed'):
                host.console_failed(job_id, code='console_failed')
        except Exception:
            pass
        response = jsonify({'ok': False, 'error': 'Unable to retry console configuration'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 502
    finally:
        username = password = ''


@app.post('/dashboard/api/deprovisioning-jobs')
@auth_required
def api_deprovisioning_job_create():
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=True)
    payload = payload if isinstance(payload, dict) else {}
    host_id = str(payload.get('host_id') or payload.get('host') or '').strip()
    name = str(payload.get('name') or '').strip()
    confirm_name = str(payload.get('confirmName') or payload.get('confirm_name') or '')
    if not host_id or not name or confirm_name != name:
        response = jsonify({'ok': False, 'error': 'Exact VM name confirmation is required'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 400
    try:
        host = _vm_host(host_id)
        if not hasattr(host, 'deprovision'):
            response = jsonify({'ok': False, 'error': 'Teardown is unavailable on this host'})
            response.headers['Cache-Control'] = 'no-store'
            return response, 409
        # Remove the externally reachable route first. The orchestrator only
        # accepts exact EpicVM-owned bundle directories and quarantines rather
        # than deleting their volumes.
        _console_orchestrator().teardown(name=name, confirm_name=confirm_name)
        result = host.deprovision(name, confirm_name=confirm_name, idempotency_key=request.headers.get('Idempotency-Key'))
        response = jsonify({'ok': True, 'host_id': host_id, **result})
        response.headers['Cache-Control'] = 'no-store'
        return response, 202
    except ConsoleOrchestrationError as exc:
        response = jsonify({'ok': False, 'error': {'code': str(getattr(exc, 'code', 'console_teardown_failed')), 'message': str(exc)}})
        response.headers['Cache-Control'] = 'no-store'
        return response, int(getattr(exc, 'status', 502) or 502)
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception:
        response = jsonify({'ok': False, 'error': 'Unable to start the teardown job'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 502


@app.get('/dashboard/api/deprovisioning-jobs/<job_id>')
@auth_required
def api_deprovisioning_job_status(job_id):
    host_id = str(request.args.get('host_id') or '').strip()
    if not host_id:
        return jsonify({'ok': False, 'error': 'host_id is required'}), 400
    try:
        host = _vm_host(host_id)
        if not hasattr(host, 'deprovisioning_status'):
            return jsonify({'ok': False, 'error': 'Teardown is unavailable on this host'}), 409
        response = jsonify({'ok': True, 'host_id': host_id, **host.deprovisioning_status(job_id)})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception:
        response = jsonify({'ok': False, 'error': 'Unable to read the teardown job'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 502

@app.post('/dashboard/api/start/<name>')
@auth_required
def api_start(name):
    force = request.values.get('force') in ('1', 'true', 'yes', 'on')
    try:
        host = _vm_host()
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    is_remote = getattr(host, 'kind', 'local') == 'remote'
    # Safe-start: reject if already running
    if not is_remote:
        try:
            cname = f'blobevm_{name}'
            r = _docker('ps', '-q', '-f', f'name=^{cname}$')
            if r.returncode == 0 and r.stdout.strip():
                return jsonify({'ok': False, 'error': 'VM already running'})
        except Exception:
            pass
        try:
            opt_status = dash_optimizer.status()
            stats = opt_status.get('stats') or {}
            profiles = (stats.get('profiles') or {}) if isinstance(stats, dict) else {}
            profile = profiles.get(name, 'desktop')
            start_ok = dash_optimizer._can_start_vm(opt_status.get('cfg') or {}, stats.get('vmStates') or [], stats.get('hostPressure') or {}, profile=profile, force=force)
            if not start_ok.get('ok'):
                return jsonify({'ok': False, 'error': start_ok.get('reason') or 'Start blocked by optimizer', 'code': start_ok.get('code'), 'optimizer': start_ok}), 409
        except Exception:
            pass
    try:
        _ensure_remote_vm_exists(host, name)
        result = host.run_manager('start', name, capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({'ok': False, 'error': result.stderr.strip() or 'Failed to start VM'}), 500
        try:
            dash_optimizer.note_vm_activity(name, 'api-start')
        except Exception:
            pass
        return jsonify({'ok': True})
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except FileNotFoundError:
        return jsonify({'ok': False, 'error': 'blobe-vm-manager not found'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/vm/<name>/status')
@auth_required
def api_vm_status(name):
    """Return rich state for the selected local or remote VM."""
    try:
        host = _vm_host()
        _ensure_remote_vm_exists(host, name)
        if getattr(host, 'kind', 'local') == 'remote':
            return jsonify({'ok': True, **host.status(name), 'placement': 'remote', 'host_id': host.host_id, 'host_name': host.host_name})
        return jsonify(_vm_status_payload(name))
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/vm/<name>/recover')
@auth_required
def api_vm_recover(name):
    try:
        data = request.get_json(silent=True) or {}
        aggressive = bool(data.get('aggressive', True)) if isinstance(data, dict) else True
        mode = (data.get('mode') if isinstance(data, dict) else None) or 'standard'
        result = _recover_vm(name, source='dashboard', aggressive=aggressive, mode=mode)
        return jsonify(result), (200 if result.get('ok') else 500)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/vm/<name>/escalate')
@auth_required
def api_vm_escalate(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,62}', name or ''):
        return jsonify({'ok': False, 'error': 'Invalid VM name'}), 400
    claim = _claim_hermes_escalation(name)
    if not claim['allowed']:
        return jsonify({
            'ok': False,
            'error': 'Hermes is already handling this VM',
            'retryAfter': claim['retry_after'],
        }), 429
    try:
        data = request.get_json(silent=True) or {}
        reason = data.get('reason') if isinstance(data, dict) else None
        if not reason:
            reason = 'Dashboard recovery help requested by user'
        rec = _recover_vm(name, source='hermes-escalation', aggressive=True)
        esc = _escalate_vm_to_hermes(name, reason, {'recovery': rec, 'request': data})
        return jsonify({'ok': True, 'recovery': rec, 'escalation': esc})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/stop/<name>')
@auth_required
def api_stop(name):
    try:
        host = _vm_host()
        _ensure_remote_vm_exists(host, name)
        host.check_call('stop', name)
        return jsonify({'ok': True})
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500

@app.post('/dashboard/api/delete/<name>')
@auth_required
def api_delete(name):
    try:
        host = _vm_host()
        _ensure_remote_vm_exists(host, name)
        host.check_call('delete', name)
        return jsonify({'ok': True})
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


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


def _read_os_release():
    values = {}
    try:
        with open('/etc/os-release', encoding='utf-8') as handle:
            for line in handle:
                key, separator, value = line.partition('=')
                if separator:
                    values[key.strip()] = value.strip().strip('"')
    except Exception:
        pass
    return values.get('PRETTY_NAME') or values.get('NAME') or 'Unknown OS'


def _kernel_release():
    try:
        return platform.release() or 'Unknown kernel'
    except Exception:
        return 'Unknown kernel'


def _dashboard_overview_host_payload():
    """Host/stats/activity only — cheap, no VM manager subprocess.

    Reads the optimizer's history/last-run files directly instead of calling
    dash_optimizer.status(), which gathers per-VM docker stats (~3s) that the
    overview never uses.
    """
    activity = []
    try:
        history = (dash_optimizer._history_state() or {}).get('events') or []
    except Exception:
        history = []
    try:
        with open(dash_optimizer.LAST_RUN_PATH, 'r') as handle:
            recent = (json.load(handle) or {}).get('events') or []
    except Exception:
        recent = []
    activity = [event for event in [*history, *recent] if isinstance(event, dict)]
    activity.sort(key=lambda event: int(event.get('ts') or 0), reverse=True)
    host_stats = _get_system_stats()
    return {
        'host': {
            'hostname': platform.node() or 'Unknown host',
            'os': _read_os_release(),
            'kernel': _kernel_release(),
            'uptimeSeconds': (host_stats or {}).get('uptime', 0),
        },
        'stats': host_stats,
        'activity': activity[:8],
    }


def _dashboard_overview_payload():
    payload = _dashboard_overview_host_payload()
    payload['instances'] = manager_json_fleet_list()
    return payload


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
    return dashboard_v2_login_public()


@app.get('/dashboard/api/auth/status')
def dashboard_v2_status_alias():
    return dashboard_v2_status_public()


@app.get('/Dashboard/api/vm/logs/<name>')
@v2_auth_required
def dashboard_v2_vm_logs(name):
    # Remote agents own their VM logs; local VMs retain the Docker path.
    try:
        host = _vm_host()
        _ensure_remote_vm_exists(host, name)
        if getattr(host, 'kind', 'local') == 'remote':
            return jsonify({'ok': True, 'logs': host.logs(name), 'placement': 'remote', 'host_id': host.host_id})
    except VmHostUnavailable as exc:
        return jsonify({'ok': False, 'error': str(exc), 'logs': ''}), getattr(exc, 'status', 503)
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
    stats = {}
    try:
        for record in get_docker_stats():
            cname = record['name']
            cpu = record['cpu_percent']
            mem = record['mem_percent']
            # Normalize VM name if container is named blobevm_<name>
            vmname = cname
            if vmname.startswith('blobevm_'):
                vmname = vmname[len('blobevm_'):]
            stats[vmname] = {'cpu_percent': round(cpu,2), 'mem_percent': round(mem,2), 'container_name': cname}
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
        host = _vm_host()
        def worker(vm_name):
            try:
                # Use manager delete which should remove container and instance data
                host.run_manager('delete', vm_name, capture_output=True, text=True)
            except Exception:
                pass
            try:
                # Create a fresh instance and start it
                host.run_manager('create', vm_name, capture_output=True, text=True)
            except Exception:
                pass
            try:
                host.run_manager('start', vm_name, capture_output=True, text=True)
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
        host = _vm_host()
        _ensure_remote_vm_exists(host, name)
        r = host.run_manager('restart', name, capture_output=True, text=True)
        ok = (r.returncode == 0)
        return jsonify({'ok': ok, 'output': r.stdout.strip(), 'error': r.stderr.strip()})
    except VmHostUnavailable as exc:
        return _vm_host_error_response(exc)
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
        result = _vm_host().run_manager('recreate', *names, capture_output=True, text=True)
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
    for n in names:
        _set_flag(n, 'rebuilding', True)
    host = _vm_host()
    def worker(targets):
        try:
            result = host.run_manager('rebuild-vms', *targets, capture_output=True, text=True)
            return result.returncode == 0, (result.stdout or '') + (result.stderr or '')
        finally:
            for n in targets:
                _set_flag(n, 'rebuilding', False)
    job_id = _start_job('rebuild-vms', names, lambda: worker(names))
    return jsonify({'ok': True, 'jobId': job_id}), 202

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
    host = _vm_host()
    def worker(tgts):
        try:
            result = host.run_manager('update-and-rebuild', *names, capture_output=True, text=True)
            return result.returncode == 0, (result.stdout or '') + (result.stderr or '')
        finally:
            for n in tgts:
                _set_flag(n, 'rebuilding', False)
    job_id = _start_job('update-and-rebuild', targets, lambda: worker(targets))
    return jsonify({'ok': True, 'jobId': job_id}), 202

@app.post('/dashboard/api/delete-all-instances')
@auth_required
def api_delete_all_instances():
    data = request.get_json(silent=True) or request.form or {}
    if data.get('confirm') != 'DELETE':
        return jsonify({'ok': False, 'error': 'Type DELETE to confirm deleting every VM'}), 400
    try:
        host = _vm_host()
        def worker():
            result = host.run_manager('delete-all-instances', '--yes', capture_output=True, text=True)
            return result.returncode == 0, (result.stdout or '') + (result.stderr or '')
        job_id = _start_job('delete-all-instances', [], worker)
        return jsonify({'ok': True, 'jobId': job_id}), 202
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/reset-all-instances')
@auth_required
def api_reset_all_instances():
    """Reset all known instances by deleting and recreating them in background."""
    try:
        data = request.get_json(silent=True) or request.form or {}
        if data.get('confirm') != 'DELETE':
            return jsonify({'ok': False, 'error': 'Type DELETE to confirm resetting every VM'}), 400
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

        host = _vm_host()
        def worker(all_names):
            output = []
            ok = True
            for n in all_names:
                for action in ('delete', 'create', 'start'):
                    result = host.run_manager(action, n, capture_output=True, text=True)
                    output.append((result.stdout or '') + (result.stderr or ''))
                    ok = ok and result.returncode == 0
            return ok, '\n'.join(output)

        job_id = _start_job('reset-all-instances', names, lambda: worker(names))
        return jsonify({'ok': True, 'jobId': job_id, 'count': len(names)}), 202
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.post('/dashboard/api/prune-docker')
@auth_required
def api_prune_docker():
    """Prune only Docker resources explicitly owned by EpicVM."""
    def worker():
        results = [
            _docker('container', 'prune', '-f', '--filter', 'label=com.blobevm.managed=1'),
            _docker('volume', 'prune', '-f', '--filter', 'label=com.blobevm.managed=1'),
            _docker('network', 'prune', '-f', '--filter', 'label=com.blobevm.managed=1'),
        ]
        output = '\n'.join((r.stdout or '') + (r.stderr or '') for r in results)
        return all(r.returncode == 0 for r in results), output
    try:
        job_id = _start_job('prune-blobevm-resources', [], worker)
        return jsonify({'ok': True, 'jobId': job_id}), 202
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.get('/dashboard/api/jobs')
@admin_auth_required
def dashboard_jobs_list():
    _init_users_db()
    conn = _users_conn()
    try:
        rows = conn.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100').fetchall()
        return jsonify({'ok': True, 'jobs': [_job_row_to_dict(row) for row in rows]})
    finally:
        conn.close()

@app.get('/dashboard/api/jobs/<job_id>')
@admin_auth_required
def dashboard_job_get(job_id):
    _init_users_db()
    conn = _users_conn()
    try:
        row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404
        return jsonify({'ok': True, 'job': _job_row_to_dict(row)})
    finally:
        conn.close()

@app.post('/dashboard/api/update-vm/<name>')
@auth_required
def api_update_vm(name):
    # Set transient updating flag and run in background to avoid blocking and to show status
    try:
        _set_flag(name, 'updating', True)
        def worker(vm_name):
            try:
                ok, out, err, _ = _run_manager('update-vm', vm_name)
                return ok, out or err
            finally:
                _set_flag(vm_name, 'updating', False)
        job_id = _start_job('update-vm', [name], lambda: worker(name))
        return jsonify({'ok': True, 'jobId': job_id}), 202
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
        _vm_host().run_manager('start', name, capture_output=True)
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
        return jsonify({'ok': True, 'cfg': s.get('cfg'), 'stats': s.get('stats'), 'lastRestart': s.get('lastRestart'), 'lastRun': s.get('lastRun')})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/optimizer/v2/summary')
@auth_required
def api_optimizer_v2_summary():
    try:
        s = dash_optimizer.status()
        stats = s.get('stats') or {}
        return jsonify({
            'ok': True,
            'hostPressure': stats.get('hostPressure') or {},
            'capacity': stats.get('capacity') or {},
            'reliefCandidates': stats.get('reliefCandidates') or [],
            'vmStates': stats.get('vmStates') or [],
            'recommendations': stats.get('recommendations') or [],
            'profiles': stats.get('profiles') or {},
            'densityProfiles': stats.get('densityProfiles') or {},
            'history': stats.get('history') or {},
            'trends': stats.get('trends') or {},
            'cfg': s.get('cfg') or {},
            'lastRun': s.get('lastRun') or {},
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/profile/<name>')
@auth_required
def api_optimizer_profile(name):
    try:
        data = request.get_json(silent=True) or {}
        profile = data.get('profile') if isinstance(data, dict) else None
        if not profile:
            return jsonify({'ok': False, 'error': 'missing profile'}), 400
        chosen = dash_optimizer.set_vm_profile(name, profile)
        return jsonify({'ok': True, 'name': name, 'profile': chosen})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/density-profile')
@auth_required
def api_optimizer_density_profile():
    try:
        data = request.get_json(silent=True) or {}
        profile = data.get('profile') if isinstance(data, dict) else None
        if not profile:
            return jsonify({'ok': False, 'error': 'missing profile'}), 400
        result = dash_optimizer.apply_density_profile(profile)
        return jsonify({'ok': True, 'profile': result.get('profile'), 'cfg': result.get('cfg'), 'profiles': dash_optimizer.available_density_profiles()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/vm/<name>/notifications')
def api_vm_notifications(name):
    gate = _enforce_vm_user_access(name)
    if gate is not None:
        return gate
    clear = request.args.get('clear') in ('1', 'true', 'yes', 'on')
    try:
        items = dash_optimizer.get_vm_notifications(name, clear=clear)
        return jsonify({'ok': True, 'items': items})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/notifications')
@auth_required
def api_notifications():
    clear = request.args.get('clear') in ('1', 'true', 'yes', 'on')
    items = []
    try:
        for instance in manager_json_list():
            name = str(instance.get('name') or '').strip()
            if not name:
                continue
            for item in dash_optimizer.get_vm_notifications(name, clear=clear):
                enriched = dict(item)
                enriched.setdefault('vmName', name)
                items.append(enriched)
        items.sort(key=lambda item: int(item.get('createdAt') or 0), reverse=True)
        return jsonify({'ok': True, 'count': len(items), 'items': items})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/activity/<name>')
@auth_required
def api_optimizer_activity(name):
    try:
        data = request.get_json(silent=True) or {}
        source = (data.get('source') if isinstance(data, dict) else None) or 'dashboard-api'
        dash_optimizer.note_vm_activity(name, source)
        return jsonify({'ok': True, 'name': name, 'source': source})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.get('/dashboard/api/optimizer/admission/<name>')
@auth_required
def api_optimizer_admission(name):
    try:
        force = request.args.get('force') in ('1', 'true', 'yes', 'on')
        s = dash_optimizer.status()
        stats = s.get('stats') or {}
        profiles = stats.get('profiles') or {}
        profile = profiles.get(name, 'desktop')
        result = dash_optimizer._can_start_vm(s.get('cfg') or {}, stats.get('vmStates') or [], stats.get('hostPressure') or {}, profile=profile, force=force)
        return jsonify({'ok': True, 'name': name, 'profile': profile, 'admission': result})
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
        return Response(t or '', mimetype='text/plain')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/dashboard/api/optimizer/clean-system')
@auth_required
def api_optimizer_clean_system():
    """Clean only explicitly labelled EpicVM Docker resources."""
    def worker():
        results = [
            _docker('container', 'prune', '-f', '--filter', 'label=com.blobevm.managed=1'),
            _docker('volume', 'prune', '-f', '--filter', 'label=com.blobevm.managed=1'),
            _docker('network', 'prune', '-f', '--filter', 'label=com.blobevm.managed=1'),
        ]
        output = '\n'.join((r.stdout or '') + (r.stderr or '') for r in results)
        return all(r.returncode == 0 for r in results), output
    try:
        job_id = _start_job('optimizer-clean-blobevm-resources', [], worker)
        return jsonify({'ok': True, 'jobId': job_id}), 202
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    try:
        _init_users_db()
    except Exception:
        pass
    try:
        dash_optimizer.start_background_loop()
    except Exception:
        pass
    app.run(host='0.0.0.0', port=5000)
