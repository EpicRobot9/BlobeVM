import json
from pathlib import Path

import pytest

from dashboard.guacamole_orchestrator import ConsoleOrchestrationError, GuacamoleOrchestrator, build_rdp_connection, derive_guacamole_verifier


def digests():
    return {key: f"example/{key}:1.6.0@sha256:{'a' * 64}" for key in ('guacamole', 'guacd', 'postgres')}


def test_plan_is_digest_pinned_and_keeps_rdp_credentials_tokenized(tmp_path):
    orch = GuacamoleOrchestrator(root=str(tmp_path), digests=digests(), tcp_probe=lambda *_: True)
    plan = orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')

    assert '@sha256:' in plan.compose
    assert 'internal: true' in plan.compose
    assert 'POSTGRES_HOST_AUTH_METHOD' in plan.compose
    assert plan.connection['username'] == '${GUAC_USERNAME}'
    assert plan.connection['password'] == '${GUAC_PASSWORD}'
    assert 'transient-password' not in plan.compose
    assert plan.verifier['password_hash']


def test_tcp_gate_blocks_route_plan():
    orch = GuacamoleOrchestrator(root='.', digests=digests(), tcp_probe=lambda *_: False)
    with pytest.raises(ConsoleOrchestrationError) as exc:
        orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')
    assert exc.value.code == 'guest_tcp_unavailable'
    assert exc.value.status == 409


def test_stage_and_teardown_quarantines_named_resources(tmp_path):
    calls = []
    revoked = []
    runner = lambda args, **kwargs: calls.append((args, kwargs))
    orch = GuacamoleOrchestrator(root=str(tmp_path), digests=digests(), tcp_probe=lambda *_: True, command_runner=runner)
    plan = orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')
    target = orch.stage_plan(plan)
    assert (target / 'docker-compose.yml').is_file()
    verifier = json.loads((target / 'guacamole' / 'user-verifier.json').read_text())
    assert 'transient-password' not in (target / 'guacamole' / 'user-verifier.json').read_text()
    assert verifier['password_hash']
    result = orch.teardown(name='alpha', confirm_name='alpha', device_id='device-1', revoke=revoked.append)
    assert result['ok'] is True
    assert revoked == ['device-1']
    assert any('down' in call[0] for call in calls)
    assert list((tmp_path / 'quarantine').glob('alpha-*'))


def test_guest_ip_and_digest_gates_are_fail_closed():
    with pytest.raises(ConsoleOrchestrationError):
        build_rdp_connection(guest_ip='192.168.1.10')
    with pytest.raises(ConsoleOrchestrationError):
        GuacamoleOrchestrator(root='.', digests={}).build_compose(name='alpha', guest_ip='100.111.82.1')
