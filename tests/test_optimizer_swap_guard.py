import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

import optimizer


def test_swap_guard_does_not_restart_vm_below_configured_max_swap_percent(monkeypatch):
    monkeypatch.setattr(
        optimizer,
        "gather_stats",
        lambda: {"swap": {"total": 100, "used": 24}},
    )
    monkeypatch.setattr(
        optimizer,
        "get_docker_stats",
        lambda: [{"name": "blobevm_idle", "mem_usage": "1GiB"}],
    )
    restart_calls = []
    monkeypatch.setattr(
        optimizer,
        "_restart_vm_container",
        lambda *args, **kwargs: restart_calls.append((args, kwargs)) or {"action": "restart"},
    )

    assert optimizer._run_swap_guard({"maxSwapPercent": 30}, {}, {}) is None
    assert restart_calls == []
