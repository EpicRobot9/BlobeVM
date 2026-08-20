# Cloud PC (BYO Sunshine) Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Let an approved EpicVM user add their own PC (already running Sunshine, joined to the same Tailscale tailnet as the dashboard server) as a managed "Cloud PC" — no EpicVM agent install required — and stream to it via Moonlight Web, exactly like a managed VM.

**Architecture:** A new `cloud_pc.py` module holds a hardened JSON registry (`/opt/blobe-vm/cloud-pcs.json`, mode `0600`) of personal PC records. Each Cloud PC reuses the existing `MoonlightOrchestrator` to stand up a per-PC Moonlight Web proxy on the dashboard host and pair it to the user's Sunshine at `<tailnet_ip>:47989/47990`. The stream is served by the existing `/vm/<name>/` wrapper + `/dashboard/auth/vm/<name>` forward-auth. Portal VM listing, start/stop, and the SPA card are extended to treat Cloud PCs as a first-class `type: 'cloudpc'` with the same orange/zine styling. "Start/Stop" toggles only the Moonlight proxy container — it never powers the user's physical PC on/off.

**Tech Stack:** Python/Flask (`dashboard/app.py`), `dashboard/moonlight_orchestrator.py` (reused), `dashboard/remote_hosts.py` patterns (reused for validation/registry), React SPA (`epicvm_web/src`), Tailscale (transport), Sunshine + Moonlight Web (streaming).

---

## Key integration facts (verified by reading the code)

- `MoonlightOrchestrator` (`dashboard/moonlight_orchestrator.py`) already does per-instance pairing to a `guest_ip` via `build_plan(name, guest_ip=...)`, `stage_plan`, `start_staged`, `pair_staged(name, sunshine_username=, sunshine_password=)`, `stop_staged`, `has_auto_login`. It validates `guest_ip` against `TAILSCALE_IP_RE` (`100.64–127.x`) — your PC's tailnet IP satisfies this.
- `dashboard_vm_forward_auth(name)` at `app.py:3573` is the Traefik forward-auth target for the Moonlight proxy. For remote VMs it calls `_reconcile_remote_console`; we add a Cloud-PC branch before it.
- `/portal/api/vms` (`app.py:3863`) lists Docker VMs via `manager_json_list()`. We extend it to also surface Cloud PCs owned/assigned to the caller.
- `/portal/api/start/<name>` (`app.py:3938`) and `/portal/api/stop/<name>` (`app.py:3953`) drive Docker VMs via `manager`. We make them Cloud-PC-aware.
- The SPA `Portal.jsx` renders cards by `type` (linux/windows/gaming) and a CONNECT button → `vm.url` (the `/vm/<name>/` wrapper). `api.js` `startVm/stopVm` hit `/portal/api/start|stop/<name>`.
- Existing portal POST endpoints (request-access, approve) use `@portal_auth_required` and the global CSRF guard at `app.py:280`; Cloud PC endpoints mirror that exact pattern.

**Security model (Tailscale, per user's choice):**
- Dashboard server must already be on the same tailnet (it already reaches guest VMs at `100.x`). Your PC at `100.x.x.x` is reachable the same way.
- Sunshine creds are used for one pairing call and never stored on disk (matches `pair_staged` behavior). The Moonlight proxy is the only thing persisted.
- Cloud PC registry is `0600`, same as `remote-hosts.json`.
- A Cloud PC is only visible/connectable to its owner (and admins). Access is owner-scoped, not via `assignedVms`.

---

## Task 1: Cloud PC registry module

**Objective:** Create `dashboard/cloud_pc.py` with a validated, persisted registry of Cloud PC records.

**Files:**
- Create: `dashboard/cloud_pc.py`
- Test: `tests/test_cloud_pc.py`

**Step 1: Write failing test**

```python
# tests/test_cloud_pc.py
import importlib.util, os, tempfile, json
spec = importlib.util.spec_from_file_location("cloud_pc", "dashboard/cloud_pc.py")
cp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cp)

def test_validate_tailnet_ip_ok():
    assert cp.validate_tailnet_ip("100.72.10.5") == "100.72.10.5"

def test_validate_tailnet_ip_rejects_public():
    try:
        cp.validate_tailnet_ip("8.8.8.8"); assert False, "should reject"
    except cp.CloudPcConfigError:
        pass

def test_upsert_and_load(tmp_path):
    path = tmp_path / "cloud-pcs.json"
    rec = cp.upsert_cloud_pc({"id":"cloudpc-epic-a1","display_name":"My Rig","owner":"epic","tailnet_ip":"100.72.10.5"}, path=str(path))
    assert rec["id"] == "cloudpc-epic-a1"
    loaded = cp.load_cloud_pcs(path=str(path))
    assert loaded[0]["owner"] == "epic"
    # file must be 0600
    assert oct(os.stat(path).st_mode & 0o777) == oct(0o600)
```

**Step 2: Run to verify failure**

Run: `cd /opt/blobe-vm/repo && python3 -m pytest tests/test_cloud_pc.py -v`
Expected: FAIL (module missing)

**Step 3: Write minimal implementation**

```python
# dashboard/cloud_pc.py
"""Personal Cloud PC registry (BYO Sunshine over Tailscale)."""
from __future__ import annotations
import ipaddress, json, os, re, stat, tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_PATH = "/opt/blobe-vm/cloud-pcs.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")

class CloudPcConfigError(ValueError):
    pass

def cloud_pcs_path(path=None):
    return Path(path or os.environ.get("EPICVM_CLOUD_PCS_FILE") or DEFAULT_PATH)

def validate_tailnet_ip(value):
    s = str(value or "").strip()
    try:
        addr = ipaddress.ip_address(s)
    except ValueError:
        raise CloudPcConfigError("tailnet_ip must be a valid IP")
    if addr not in TAILSCALE_NET:
        raise CloudPcConfigError("tailnet_ip must be a Tailscale (100.64.0.0/10) address")
    return s

def _normalize(raw, *, persisted=False):
    rid = str(raw.get("id") or "").strip().lower()
    if not ID_RE.fullmatch(rid):
        raise CloudPcConfigError("id must match [a-z0-9][a-z0-9._-]{0,62}")
    owner = str(raw.get("owner") or "").strip()
    if not owner:
        raise CloudPcConfigError("owner is required")
    rec = {
        "id": rid,
        "display_name": str(raw.get("display_name") or rid)[:120],
        "owner": owner,
        "tailnet_ip": validate_tailnet_ip(raw.get("tailnet_ip")),
        "paired": bool(raw.get("paired", False)),
        "enabled": raw.get("enabled", True) is not False,
        "created_at": int(raw.get("created_at", 0) or 0),
    }
    return rec

def load_cloud_pcs(path=None):
    p = cloud_pcs_path(path)
    if not p.exists():
        return []
    try:
        if os.name != "nt" and stat.S_IMODE(p.stat().st_mode) & 0o077:
            raise CloudPcConfigError("cloud-pc registry must not be group/world readable")
        data = json.loads(p.read_text(encoding="utf-8"))
    except CloudPcConfigError:
        raise
    except Exception as exc:
        raise CloudPcConfigError(f"cannot read cloud-pc registry: {exc}")
    raw = data if isinstance(data, list) else data.get("pcs", [])
    return [_normalize(r, persisted=True) for r in raw if isinstance(r, Mapping)]

def upsert_cloud_pc(raw, path=None):
    rec = _normalize(raw)
    existing = load_cloud_pcs(path)
    updated = [r for r in existing if r["id"] != rec["id"]]
    updated.append(rec)
    _write(updated, path)
    return dict(rec)

def _write(pcs, path=None):
    p = cloud_pcs_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    import secrets
    fd, tmp = tempfile.mkstemp(prefix=f".cloud-pcs.", dir=str(p.parent), text=True)
    try:
        if hasattr(os, "fchmod"): os.fchmod(fd, 0o600)
        else: os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            json.dump(pcs, h, indent=2, sort_keys=True); h.write("\n"); h.flush(); os.fsync(h.fileno())
        os.replace(tmp, p); os.chmod(p, 0o600)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
```

**Step 4: Run test to verify pass**

Run: `cd /opt/blobe-vm/repo && python3 -m pytest tests/test_cloud_pc.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add dashboard/cloud_pc.py tests/test_cloud_pc.py
git commit -m "feat: Cloud PC registry module (validated, 0600 JSON)"
```

---

## Task 2: Cloud PC manager (proxy + pairing via MoonlightOrchestrator)

**Objective:** Add functions that create/start/stop/pair/status a Cloud PC by reusing `MoonlightOrchestrator`.

**Files:**
- Modify: `dashboard/cloud_pc.py`
- Test: `tests/test_cloud_pc.py` (append)

**Step 1: Write failing test**

```python
def test_cloudpc_name_unique():
    n = cp.generate_cloudpc_id("epic")
    assert n.startswith("cloudpc-epic-") and cp.ID_RE.fullmatch(n)

def test_status_offline_when_no_proxy(monkeypatch):
    # No docker; ensure status degrades to offline without raising.
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: _fake_run())
    st = cp.cloudpc_status("cloudpc-epic-a1", tailnet_ip="100.72.10.5")
    assert st["readiness"] in ("stopped", "offline", "provisioning")
```

**Step 2: Run to verify failure**

Run: `cd /opt/blobe-vm/repo && python3 -m pytest tests/test_cloud_pc.py -v`
Expected: FAIL (new functions missing)

**Step 3: Write minimal implementation** (append to `dashboard/cloud_pc.py`)

```python
import secrets, subprocess, time
from .moonlight_orchestrator import MoonlightOrchestrator, ConsoleOrchestrationError

def generate_cloudpc_id(owner):
    return f"cloudpc-{str(owner).lower()}-{secrets.token_hex(3)}"

def _orchestrator():
    return MoonlightOrchestrator()  # uses env EPICVM_* routing (same as VM consoles)

def create_cloudpc(*, owner, display_name, tailnet_ip, sunshine_username="", sunshine_password=""):
    tid = generate_cloudpc_id(owner)
    orch = _orchestrator()
    try:
        plan = orch.build_plan(name=tid, guest_ip=tailnet_ip)
        orch.stage_plan(plan)
    except ConsoleOrchestrationError as exc:
        raise CloudPcConfigError(f"could not stage proxy: {exc.code}")
    rec = upsert_cloud_pc({
        "id": tid, "display_name": display_name, "owner": owner,
        "tailnet_ip": tailnet_ip, "paired": False, "created_at": int(time.time()),
    })
    if sunshine_username and sunshine_password:
        pair_cloudpc(tid, sunshine_username=sunshine_username, sunshine_password=sunshine_password)
        rec = load_cloud_pcs_inline(tid)
    return rec

def load_cloud_pcs_inline(tid):
    for r in load_cloud_pcs():
        if r["id"] == tid:
            return r
    return None

def pair_cloudpc(tid, *, sunshine_username, sunshine_password):
    orch = _orchestrator()
    orch.start_staged(tid)
    orch.pair_staged(tid, sunshine_username=sunshine_username, sunshine_password=sunshine_password)
    _set_paired(tid, True)

def _set_paired(tid, value):
    pcs = load_cloud_pcs()
    for r in pcs:
        if r["id"] == tid:
            r["paired"] = bool(value)
    _write(pcs)

def start_cloudpc(tid):
    _orchestrator().start_staged(tid)

def stop_cloudpc(tid):
    _orchestrator().stop_staged(tid)

def cloudpc_status(tid, *, tailnet_ip):
    orch = _orchestrator()
    proxy_up = _proxy_running(orch, tid)
    sunshine_up = _tcp(tailnet_ip, 47989, 2.0) and _tcp(tailnet_ip, 47990, 2.0)
    rec = load_cloud_pcs_inline(tid) or {}
    paired = bool(rec.get("paired"))
    if not proxy_up:
        readiness = "stopped"
    elif not sunshine_up:
        readiness = "offline"
    elif not paired:
        readiness = "provisioning"
    else:
        readiness = "ready"
    return {"name": tid, "readiness": readiness, "proxyUp": proxy_up,
            "sunshineUp": sunshine_up, "paired": paired}

def _proxy_running(orch, tid):
    try:
        return orch._runtime_isolated(tid)
    except Exception:
        return False

def _tcp(host, port, timeout):
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True
    except OSError:
        return False
```

**Step 4: Run test to verify pass**

Run: `cd /opt/blobe-vm/repo && python3 -m pytest tests/test_cloud_pc.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add dashboard/cloud_pc.py tests/test_cloud_pc.py
git commit -m "feat: Cloud PC manager reuses MoonlightOrchestrator for proxy+pairing"
```

---

## Task 3: Portal API endpoints (create / list / pair / start / stop)

**Objective:** Expose Cloud PC operations to the approved user via the existing portal auth pattern.

**Files:**
- Modify: `dashboard/app.py` (import + endpoints)
- Test: `tests/test_cloud_pc_api.py`

**Step 1: Write failing test**

```python
# tests/test_cloud_pc_api.py  (uses the app test client; mirror existing tests)
def test_create_cloudpc_requires_auth(client):
    r = client.post("/portal/api/cloudpc", json={"display_name":"Rig","tailnet_ip":"100.72.10.5"})
    assert r.status_code in (401, 403)

def test_create_cloudpc_rejects_public_ip(client, auth_headers):
    r = client.post("/portal/api/cloudpc", headers=auth_headers,
                    json={"display_name":"Rig","tailnet_ip":"8.8.8.8"})
    assert r.status_code == 400
```

**Step 2: Run to verify failure**

Run: `cd /opt/blobe-vm/repo && python3 -m pytest tests/test_cloud_pc_api.py -v`
Expected: FAIL

**Step 3: Write minimal implementation** (in `dashboard/app.py`)

Add near the other portal endpoints:

```python
from .cloud_pc import (cloud_pc as _cp_module)  # or import functions directly
```

```python
@app.post('/portal/api/cloudpc')
@portal_auth_required
def portal_cloudpc_create():
    user = request.portal_user
    data = request.get_json(silent=True) or {}
    display_name = str(data.get('displayName') or '').strip()
    tailnet_ip = str(data.get('tailnet_ip') or '').strip()
    su = str(data.get('sunshineUsername') or '')
    sp = str(data.get('sunshinePassword') or '')
    if not display_name or not tailnet_ip:
        return jsonify({'ok': False, 'error': 'displayName and tailnet_ip required'}), 400
    try:
        rec = _cp_module.create_cloudpc(owner=user['username'], display_name=display_name,
                                         tailnet_ip=tailnet_ip, sunshine_username=su, sunshine_password=sp)
    except _cp_module.CloudPcConfigError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True, 'cloudpc': rec})

@app.post('/portal/api/cloudpc/<name>/pair')
@portal_auth_required
def portal_cloudpc_pair(name):
    user = request.portal_user
    rec = _cp_module.load_cloud_pcs_inline(name)
    if not rec or rec['owner'] != user['username']:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    data = request.get_json(silent=True) or {}
    su = str(data.get('sunshineUsername') or ''); sp = str(data.get('sunshinePassword') or '')
    try:
        _cp_module.pair_cloudpc(name, sunshine_username=su, sunshine_password=sp)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True})

@app.post('/portal/api/cloudpc/<name>/start')
@portal_auth_required
def portal_cloudpc_start(name):
    rec = _cp_module.load_cloud_pcs_inline(name)
    if not rec or rec['owner'] != request.portal_user['username']:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    try: _cp_module.start_cloudpc(name)
    except Exception as exc: return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True})

@app.post('/portal/api/cloudpc/<name>/stop')
@portal_auth_required
def portal_cloudpc_stop(name):
    rec = _cp_module.load_cloud_pcs_inline(name)
    if not rec or rec['owner'] != request.portal_user['username']:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    _cp_module.stop_cloudpc(name)
    return jsonify({'ok': True})
```

Also extend `/portal/api/vms` (around `app.py:3872`) so Cloud PCs the user owns are appended:

```python
# after building `vms` from Docker VMs:
for pc in _cp_module.load_cloud_pcs():
    if pc['owner'] != user['username']:
        continue
    st = _cp_module.cloudpc_status(pc['id'], tailnet_ip=pc['tailnet_ip'])
    vms.append({
        'name': pc['id'], 'url': _build_vm_url(pc['id']),
        'wrapperUrl': f'/vm/{pc["id"]}/', 'accessMode': 'restricted',
        'allowed': True, 'type': 'cloudpc', 'os': 'Your PC',
        'profile': 'cloudpc', 'status': st['readiness'], 'state': st['readiness'],
        'running': st['readiness'] == 'ready', 'healthy': st['readiness'] == 'ready',
        'crashed': False, 'exists': True, 'recoveryState': 'healthy',
        'readiness': st['readiness'], 'title': pc['display_name'],
        'cpu': '', 'memory': '',
    })
```

And make `/portal/api/start/<name>` + `/portal/api/stop/<name>` Cloud-PC-aware by checking `_cp_module.load_cloud_pcs_inline(name)` first and routing to `_cp_module.start_cloudpc/stop_cloudpc`.

**Step 4: Run test to verify pass**

Run: `cd /opt/blobe-vm/repo && python3 -m pytest tests/test_cloud_pc_api.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add dashboard/app.py tests/test_cloud_pc_api.py
git commit -m "feat: Cloud PC portal endpoints (create/list/pair/start/stop)"
```

---

## Task 4: Forward-auth + stream routing for Cloud PCs

**Objective:** Make `/dashboard/auth/vm/<name>` serve Cloud PC streams and ensure the proxy is up + paired.

**Files:**
- Modify: `dashboard/app.py` `dashboard_vm_forward_auth` (`app.py:3573`)

**Step 1: Add Cloud PC branch** — at the top of `dashboard_vm_forward_auth`, before the remote-host branch:

```python
pc = _cp_module.load_cloud_pcs_inline(name)
if pc is not None:
    if pc['owner'] != user['username'] and not _admin_vm_sso_authenticated():
        return Response('', 302, {'Location': _render_vm_denied_url(name)})
    try:
        _cp_module.start_cloudpc(name) if not _cp_module._proxy_running(_cp_module._orchestrator(), name) else None
    except Exception:
        pass
    rec = _cp_module.load_cloud_pcs_inline(name)
    if not rec or not rec.get('paired'):
        response = Response('Cloud PC is not paired yet. Open the portal and pair your PC.', status=503)
        response.headers['Retry-After'] = '5'
        response.headers['X-EpicVM-Console-Code'] = 'console_not_paired'
        return response
    return Response('OK', 200)
```

(Extraction of a `_render_vm_denied_url` helper mirrors existing `_render_vm_denied`.)

**Step 2: Verify** with a local probe (start the app test client, confirm a paired Cloud PC returns 200 and an unpaired one 503). Add a test in `tests/test_cloud_pc_api.py`.

**Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: Cloud PC forward-auth + stream routing"
```

---

## Task 5: SPA — add Cloud PC card + "Connect your PC" modal

**Objective:** Surface Cloud PCs in the portal with the same styling and let users add/pair their PC.

**Files:**
- Modify: `epicvm_web/src/pages/Portal.jsx` (TYPE_META + card + modal)
- Modify: `epicvm_web/src/api.js` (add `addCloudPc`, `pairCloudPc` and route start/stop)

**Step 1: Add API helpers** to `api.js`:

```js
export async function addCloudPc({ displayName, tailnetIp, sunshineUsername, sunshinePassword }) {
  return apiFetch(`${PORTAL}/api/cloudpc`, { method: 'POST',
    body: JSON.stringify({ displayName, tailnetIp, sunshineUsername, sunshinePassword }) })
}
export async function pairCloudPc(name, { sunshineUsername, sunshinePassword }) {
  return apiFetch(`${PORTAL}/api/cloudpc/${encodeURIComponent(name)}/pair`, { method: 'POST',
    body: JSON.stringify({ sunshineUsername, sunshinePassword }) })
}
```

**Step 2: Extend `TYPE_META`** in `Portal.jsx`:

```js
cloudpc: { label: 'Cloud PC', icon: Desktop, cls: 't-cloudpc', tag: 'YOUR PC' },
```

Add a "Connect your PC" CTA card at the end of the grid that opens a modal (displayName, tailnetIp, optional Sunshine creds). On submit call `addCloudPc`, then `load()`. For a `provisioning`/`not paired` cloud PC, the manage panel gets a "PAIR" button calling `pairCloudPc`.

**Step 3: Add CSS** (`epicvm_web/src/index.css`) for `.t-cloudpc` (orange/cyan accent) — mirror `.t-gaming`/`.t-linux`.

**Step 4: Build + deploy**

```bash
cd /opt/blobe-vm/repo/epicvm_web && npm run build && cp -r dist/* /opt/blobe-vm/dashboard/static/  # or wherever the live SPA is served
```

(Confirm the live SPA serve path before copying — it was `epicvm_web/dist` previously.)

**Step 5: Commit**

```bash
git add epicvm_web/src/pages/Portal.jsx epicvm_web/src/api.js epicvm_web/src/index.css
git commit -m "feat: Cloud PC portal UI (add/pair card, orange/zine styling)"
```

---

## Task 6: Docs + end-to-end verification

**Objective:** Document the user-facing flow and verify live with the `nyxietest` account (no agent install).

**Files:**
- Modify: `docs/REMOTE_HOSTS.md` (add "Connect your own PC" section) or new `docs/CLOUD_PC.md`
- Live verify with `nyxietest` (approved test account)

**Step 1: Add docs** describing: join tailnet, ensure Sunshine running + reachable at `100.x:47989/47990`, open Portal → "Connect your PC" → enter display name + tailnet IP (+ Sunshine creds to auto-pair) → CONNECT.

**Step 2: Live e2e (no agent):** as `nyxietest`, POST `/portal/api/cloudpc` with a valid tailnet IP; confirm proxy stages; confirm `/portal/api/vms` lists it as `type:cloudpc`; confirm start/stop toggle the proxy; confirm unpaired returns 503 at the wrapper. (Full stream pairing requires a real Sunshine box on the tailnet — verify the pairing *path* with a reachable test Sunshine or document the manual step.)

**Step 3: Commit + push**

```bash
git add docs/ && git commit -m "docs: Cloud PC (BYO Sunshine) user guide" && git push origin production
```

---

## Verification summary (all must pass before "done")
- `pytest tests/test_cloud_pc.py tests/test_cloud_pc_api.py` → green
- `python3 -m py_compile dashboard/app.py dashboard/cloud_pc.py` → clean
- Live: `nyxietest` creates a Cloud PC, it appears in `/portal/api/vms` as `type:cloudpc`, start/stop toggles the Moonlight proxy, unpaired wrapper returns 503.
- SPA builds and the new card/modal render in the orange/zine style.
- Committed and pushed to `production`.
