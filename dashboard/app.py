#!/usr/bin/env python3
import os, json, subprocess, shlex, base64
from functools import wraps
from flask import Flask, jsonify, request, abort, send_from_directory, render_template_string, Response

APP_ROOT = '/opt/blobe-vm'
MANAGER = 'blobe-vm-manager'
TEMPLATE = """
<!doctype html><html><head><title>BlobeVM Dashboard</title>
<style>body{font-family:system-ui,Arial;margin:1.5rem;background:#111;color:#eee}table{border-collapse:collapse;width:100%;}th,td{padding:.5rem;border-bottom:1px solid #333}a,button{background:#2563eb;color:#fff;border:none;padding:.4rem .8rem;border-radius:4px;text-decoration:none;cursor:pointer}form{display:inline}h1{margin-top:0} .badge{background:#444;padding:.15rem .4rem;border-radius:3px;font-size:.65rem;text-transform:uppercase;margin-left:.3rem}</style>
</head><body>
<h1>BlobeVM Dashboard</h1>
<form method=post action="/dashboard/api/create" onsubmit="return createVM(event)">
<input name=name placeholder="name" required pattern="[a-zA-Z0-9-]+" />
<button type=submit>Create</button>
</form>
<table><thead><tr><th>Name</th><th>Status</th><th>URL</th><th>Actions</th></tr></thead><tbody id=tbody></tbody></table>
<script>
async function load(){
  const r=await fetch('/dashboard/api/list');
  const data=await r.json();
  const tb=document.getElementById('tbody');
  tb.innerHTML='';
  data.instances.forEach(i=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${i.name}</td><td>${i.status||''}</td><td><a href="${i.url}" target=_blank>${i.url}</a></td>`+
     `<td>`+
     `<button onclick=act('start','${i.name}')>Start</button>`+
     `<button onclick=act('stop','${i.name}')>Stop</button>`+
     `<button onclick=delvm('${i.name}') style="background:#dc2626">Delete</button>`+
     `</td>`;
    tb.appendChild(tr);
  });
}
async function act(cmd,name){await fetch(`/dashboard/api/${cmd}/${name}`,{method:'post'});load();}
async function delvm(name){if(!confirm('Delete '+name+'?'))return;await fetch(`/dashboard/api/delete/${name}`,{method:'post'});load();}
async function createVM(e){e.preventDefault();const fd=new FormData(e.target);await fetch('/dashboard/api/create',{method:'post',body:new URLSearchParams(fd)});e.target.reset();load();}
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

def manager_json_list():
    # Parse list output heuristically
    out = subprocess.check_output([MANAGER, 'list'], text=True)
    lines = [l[2:] for l in out.splitlines() if l.startswith('- ')]
    instances = []
    for l in lines:
        try:
            # - name -> status -> URL
            parts = [p.strip() for p in l.split('->')]
            name = parts[0].split()[0]
            status = parts[1] if len(parts) > 1 else ''
            url = parts[2] if len(parts) > 2 else ''
            instances.append({'name': name, 'status': status, 'url': url})
        except Exception:
            pass
    return instances

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
        abort(400)
    subprocess.check_call([MANAGER, 'create', name])
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
