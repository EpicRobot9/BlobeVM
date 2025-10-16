#!/usr/bin/env python3
import os, json, subprocess, shlex, base64, socket, threading, time
from functools import wraps
from flask import Flask, jsonify, request, abort, send_from_directory, render_template_string, Response

APP_ROOT = '/opt/blobe-vm'
MANAGER = 'blobe-vm-manager'
HOST_DOCKER_BIN = os.environ.get('HOST_DOCKER_BIN') or '/usr/bin/docker'
CONTAINER_DOCKER_BIN = os.environ.get('CONTAINER_DOCKER_BIN') or '/usr/bin/docker'
DOCKER_VOLUME_BIND = f'{HOST_DOCKER_BIN}:{CONTAINER_DOCKER_BIN}:ro'
TEMPLATE = """
<!doctype html><html><head><title>BlobeVM Dashboard</title>
<style>body{font-family:system-ui,Arial;margin:1.5rem;background:#111;color:#eee}table{border-collapse:collapse;width:100%;}th,td{padding:.5rem;border-bottom:1px solid #333}a,button{background:#2563eb;color:#fff;border:none;padding:.4rem .8rem;border-radius:4px;text-decoration:none;cursor:pointer}form{display:inline}h1{margin-top:0} .badge{background:#444;padding:.15rem .4rem;border-radius:3px;font-size:.65rem;text-transform:uppercase;margin-left:.3rem} .muted{opacity:.75} .btn-red{background:#dc2626} .btn-gray{background:#374151} .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}.green{background:#10b981}.red{background:#ef4444}.gray{background:#6b7280}</style>
</head><body>
<h1>BlobeVM Dashboard</h1>
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

let mergedMode = false, basePath = '/vm', customDomain = '', dashPort = '', dashIp = '';
async function load(){
    try {
        const [r, r2] = await Promise.all([
            fetch('/dashboard/api/list'),
            fetch('/dashboard/api/modeinfo')
        ]);
        if (!r.ok) {
            console.error('[BLOBEDASH] /dashboard/api/list HTTP', r.status);
            return;
        }
        if (!r2.ok) {
            console.error('[BLOBEDASH] /dashboard/api/modeinfo HTTP', r2.status);
            return;
        }
        const data = await r.json().catch(err => { console.error('[BLOBEDASH] list JSON error', err); return {instances:[]}; });
        const info = await r2.json().catch(err => { console.error('[BLOBEDASH] modeinfo JSON error', err); return {}; });
        dbg('modeinfo', info);
        dbg('instances', data.instances);
        mergedMode = !!info.merged;
        basePath = info.basePath||'/vm';
        customDomain = info.domain||'';
        dashPort = info.dashPort||'';
        dashIp = info.ip||'';
        document.getElementById('customdomain').value = customDomain;
        document.getElementById('domainip').textContent = `Point domain to: ${dashIp}`;
        const tb=document.getElementById('tbody');
        tb.innerHTML='';
        (data.instances||[]).forEach(i=>{
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
                // direct: show port and build link using current host
                const m = i.url && i.url.match(/:(\d+)/);
                portOrPath = m ? m[1] : '';
                if (portOrPath) {
                    const proto = window.location.protocol;
                    const host = window.location.hostname;
                    openUrl = `${proto}//${host}:${portOrPath}/`;
                }
            }
            dbg('row', { name: i.name, status: i.status, rawUrl: i.url, mergedMode, portOrPath, openUrl });
            tr.innerHTML=`<td>${i.name}</td><td>${dot}<span class=muted>${i.status||''}</span></td><td>${portOrPath}</td><td><a href="${openUrl}" target=_blank>${openUrl}</a></td>`+
             `<td>`+
             `<button onclick=window.open('${openUrl}','_blank')>Open</button>`+
             `<button onclick=act('start','${i.name}')>Start</button>`+
             `<button onclick=act('stop','${i.name}')>Stop</button>`+
             `<button onclick=delvm('${i.name}') class="btn-red">Delete</button>`+
             `</td>`;
            tb.appendChild(tr);
        });
    } catch (err) {
        console.error('[BLOBEDASH] load() error', err);
    }
}
async function setCustomDomain(){
    const dom = document.getElementById('customdomain').value.trim();
    if(!dom) return alert('Enter a domain.');
    const r = await fetch('/dashboard/api/set-domain', {method:'post',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`domain=${encodeURIComponent(dom)}`});
    const j = await r.json().catch(()=>({}));
    if(j && j.ip) document.getElementById('domainip').textContent = `Point domain to: ${j.ip}`;
    else alert('Saved, but could not resolve IP.');
}
function statusDot(st){
    const s=(st||'').toLowerCase();
    let cls='gray';
    if(s.includes('up')) cls='green';
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
            # In direct mode, override URL with host:published-port to avoid container IPs
            if _is_direct_mode():
                host = _request_host()
                for it in instances:
                    cname = f"blobevm_{it['name']}"
                    hp = _vm_host_port(cname)
                    if hp and host:
                        it['url'] = f"http://{host}:{hp}/"
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
        # In direct mode, compute URL using host published port
        if _is_direct_mode():
            hp = _vm_host_port(cname)
            host = _request_host()
            if hp and host:
                url = f"http://{host}:{hp}/"
            else:
                # Fallback to manager per-VM URL
                try:
                    url = subprocess.check_output([MANAGER, 'url', name], text=True).strip()
                except Exception:
                    url = ''
        else:
            try:
                url = subprocess.check_output([MANAGER, 'url', name], text=True).strip()
            except Exception:
                url = ''
        instances.append({'name': name, 'status': status, 'url': url})
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
    _write_env_kv({'BLOBEVM_DOMAIN': dom})
    # Best-effort IP hint: show the host the user is using to reach the dashboard
    ip = _request_host() or ''
    if not ip:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = ''
    return jsonify({'ok': True, 'domain': dom, 'ip': ip})

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
            'ghcr.io/library/python:3.11-slim',
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
        'ghcr.io/library/python:3.11-slim',
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
    msg = 'Disabling single-port mode; dashboard will run directly on a high port.'
    if dash_port:
        msg = f'Disabling single-port mode; dashboard will move to http://<host>:{dash_port}/dashboard.'
    return jsonify({'ok': True, 'message': msg})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
