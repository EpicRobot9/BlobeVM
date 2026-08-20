import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dashboard.vm_hosts import LocalDockerHost, VmHostRegistry, VmHostUnavailable


def test_local_provider_preserves_manager_commands(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="started\n", stderr="")

    monkeypatch.setattr("dashboard.vm_hosts.subprocess.run", fake_run)
    host = LocalDockerHost(manager="/custom/blobe-vm-manager")

    result = host.run_manager("start", "alpha", capture_output=True, text=True)

    assert result.returncode == 0
    assert calls == [
        (
            ["/custom/blobe-vm-manager", "start", "alpha"],
            {"capture_output": True, "text": True},
        )
    ]


def test_local_inventory_items_have_non_breaking_placement_metadata(monkeypatch):
    def fake_check_output(argv, **kwargs):
        assert argv == ["/custom/blobe-vm-manager", "list"]
        assert kwargs == {"text": True}
        return "- alpha -> running -> http://127.0.0.1:20001/\n"

    monkeypatch.setattr("dashboard.vm_hosts.subprocess.check_output", fake_check_output)
    host = LocalDockerHost(manager="/custom/blobe-vm-manager")

    inventory = host.list_vms()

    assert inventory == [
        {
            "name": "alpha",
            "status": "running",
            "url": "http://127.0.0.1:20001/",
            "placement": "local",
            "host_id": "local",
            "host_name": "EpicVM Server",
            "provider": "local-docker",
        }
    ]


def test_vm_host_registry_looks_up_local_provider_and_rejects_unknown_hosts():
    host = LocalDockerHost(manager="/custom/blobe-vm-manager")
    registry = VmHostRegistry([host])

    assert registry.get("local") is host

    with pytest.raises(VmHostUnavailable):
        registry.get("missing")


def test_dashboard_local_inventory_uses_registered_provider(monkeypatch, tmp_path):
    import importlib.util

    dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
    monkeypatch.setenv("BLOBEDASH_STATE", str(tmp_path))
    monkeypatch.setenv("BLOBEVM_ALLOW_INSECURE_DASHBOARD", "1")
    spec = importlib.util.spec_from_file_location("remote_hosts_test_app", os.path.join(dashboard_dir, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module.LOCAL_VM_HOST,
        "list_vms",
        lambda: [{"name": "alpha", "status": "running", "url": "/vm/alpha/"}],
    )

    inventory = module.manager_json_list()

    assert inventory == [
        {
            "name": "alpha",
            "status": "running",
            "url": "/vm/alpha/",
            "placement": "local",
            "host_id": "local",
            "host_name": "EpicVM Server",
            "provider": "local-docker",
        }
    ]


def test_portal_assignment_inventory_includes_remote_provider(monkeypatch, tmp_path):
    import importlib.util

    dashboard_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
    monkeypatch.setenv("BLOBEDASH_STATE", str(tmp_path))
    monkeypatch.setenv("BLOBEVM_ALLOW_INSECURE_DASHBOARD", "1")
    spec = importlib.util.spec_from_file_location(
        "remote_assignment_inventory_test_app", os.path.join(dashboard_dir, "app.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class RemoteProvider:
        kind = "remote"

    class Registry:
        providers = {"local": object(), "epic-pc": RemoteProvider()}

    monkeypatch.setattr(module, "VM_HOST_REGISTRY", Registry())
    monkeypatch.setattr(
        module,
        "manager_json_list",
        lambda host_id=None: (
            [{"name": "local-vm"}]
            if host_id == "local"
            else [{"name": "gaming-gpup-pilot-03"}]
        ),
    )

    assert module._known_vm_names() == {"local-vm", "gaming-gpup-pilot-03"}


def test_dashboard_bulk_recreate_uses_registered_provider(monkeypatch, tmp_path):
    import importlib.util

    dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
    spec = importlib.util.spec_from_file_location(
        "remote_hosts_bulk_route_test_app", os.path.join(dashboard_dir, "app.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_allow_insecure_dashboard", lambda: True)
    monkeypatch.setenv("BLOBEDASH_STATE", str(tmp_path))

    provider_calls = []
    subprocess_calls = []

    def fake_provider_run(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="recreated\n", stderr="")

    def fake_subprocess_run(argv, **kwargs):
        subprocess_calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="direct\n", stderr="")

    monkeypatch.setattr(module.LOCAL_VM_HOST, "run_manager", fake_provider_run)
    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)

    response = module.app.test_client().post(
        "/dashboard/api/recreate", json={"names": ["alpha"]}
    )

    assert response.status_code == 200
    assert provider_calls == [
        (("recreate", "alpha"), {"capture_output": True, "text": True})
    ]
    assert subprocess_calls == []


def test_remote_inventory_failure_does_not_fall_back_to_local(monkeypatch, tmp_path):
    import importlib.util

    dashboard_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
    monkeypatch.setenv("BLOBEVM_ALLOW_INSECURE_DASHBOARD", "1")
    spec = importlib.util.spec_from_file_location(
        "remote_inventory_failure_test_app", os.path.join(dashboard_dir, "app.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class OfflineRemote:
        kind = "remote"
        host_id = "offline-pc"
        host_name = "Offline PC"

        def list_vms(self):
            raise module.VmHostUnavailable("offline")

        def normalize_inventory(self, instances):
            return list(instances)

    class Registry:
        def refresh(self):
            return None

        def get(self, host_id="local"):
            assert host_id == "offline-pc"
            return OfflineRemote()

    monkeypatch.setattr(module, "VM_HOST_REGISTRY", Registry())

    with pytest.raises(module.VmHostUnavailable, match="offline"):
        module.manager_json_list("offline-pc")
