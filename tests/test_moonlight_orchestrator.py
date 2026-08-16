import json
import pathlib
from types import SimpleNamespace

import pytest

from dashboard.moonlight_orchestrator import MoonlightOrchestrator
import dashboard.moonlight_orchestrator as moonlight_module
from dashboard.guacamole_orchestrator import ConsoleOrchestrationError


IMAGE = "mrcreativ3001/moonlight-web-stream@sha256:" + "a" * 64


def make_orchestrator(root, **overrides):
    options = {
        "root": str(root),
        "digests": {"moonlight": IMAGE},
        "tcp_probe": lambda *_: True,
        "disk_probe": lambda: True,
        "route_owner_probe": lambda *_: True,
        "routing_probe": lambda: True,
        "auth_status_probe": lambda *_: True,
        "public_host": "techexplore.us",
        "tls_resolver": "myresolver",
        "auth_middleware": "epicvm-portal-auth@file",
        "router_priority": 600,
    }
    options.update(overrides)
    return MoonlightOrchestrator(**options)


def test_plan_is_digest_pinned_path_correct_and_does_not_contain_credentials(tmp_path):
    orch = make_orchestrator(tmp_path)
    plan = orch.build_plan(name="alpha", guest_ip="100.111.82.1")
    assert IMAGE in plan.compose
    assert "Host(`techexplore.us`) && PathPrefix(`/vm/alpha/`)" in plan.compose
    assert "middlewares: \"epicvm-alpha-portal-auth,epicvm-alpha-portal-user\"" in plan.compose
    assert "middlewares.epicvm-alpha-portal-auth.forwardauth.address: \"http://blobedash:5000/dashboard/auth/vm/alpha\"" in plan.compose
    assert "middlewares.epicvm-alpha-portal-auth.forwardauth.trustForwardHeader: \"true\"" in plan.compose
    assert "middlewares.epicvm-alpha-portal-user.forwardauth" not in plan.compose
    assert "epicvm-portal-auth@file" not in plan.compose
    assert "url_path_prefix\":\"/vm/alpha\"" in plan.config
    assert "ports:" not in plan.compose
    assert "healthcheck:" in plan.compose
    assert 'test: ["CMD-SHELL", "kill -0 1"]' in plan.compose
    assert "curl -fsS" not in plan.compose
    assert "internal: true" not in plan.compose
    assert "sunshine" not in plan.compose.lower()
    assert "operator" not in plan.compose
    assert "transient-password" not in plan.compose + plan.config + plan.data


def test_staging_writes_only_safe_owned_metadata(tmp_path):
    orch = make_orchestrator(tmp_path)
    target = orch.stage_plan(orch.build_plan(name="alpha", guest_ip="100.111.82.1"))
    assert target == tmp_path / "alpha"
    assert (target / "server" / "config.json").is_file()
    assert (target / "server" / "data.json").is_file()
    plan = json.loads((target / "plan.json").read_text())
    assert plan == {"owner": "EpicVM", "version": 1, "backend": "moonlight", "name": "alpha", "guestIp": "100.111.82.1", "routePrefix": "/vm/alpha/", "paired": False}
    assert list(target.rglob("*"))


def test_stage_plan_chowns_paths_after_atomic_rename(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(moonlight_module.os, "chown", lambda path, uid, gid: calls.append(pathlib.Path(path)), raising=False)
    orch = make_orchestrator(tmp_path)
    target = orch.stage_plan(orch.build_plan(name="alpha", guest_ip="100.111.82.1"))
    assert target / "server" in calls
    assert target / "server" / "config.json" in calls
    assert target / "server" / "data.json" in calls
    assert not any(path.name.startswith(".alpha-") for path in calls)


def test_internal_api_url_includes_configured_vm_prefix(tmp_path):
    orch = make_orchestrator(tmp_path)

    def inspect(args, **_kwargs):
        if args[1:3] == ["ps", "--filter"]:
            return SimpleNamespace(stdout="container-id")
        return SimpleNamespace(stdout=json.dumps([{"NetworkSettings": {"Networks": {"proxy": {"IPAddress": "172.20.0.2"}}}}]))

    orch.command_runner = inspect
    assert orch._container_url("alpha") == "http://172.20.0.2:8080/vm/alpha"


def test_pairing_keeps_sunshine_secret_out_of_bundle(tmp_path):
    calls = []

    class Response:
        def __init__(self, lines=(), payload=b""):
            self.lines = [line if isinstance(line, bytes) else str(line).encode() for line in lines]
            self.payload = payload

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

        def read(self, *_args):
            if self.payload:
                return self.payload
            return b"\n".join(self.lines)

        def close(self):
            return None

    def http(method, url, *, headers, body, timeout):
        calls.append((method, url, headers, body))
        if url.endswith("/api/hosts"):
            return Response(payload=(json.dumps({"host_id": "other-host", "paired": "Paired"}) + "\n").encode())
        if url.endswith("/api/host"):
            return Response(payload=json.dumps({"host": {"host_id": "1251941260"}}).encode())
        if url.endswith("/api/pair"):
            return Response([json.dumps({"Pin": "1234"}), json.dumps({"Paired": "Paired"})])
        if url.endswith("/api/pin"):
            return Response(payload=b'{"status":true}')
        raise AssertionError(url)

    orch = make_orchestrator(tmp_path, http_request=http)
    orch.stage_plan(orch.build_plan(name="alpha", guest_ip="100.111.82.1"))
    orch._container_url = lambda _name: "http://172.20.0.2:8080/vm/alpha"
    result = orch.pair_staged("alpha", sunshine_username="sunshine-user", sunshine_password="secret-value")
    assert result["paired"] is True
    pair = next(call for call in calls if call[1].endswith("/api/pair"))
    pair_payload = json.loads(pair[3].decode())
    assert isinstance(pair_payload["host_id"], int)
    assert pair_payload["host_id"] == 1251941260
    contents = "".join(path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file())
    assert "secret-value" not in contents
    assert "sunshine-user" not in contents
    sunshine = next(call for call in calls if call[1].endswith("/api/pin"))
    assert "secret-value" not in sunshine[3].decode()
    assert sunshine[2]["Authorization"].startswith("Basic ")
    assert json.loads((tmp_path / "alpha" / "plan.json").read_text())["paired"] is True


def test_sunshine_pair_retries_when_sunshine_reports_pending_session(tmp_path):
    calls = []
    pin_responses = [
        {"status": False},
        {"status": True},
    ]

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode("utf-8")

        def read(self, *_args):
            return self.payload

        def close(self):
            return None

    def http(method, url, *, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        if url.endswith("/api/hosts"):
            return Response({"hosts": []})
        if url.endswith("/api/host"):
            return Response({"host": {"host_id": "1251941260"}})
        if url.endswith("/api/pair"):
            return Response({"Pin": "1234"})
        if url.endswith("/api/pin"):
            return Response(pin_responses.pop(0))
        raise AssertionError(url)

    orch = make_orchestrator(tmp_path, http_request=http)
    orch.stage_plan(orch.build_plan(name="alpha", guest_ip="100.111.82.1"))
    orch._sunshine_pair("100.111.82.1", "sunshine-user", "secret-value", "1234", "alpha")
    pin_calls = [call for call in calls if call[1].endswith("/api/pin")]
    assert len(pin_calls) == 2


def test_invalid_guest_and_missing_digest_fail_closed(tmp_path):
    with pytest.raises(ConsoleOrchestrationError) as guest:
        make_orchestrator(tmp_path).build_plan(name="alpha", guest_ip="192.168.1.3")
    assert guest.value.code == "invalid_guest_ip"
    with pytest.raises(ConsoleOrchestrationError) as digest:
        make_orchestrator(tmp_path, digests={"moonlight": ""}).build_compose(name="alpha")
    assert digest.value.code == "digest_required"
