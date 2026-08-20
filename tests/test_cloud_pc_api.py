import importlib.util
import json
import sys
from pathlib import Path

import pytest

APP_PATH = Path("/opt/blobe-vm/repo/dashboard/app.py")
if not APP_PATH.is_file():
    APP_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"

# Load the cloud_pc module so we can point its registry at a temp path in tests.
_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))
_cp_spec = importlib.util.spec_from_file_location("cloud_pc_test_mod", str(_DASHBOARD_DIR / "cloud_pc.py"))
cp = importlib.util.module_from_spec(_cp_spec)
_cp_spec.loader.exec_module(cp)


def load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOBEDASH_STATE", str(tmp_path))
    monkeypatch.setenv("DASH_V2_SECRET", "test-secret")
    # Point the Cloud PC registry at a temp file so tests stay isolated.
    monkeypatch.setenv("EPICVM_CLOUD_PCS_FILE", str(tmp_path / "cloud-pcs.json"))
    monkeypatch.delenv("BLOBEVM_ALLOW_INSECURE_DASHBOARD", raising=False)
    spec = importlib.util.spec_from_file_location("cloudpc_api_test_app", str(APP_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_admin_credentials", lambda: ("operator", "dashboard-password"))
    monkeypatch.setattr(module, "_dashboard_secret", lambda: "dashboard-secret")
    monkeypatch.setattr(module, "_verify_v2_token", lambda token: bool(token))
    # Monkeypatch the cloud_pc orchestrator so Docker is never touched.
    module._cp_start = lambda name: None
    module._cp_stop = lambda name: None
    module._load_cloud_pc = cp.load_cloud_pc
    module._load_cloud_pcs = cp.load_cloud_pcs
    module._is_cloudpc = cp.is_cloudpc
    module._cp_status = lambda name, tailnet_ip: {"name": name, "readiness": "stopped", "running": False, "paired": False, "healthy": False}
    module._cp_moonlight_proxy_up = lambda name: False
    return module


# --- Fake portal auth ---------------------------------------------------------------

class FakeUser:
    def __init__(self, username="epic"):
        self.username = username
        self.assignedVms = []


@pytest.fixture
def portal_client(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    user = {"username": "epic", "assignedVms": []}
    monkeypatch.setattr(module, "_current_portal_user", lambda: user)
    monkeypatch.setattr(module, "_verify_portal_token", lambda token: user if token else None)
    client = module.app.test_client()
    client.set_cookie("Portal-Auth", "valid-portal-session")
    return client


def _headers():
    return {"Origin": "http://localhost"}


def test_create_cloudpc_requires_auth(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_current_portal_user", lambda: None)
    monkeypatch.setattr(module, "_verify_portal_token", lambda token: None)
    client = module.app.test_client()
    resp = client.post("/portal/api/cloudpc", json={"displayName": "Rig", "tailnet_ip": "100.72.10.5"}, headers=_headers())
    assert resp.status_code == 401


def test_create_cloudpc_rejects_public_ip(portal_client):
    resp = portal_client.post("/portal/api/cloudpc", json={"displayName": "Rig", "tailnet_ip": "8.8.8.8"}, headers=_headers())
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_create_cloudpc_rejects_missing_fields(portal_client):
    assert portal_client.post("/portal/api/cloudpc", json={"tailnet_ip": "100.72.10.5"}, headers=_headers()).status_code == 400
    assert portal_client.post("/portal/api/cloudpc", json={"displayName": "Rig"}, headers=_headers()).status_code == 400


def test_create_cloudpc_ok_and_listed(portal_client):
    resp = portal_client.post("/portal/api/cloudpc", json={"displayName": "My Rig", "tailnet_ip": "100.72.10.5"}, headers=_headers())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    cid = body["cloudpc"]["id"]
    assert cid.startswith("cloudpc-epic-")

    vms = portal_client.get("/portal/api/vms").get_json()
    names = [v["name"] for v in vms["vms"]]
    assert cid in names
    entry = next(v for v in vms["vms"] if v["name"] == cid)
    assert entry["type"] == "cloudpc"
    assert entry["owner"] == "epic"
    assert entry["accessMode"] == "restricted"


def test_cloudpc_owner_scoping(portal_client, monkeypatch, tmp_path):
    # A second user (bob) must NOT see epic's cloud PC.
    resp = portal_client.post("/portal/api/cloudpc", json={"displayName": "My Rig", "tailnet_ip": "100.72.10.5"}, headers=_headers())
    cid = resp.get_json()["cloudpc"]["id"]

    module = load_app(monkeypatch, tmp_path)
    bob = {"username": "bob", "assignedVms": []}
    monkeypatch.setattr(module, "_current_portal_user", lambda: bob)
    monkeypatch.setattr(module, "_verify_portal_token", lambda token: bob if token else None)
    # Share the same registry file so bob sees the same records.
    monkeypatch.setenv("EPICVM_CLOUD_PCS_FILE", str(tmp_path / "cloud-pcs.json"))
    client = module.app.test_client()
    client.set_cookie("Portal-Auth", "bob-session")
    vms = client.get("/portal/api/vms").get_json()
    names = [v["name"] for v in vms["vms"]]
    assert cid not in names
    # bob cannot start/stop epic's cloud PC
    h = _headers()
    assert client.post(f"/portal/api/cloudpc/{cid}/start", headers=h).status_code == 403
    assert client.post(f"/portal/api/cloudpc/{cid}/stop", headers=h).status_code == 403
    assert client.delete(f"/portal/api/cloudpc/{cid}", headers=h).status_code == 403


def test_cloudpc_start_stop_forward_auth_unpaired(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    user = {"username": "epic", "assignedVms": []}
    monkeypatch.setattr(module, "_current_portal_user", lambda: user)
    monkeypatch.setattr(module, "_verify_portal_token", lambda token: user if token else None)
    client = module.app.test_client()
    client.set_cookie("Portal-Auth", "valid-portal-session")

    cid = client.post("/portal/api/cloudpc", json={"displayName": "Rig", "tailnet_ip": "100.72.10.5"}, headers=_headers()).get_json()["cloudpc"]["id"]
    # start returns ok (proxy start is mocked)
    assert client.post(f"/portal/api/cloudpc/{cid}/start", headers=_headers()).status_code == 200
    # forward-auth to the stream wrapper must 503 while unpaired
    module._is_cloudpc = cp.is_cloudpc
    module._load_cloud_pc = cp.load_cloud_pc
    resp = client.get(f"/dashboard/auth/vm/{cid}")
    assert resp.status_code == 503
    assert "not paired" in resp.get_data(as_text=True).lower()


def test_forward_auth_unknown_cloudpc_is_404_redirect(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    user = {"username": "epic", "assignedVms": []}
    monkeypatch.setattr(module, "_current_portal_user", lambda: user)
    monkeypatch.setattr(module, "_verify_portal_token", lambda token: user if token else None)
    client = module.app.test_client()
    client.set_cookie("Portal-Auth", "valid-portal-session")
    # Non-existent cloud pc name should fall through to normal VM gate (not crash).
    resp = client.get("/dashboard/auth/vm/cloudpc-nonexistent-zzz")
    assert resp.status_code in (200, 302)
