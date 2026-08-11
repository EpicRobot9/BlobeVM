import importlib.util
import os
import sys
from types import SimpleNamespace


def load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOBEDASH_STATE", str(tmp_path))
    monkeypatch.delenv("BLOBEVM_ALLOW_INSECURE_DASHBOARD", raising=False)
    path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "app.py")
    monkeypatch.syspath_prepend(os.path.dirname(path))
    spec = importlib.util.spec_from_file_location("provisioning_api_test_app", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_admin_credentials", lambda: ("operator", "dashboard-password"))
    monkeypatch.setattr(module, "_dashboard_secret", lambda: "dashboard-secret")
    monkeypatch.setattr(module, "_verify_v2_token", lambda token: bool(token))
    return module


class FakeRemoteHost:
    kind = "remote"
    host_id = "epic-pc"
    host_name = "Epic PC"

    def public_record(self):
        return {"online": True, "capabilities": {"provisioning": True}}

    def provision(self, name, profile, idempotency_key=None):
        return {"job": {"id": "job-1", "name": name, "profile": profile, "state": "awaiting_claim"}, "claimToken": "one-use"}

    def provisioning_status(self, job_id):
        return {"job": {"id": job_id, "state": "awaiting_claim"}}

    def claim(self, job_id, username, password, claim_token):
        return {"job": {"id": job_id, "name": "alpha", "state": "awaiting_console", "tailnetIp": "100.111.82.1"}}

    def console_complete(self, job_id, route_prefix, guest_tcp_verified):
        return {"job": {"id": job_id, "name": "alpha", "state": "ready", "consoleRoutePrefix": route_prefix}}

    def console_failed(self, job_id, code="console_failed"):
        return {"job": {"id": job_id, "name": "alpha", "state": "console_failed", "errorCode": code}}

    def deprovision(self, name, confirm_name, idempotency_key=None):
        return {"job": {"id": "tear-1", "name": name, "state": "ready"}}

    def deprovisioning_status(self, job_id):
        return {"job": {"id": job_id, "state": "ready"}}


def attach_host(module):
    host = FakeRemoteHost()

    class Registry:
        def refresh(self):
            return None

        def get(self, host_id="local"):
            if host_id != host.host_id:
                raise module.VmHostUnavailable("missing", status=404, code="not_found")
            return host

    module.VM_HOST_REGISTRY = Registry()

    class Console:
        automatic = False
        def build_plan(self, name, guest_ip, username, password):
            return SimpleNamespace(name=name, route_prefix=f"/vm/{name}/")
        def stage_plan(self, plan):
            return None
        def start_staged(self, name):
            return {"ok": True, "routePrefix": f"/vm/{name}/", "guestTcpVerified": True}
        def stop_staged(self, name):
            return None
        def quarantine_staged(self, name):
            return None
        def teardown(self, **kwargs):
            return {"ok": True}
        def has_auto_login(self, name):
            return self.automatic
        def enable_auto_login(self, name, username, password):
            assert username == 'operator'
            assert password == 'transient-password'
            self.automatic = True
        def build_json_auth_data(self, name):
            assert self.automatic
            return 'encrypted-data'
    module._CONSOLE_ORCHESTRATOR = Console()


def authenticated_client(module):
    client = module.app.test_client()
    client.set_cookie("Dashboard-Auth", "session")
    return client


def test_mutating_provisioning_api_requires_session_csrf(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    attach_host(module)
    client = authenticated_client(module)
    no_csrf = client.post(
        "/dashboard/api/provisioning-jobs",
        json={"host_id": "epic-pc", "name": "alpha", "profile": "standard"},
        headers={"Origin": "http://localhost"},
    )
    assert no_csrf.status_code == 403

    token_response = client.get("/dashboard/api/auth/csrf")
    assert token_response.status_code == 200
    csrf = token_response.get_json()["csrfToken"]
    response = client.post(
        "/dashboard/api/provisioning-jobs",
        json={"host_id": "epic-pc", "name": "alpha", "profile": "standard"},
        headers={"Origin": "http://localhost", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["claimToken"] == "one-use"


def test_provisioning_fails_closed_until_host_prerequisites_are_ready(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)

    class NotReadyHost(FakeRemoteHost):
        def public_record(self):
            return {"online": True, "capabilities": {"provisioning": False}}

    class Registry:
        def refresh(self):
            return None

        def get(self, host_id="local"):
            return NotReadyHost()

    module.VM_HOST_REGISTRY = Registry()
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    response = client.post(
        "/dashboard/api/provisioning-jobs",
        json={"host_id": "epic-pc", "name": "alpha", "profile": "standard"},
        headers={"Origin": "http://localhost", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "provisioning_unavailable"


def test_claim_is_https_only_and_never_reflects_password(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    attach_host(module)
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    payload = {"host_id": "epic-pc", "username": "operator", "password": "transient", "claimToken": "one-use"}
    http_response = client.post(
        "/dashboard/api/provisioning-jobs/job-1/claim",
        json=payload,
        headers={"Origin": "http://localhost", "X-CSRF-Token": csrf},
    )
    assert http_response.status_code == 426
    assert "transient" not in http_response.get_data(as_text=True)

    https_response = client.post(
        "/dashboard/api/provisioning-jobs/job-1/claim",
        json=payload,
        headers={"Origin": "http://localhost", "X-Forwarded-Proto": "https", "X-CSRF-Token": csrf},
    )
    assert https_response.status_code == 200
    assert https_response.headers["Cache-Control"] == "no-store"
    assert "transient" not in https_response.get_data(as_text=True)


def test_remote_409_is_preserved(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)

    class ConflictHost(FakeRemoteHost):
        def provision(self, *args, **kwargs):
            raise module.VmHostUnavailable("duplicate", status=409, code="conflict")

    class Registry:
        def refresh(self):
            return None

        def get(self, host_id="local"):
            return ConflictHost()

    module.VM_HOST_REGISTRY = Registry()
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    response = client.post(
        "/dashboard/api/provisioning-jobs",
        json={"host_id": "epic-pc", "name": "alpha", "profile": "standard"},
        headers={"Origin": "http://localhost", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 409


def test_console_retry_requires_failed_state_and_reentered_credentials(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    attach_host(module)

    class RetryHost(FakeRemoteHost):
        def provisioning_status(self, job_id):
            return {"job": {"id": job_id, "name": "alpha", "state": "console_failed", "tailnetIp": "100.111.82.1"}}

    module.VM_HOST_REGISTRY.get = lambda host_id="local": RetryHost()
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    response = client.post(
        "/dashboard/api/provisioning-jobs/job-1/retry-console",
        json={"host_id": "epic-pc", "username": "operator", "password": "transient-password"},
        headers={"Origin": "http://localhost", "X-Forwarded-Proto": "https", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.get_json()["job"]["state"] == "ready"
    assert "transient-password" not in response.get_data(as_text=True)


def test_admin_can_enable_and_launch_automatic_console_without_password_reflection(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    attach_host(module)
    client = authenticated_client(module)
    csrf = client.get('/dashboard/api/auth/csrf').get_json()['csrfToken']
    setup = client.post(
        '/dashboard/api/console-credentials/alpha',
        json={'username':'operator','password':'transient-password'},
        headers={'Origin':'http://localhost','X-Forwarded-Proto':'https','X-CSRF-Token':csrf},
    )
    assert setup.status_code == 200
    assert 'transient-password' not in setup.get_data(as_text=True)
    launch = client.get('/dashboard/console/alpha/')
    assert launch.status_code == 200
    body = launch.get_data(as_text=True)
    assert 'localStorage.removeItem("GUAC_AUTH_TOKEN")' in body
    assert 'sessionStorage.removeItem("GUAC_AUTH_TOKEN")' in body
    assert 'new URLSearchParams({data:"encrypted-data"})' in body
    assert '/vm/alpha/api/tokens' in body
    assert 'localStorage.setItem("GUAC_AUTH_TOKEN",result.authToken)' in body
    assert '/vm/alpha/?data=' not in body
    assert launch.headers['Cache-Control'] == 'no-store'
