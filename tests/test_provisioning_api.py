import importlib.util
import os
import sys
import threading
import time
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
        return {"job": {"id": "job-1", "name": name, "profile": profile, "state": "unclaimed"}, "claimToken": "one-use"}

    def provisioning_status(self, job_id):
        return {"job": {"id": job_id, "state": "unclaimed"}}

    def claim(self, job_id, username, password, claim_token):
        return {"job": {"id": job_id, "name": "alpha", "state": "streaming_setup", "tailnetIp": "100.111.82.1"}}

    def console_complete(self, job_id, route_prefix, guest_tcp_verified):
        return {"job": {"id": job_id, "name": "alpha", "state": "ready", "consoleRoutePrefix": route_prefix}}

    def console_failed(self, job_id, code="console_failed"):
        return {"job": {"id": job_id, "name": "alpha", "state": "setup_failed:streaming", "errorCode": code}}

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


def test_dashboard_api_unauthorized_does_not_emit_browser_basic_challenge(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    response = module.app.test_client().get("/dashboard/api/auth/csrf")
    assert response.status_code == 401
    assert "WWW-Authenticate" not in response.headers


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
            return {"job": {"id": job_id, "name": "alpha", "state": "setup_failed:streaming", "tailnetIp": "100.111.82.1"}}

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


def test_console_retry_conflict_is_read_only(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    attach_host(module)

    class InProgressHost(FakeRemoteHost):
        def __init__(self):
            self.failed_codes = []

        def provisioning_status(self, job_id):
            return {"job": {"id": job_id, "name": "alpha", "state": "streaming_setup"}}

        def console_failed(self, job_id, code="console_failed"):
            self.failed_codes.append(code)
            return super().console_failed(job_id, code)

    host = InProgressHost()
    module.VM_HOST_REGISTRY.get = lambda host_id="local": host
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    response = client.post(
        "/dashboard/api/provisioning-jobs/job-1/retry-console",
        json={"host_id": "epic-pc", "username": "operator", "password": "transient-password"},
        headers={"Origin": "http://localhost", "X-Forwarded-Proto": "https", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "console_retry_not_allowed"
    assert host.failed_codes == []


def test_console_retry_ready_is_idempotent(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    attach_host(module)

    class ReadyHost(FakeRemoteHost):
        def provisioning_status(self, job_id):
            return {"job": {"id": job_id, "name": "alpha", "state": "ready", "consoleRoutePrefix": "/vm/alpha/"}}

    host = ReadyHost()
    module.VM_HOST_REGISTRY.get = lambda host_id="local": host
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    response = client.post(
        "/dashboard/api/provisioning-jobs/job-1/retry-console",
        json={"host_id": "epic-pc", "username": "operator", "password": "transient-password"},
        headers={"Origin": "http://localhost", "X-Forwarded-Proto": "https", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.get_json()["job"]["state"] == "ready"


def test_remote_console_worker_rechecks_ready_state_before_credentials(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)

    class ReadyHost(FakeRemoteHost):
        def provisioning_status(self, job_id):
            return {"job": {"id": job_id, "name": "alpha", "state": "ready", "consoleRoutePrefix": "/vm/alpha/"}}

        def console_credentials(self, *args, **kwargs):
            raise AssertionError("credentials must not be sent after ready")

    class ShouldNotRunOrchestrator:
        def quarantine_staged(self, name):
            raise AssertionError("Moonlight must not be touched after ready")

    host = ReadyHost()
    key = ("epic-pc", "job-1")
    module._CONSOLE_RETRY_TASKS[key] = {
        "operationId": "op-ready",
        "startedAt": time.time(),
        "status": "pending",
        "failureCode": "",
        "routeReady": False,
    }
    module._start_remote_moonlight_console_retry(
        host=host,
        host_id="epic-pc",
        job_id="job-1",
        name="alpha",
        guest_ip="100.111.82.1",
        route_name="alpha--epic-pc",
        guest_username="operator",
        guest_password="transient-password",
        sunshine_username="sun-user",
        sunshine_password="sun-password",
        orchestrator=ShouldNotRunOrchestrator(),
        operation_id="op-ready",
    )

    task = module._CONSOLE_RETRY_TASKS[key]
    assert task["status"] == "ready"
    assert task["routeReady"] is True
    assert task["failureCode"] == ""


def test_remote_moonlight_retry_returns_pending_and_deduplicates(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class PendingHost(FakeRemoteHost):
        def provisioning_status(self, job_id):
            return {"job": {"id": job_id, "name": "alpha", "state": "setup_failed:streaming", "tailnetIp": "100.111.82.1"}}

        def console_credentials(self, *args, **kwargs):
            started.set()
            release.wait(2)

        def console_complete(self, *args, **kwargs):
            finished.set()
            return super().console_complete(*args, **kwargs)

    class MoonlightConsole:
        backend = "moonlight"

        def quarantine_staged(self, name):
            return None

        def build_plan(self, name, guest_ip, route_name=None):
            return SimpleNamespace(route_prefix=f"/vm/{name}/")

        def stage_plan(self, plan):
            return None

        def start_staged(self, name):
            return {"routePrefix": f"/vm/{name}/", "guestTcpVerified": True}

        def pair_staged(self, name, sunshine_username, sunshine_password):
            return {"routePrefix": f"/vm/{name}/", "guestTcpVerified": True}

        def stop_staged(self, name):
            return None

    host = PendingHost()
    module.VM_HOST_REGISTRY.get = lambda host_id="local": host
    module._CONSOLE_ORCHESTRATOR = MoonlightConsole()
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    payload = {
        "host_id": "epic-pc",
        "username": "operator",
        "password": "transient-password",
        "sunshineUsername": "sun-user",
        "sunshinePassword": "sun-password",
    }
    headers = {"Origin": "http://localhost", "X-Forwarded-Proto": "https", "X-CSRF-Token": csrf}
    response = client.post("/dashboard/api/provisioning-jobs/job-1/retry-console", json=payload, headers=headers)
    assert response.status_code == 202
    body = response.get_json()
    assert body["pending"] is True
    assert body["job"]["consoleRetryPending"] is True
    assert "transient-password" not in response.get_data(as_text=True)
    assert started.wait(1)

    duplicate = client.post("/dashboard/api/provisioning-jobs/job-1/retry-console", json=payload, headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"]["code"] == "console_retry_in_progress"

    release.set()
    assert finished.wait(2)
    deadline = time.time() + 2
    while time.time() < deadline and module._CONSOLE_RETRY_TASKS.get(('epic-pc', 'job-1'), {}).get('status') == 'pending':
        time.sleep(0.01)
    task = module._CONSOLE_RETRY_TASKS[('epic-pc', 'job-1')]
    assert task['status'] == 'ready'
    assert task['routeReady'] is True
    assert task['failureCode'] == ''


def test_remote_moonlight_retry_publishes_safe_terminal_failure(monkeypatch, tmp_path):
    module = load_app(monkeypatch, tmp_path)

    class FailedHost(FakeRemoteHost):
        def provisioning_status(self, job_id):
            return {"job": {"id": job_id, "name": "alpha", "state": "setup_failed:streaming", "tailnetIp": "100.111.82.1"}}

        def console_credentials(self, *args, **kwargs):
            raise module.VmHostUnavailable("not shown", status=503, code="management_transport_failed")

    class MoonlightConsole:
        backend = "moonlight"
        def quarantine_staged(self, name): return None
        def stop_staged(self, name): return None

    host = FailedHost()
    module.VM_HOST_REGISTRY.get = lambda host_id="local": host
    module._CONSOLE_ORCHESTRATOR = MoonlightConsole()
    client = authenticated_client(module)
    csrf = client.get("/dashboard/api/auth/csrf").get_json()["csrfToken"]
    headers = {"Origin": "http://localhost", "X-Forwarded-Proto": "https", "X-CSRF-Token": csrf}
    payload = {"host_id": "epic-pc", "username": "operator", "password": "secret", "sunshineUsername": "sun", "sunshinePassword": "sun-secret"}
    response = client.post("/dashboard/api/provisioning-jobs/job-1/retry-console", json=payload, headers=headers)
    assert response.status_code == 202
    deadline = time.time() + 2
    while time.time() < deadline:
        task = module._CONSOLE_RETRY_TASKS.get(("epic-pc", "job-1"))
        if task and task.get("status") == "failed":
            break
        time.sleep(0.01)
    assert task["status"] == "failed"
    assert task["failureCode"] == "management_transport_failed"
    assert "secret" not in str(task)
    status = client.get("/dashboard/api/provisioning-jobs/job-1?host_id=epic-pc")
    assert status.status_code == 200
    body = status.get_json()["job"]
    assert body["consoleRetryOutcome"] == "failed"
    assert body["errorCode"] == "management_transport_failed"
    assert "secret" not in status.get_data(as_text=True)


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
    assert setup.get_json()['launchUrl'] == '/dashboard/console/alpha/launch'
    paired_setup = client.get('/dashboard/console/alpha/setup')
    assert paired_setup.status_code == 302
    assert paired_setup.headers['Location'] == '/dashboard/console/alpha/launch'
    entry = client.get('/dashboard/console/alpha/')
    assert entry.status_code == 302
    assert entry.headers['Location'] == '/dashboard/console/alpha/setup'
    launch = client.get('/dashboard/console/alpha/launch')
    assert launch.status_code == 200
    body = launch.get_data(as_text=True)
    assert 'localStorage.removeItem("GUAC_AUTH_TOKEN")' in body
    assert 'sessionStorage.removeItem("GUAC_AUTH_TOKEN")' in body
    assert 'new URLSearchParams({data:"encrypted-data"})' in body
    assert '/vm/alpha/api/tokens' in body
    assert 'localStorage.setItem("GUAC_AUTH_TOKEN",JSON.stringify(result.authToken))' in body
    assert '/vm/alpha/?data=' not in body
    assert launch.headers['Cache-Control'] == 'no-store'
