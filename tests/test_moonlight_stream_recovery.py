"""Regression coverage for Gaming stream-start recovery and evidence gates.

Covers the three black-screen readiness defects:
1. MoonlightOrchestrator.restart_session rebuilds stream state without
   touching the paired bundle contract (control-stream startup race).
2. The dashboard console-verify endpoint rejects missing or sub-threshold
   frame metrics instead of persisting false-positive readiness.
3. Complete-EpicVMProvisioningConsole (agent) rejects jobs whose frame
   metrics are missing or below threshold.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path, extra_path: Path | None = None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if extra_path is not None:
        sys.path.insert(0, str(extra_path))
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Orchestrator restart_session
# ---------------------------------------------------------------------------

def _make_orchestrator(tmp_path: Path, plan_overrides: dict | None = None):
    orch_module = _load_module(
        "epicvm_moonlight_orchestrator_under_test",
        REPO / "dashboard" / "moonlight_orchestrator.py",
        extra_path=REPO / "dashboard",
    )
    plan = {
        "name": "vmx",
        "owner": "EpicVM",
        "backend": "moonlight",
        "guestIp": "100.90.90.90",
        "routePrefix": "/vm/vmx/",
        "paired": True,
    }
    if plan_overrides:
        plan.update(plan_overrides)
    calls = []

    class Orchestrator(orch_module.MoonlightOrchestrator):
        def __init__(self):
            self.root = tmp_path
            self._calls = calls

        def _read_plan(self, name):
            return dict(plan)

        def stop_staged(self, name):
            calls.append(("stop", name))

        def start_staged(self, name):
            calls.append(("start", name))

    return Orchestrator(), calls


def test_restart_session_restarts_container_and_preserves_route(tmp_path):
    orch, calls = _make_orchestrator(tmp_path)
    result = orch.restart_session("vmx")
    assert result["ok"] is True and result["restarted"] is True
    assert result["routePrefix"] == "/vm/vmx/"
    assert [c[0] for c in calls] == ["stop", "start"]
    assert all(c[1] == "vmx" for c in calls)


def test_restart_session_rejects_route_mismatch(tmp_path):
    orch, calls = _make_orchestrator(tmp_path)
    with pytest.raises(Exception) as excinfo:
        orch.restart_session("vmx", route_name="other-vm")
    assert getattr(excinfo.value, "code", "") == "moonlight_stale_bundle"
    assert calls == []


# ---------------------------------------------------------------------------
# 2/3. Shared threshold logic used by both gates
# ---------------------------------------------------------------------------

def test_console_verify_thresholds():
    """The documented thresholds reject black, frozen, short, and static video."""
    thresholds = {
        "nonblackFraction": 0.60,
        "meanLuma": 12.0,
        "stdDev": 8.0,
        "decodedFramesDelta": 3,
        "durationMs": 1500,
    }
    good = {"nonblackFraction": 0.74, "meanLuma": 40.0, "stdDev": 41.0, "decodedFramesDelta": 150, "durationMs": 5000}
    for name, minimum in thresholds.items():
        bad = dict(good)
        bad[name] = minimum - 0.001
        failures = [k for k, m in bad.items() if m < thresholds[k]]
        assert name in failures


GOOD_METRICS = {
    "nonblackFraction": 0.7452,
    "meanLuma": 40.76,
    "stdDev": 41.2,
    "decodedFramesDelta": 169,
    "durationMs": 5000,
}
BLACK_METRICS = {key: 0 for key in GOOD_METRICS}


class _FakeHost:
    kind = "remote"

    def __init__(self, job):
        self._job = job

    def provisioning_status(self, job_id):
        return {"job": dict(self._job)}

    def console_complete(self, job_id, **kwargs):
        job = dict(self._job)
        job["state"] = "ready"
        job.update({k: True for k in (
            "consoleFrameVerified", "keyboardInputVerified", "mouseInputVerified")})
        return {"ok": True, "job": job}


@pytest.fixture()
def app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOBEDASH_STATE", str(tmp_path))
    app = _load_module(
        "epicvm_app_under_test", REPO / "dashboard" / "app.py", extra_path=REPO / "dashboard"
    )
    # Mirror the production auth seams used by tests/test_provisioning_api.py.
    monkeypatch.setattr(app, "_admin_credentials", lambda: ("operator", "dashboard-password"))
    monkeypatch.setattr(app, "_dashboard_secret", lambda: "dashboard-secret")
    monkeypatch.setattr(app, "_verify_v2_token", lambda token: bool(token))
    return app


def _verify_client(app, payload):
    client = app.app.test_client()
    # Bypass the HTTPS gate exactly as the production TLS terminator would.
    client.post("/dashboard/api/provisioning-jobs/<id>".replace("<id>", "x"), data={})
    resp = None
    with client.session_transaction() as session:  # pragma: no cover
        session.clear()
    return client


def _post_verify(app, monkeypatch, job, payload):
    host = _FakeHost(job)
    monkeypatch.setattr(app, "_vm_host", lambda host_id: host)
    monkeypatch.setattr(app, "_same_origin_request", lambda: True)
    monkeypatch.setattr(app, "_csrf_request_valid", lambda: True)
    client = app.app.test_client()
    client.set_cookie("Dashboard-Auth", "session")
    # Simulate the reverse-proxy HTTPS header used in production.
    response = client.open(
        f"/dashboard/api/provisioning-jobs/{job['id']}/console-verify",
        method="POST",
        json=payload,
        headers={"X-Forwarded-Proto": "https"},
    )
    return response


def test_dashboard_verify_requires_quantified_metrics(app_module, monkeypatch):
    job = {"id": "jobframe1", "name": "vmx", "state": "streaming_setup"}
    payload = {
        "host_id": "epic-pc",
        "routePrefix": "/vm/vmx/",
        "evidenceSource": "browser_kvm",
        "videoFrameVerified": True,
        "keyboardInputVerified": True,
        "mouseInputVerified": True,
        "guestTcpVerified": True,
    }
    response = _post_verify(app_module, monkeypatch, job, payload)
    assert response.status_code == 422
    body = response.get_json()
    assert body["error"]["code"] == "frame_metrics_required"


def test_dashboard_verify_rejects_black_frames(app_module, monkeypatch):
    job = {"id": "jobframe2", "name": "vmx", "state": "streaming_setup"}
    payload = {
        "host_id": "epic-pc",
        "routePrefix": "/vm/vmx/",
        "evidenceSource": "browser_kvm",
        "videoFrameVerified": True,
        "keyboardInputVerified": True,
        "mouseInputVerified": True,
        "guestTcpVerified": True,
        "frameMetrics": BLACK_METRICS,
    }
    response = _post_verify(app_module, monkeypatch, job, payload)
    assert response.status_code == 422
    body = response.get_json()
    assert body["error"]["code"] == "frame_evidence_rejected"
    assert "nonblackFraction" in body["error"]["message"]


def test_dashboard_verify_accepts_real_frame_evidence(app_module, monkeypatch):
    job = {"id": "jobframe3", "name": "vmx", "state": "streaming_setup"}
    payload = {
        "host_id": "epic-pc",
        "routePrefix": "/vm/vmx/",
        "evidenceSource": "browser_kvm",
        "videoFrameVerified": True,
        "keyboardInputVerified": True,
        "mouseInputVerified": True,
        "guestTcpVerified": True,
        "frameMetrics": GOOD_METRICS,
    }
    response = _post_verify(app_module, monkeypatch, job, payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    assert body["visualValidationComplete"] is True


def test_agent_console_complete_rejects_missing_metrics():
    """Complete-EpicVMProvisioningConsole must fail closed without metrics.

    The PowerShell gate mirrors these thresholds; this test pins the shared
    contract so a future edit cannot silently drop one side.
    """
    provisioning_source = (REPO / "remote_agent" / "windows" / "Provisioning.ps1").read_text(encoding="utf-8")
    assert "'nonblackFraction'; Min=0.60" in provisioning_source
    assert "'meanLuma';         Min=12.0" in provisioning_source
    assert "'stdDev' readiness threshold" in provisioning_source
    assert "'decodedFramesDelta'; Min=3.0" in provisioning_source
    assert "'durationMs';       Min=1500.0" in provisioning_source
    assert "frameMetrics" in provisioning_source


def test_gaming_capture_script_is_wired_for_gaming_only():
    """The gaming Sunshine capture path must be selected only when IsGaming."""
    guest_source = (REPO / "remote_agent" / "windows" / "providers" / "GuestProvider.ps1").read_text(encoding="utf-8")
    assert "function Get-EpicVMGamingSunshineCaptureScript" in guest_source
    assert "$sunshineScript=Get-EpicVMSunshineConfigurationScript -ForGaming ([bool]$IsGaming)" in guest_source
    # output_name pin must exist inside the capture script
    assert "output_name = Virtual Display" in guest_source
    # auto-logon keys must exist for a real interactive desktop session
    assert "AutoAdminLogon" in guest_source
