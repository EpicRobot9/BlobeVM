import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from dashboard.guacamole_orchestrator import ConsoleOrchestrationError, GuacamoleOrchestrator, build_rdp_connection, derive_guacamole_verifier


def digests():
    return {key: f"example/{key}:1.6.0@sha256:{'a' * 64}" for key in ('guacamole', 'guacd', 'postgres')}


def make_orchestrator(root, **overrides):
    options = {
        "root": str(root),
        "digests": digests(),
        "tcp_probe": lambda *_: True,
        "disk_probe": lambda: True,
        "route_owner_probe": lambda *_: True,
        "routing_probe": lambda: True,
        "auth_status_probe": lambda *_: True,
        "public_host": "techexplore.us",
        "tls_resolver": "letsencrypt",
        "auth_middleware": "epic-root-auth@docker",
        "router_priority": 500,
    }
    options.update(overrides)
    return GuacamoleOrchestrator(**options)


def test_plan_is_digest_pinned_and_keeps_rdp_credentials_tokenized(tmp_path):
    orch = make_orchestrator(tmp_path)
    plan = orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')

    assert '@sha256:' in plan.compose
    assert 'internal: true' in plan.compose
    assert 'POSTGRES_HOST_AUTH_METHOD' in plan.compose
    assert 'WEBAPP_CONTEXT: "ROOT"' in plan.compose
    assert 'nc -z 127.0.0.1 4822' in plan.compose
    assert 'interval: 5s' in plan.compose
    assert 'curl -fsS http://127.0.0.1:8080/' in plan.compose
    assert 'ports:' not in plan.compose
    assert plan.connection['username'] == '${GUAC_USERNAME}'
    assert plan.connection['password'] == '${GUAC_PASSWORD}'
    assert 'transient-password' not in plan.compose
    assert plan.verifier['password_hash']
    assert 'Host(`techexplore.us`)' in plan.compose
    assert 'epic-root-auth@docker' in plan.compose


def test_verifier_uses_guacamole_password_then_salt_order():
    salt = bytes(range(32))
    verifier = derive_guacamole_verifier('operator', 'transient-password', salt=salt)
    expected = hashlib.sha256(b'transient-password' + salt).digest()
    assert base64.b64decode(verifier['password_hash']) == expected


def test_reserved_default_administrator_is_rejected():
    with pytest.raises(ConsoleOrchestrationError) as exc:
        derive_guacamole_verifier('guacadmin', 'transient-password')
    assert exc.value.code == 'reserved_username'


def test_tcp_gate_blocks_route_plan():
    orch = make_orchestrator('.', tcp_probe=lambda *_: False)
    with pytest.raises(ConsoleOrchestrationError) as exc:
        orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')
    assert exc.value.code == 'guest_tcp_unavailable'
    assert exc.value.status == 409


def test_stage_and_teardown_quarantines_named_resources(tmp_path):
    calls = []
    revoked = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        stdout = 'CREATE TABLE guacamole_entity (entity_id integer);' if any(str(arg).endswith('initdb.sh') for arg in args) else ''
        return SimpleNamespace(stdout=stdout, returncode=0)
    orch = make_orchestrator(tmp_path, command_runner=runner)
    plan = orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')
    target = orch.stage_plan(plan)
    assert (target / 'docker-compose.yml').is_file()
    assert 'com.blobevm.managed: "1"' in (target / 'docker-compose.yml').read_text()
    seed = (target / 'initdb.sql').read_text()
    assert 'CREATE TABLE guacamole_entity' in seed
    assert "DELETE FROM guacamole_entity WHERE name = 'guacadmin'" in seed
    assert 'transient-password' not in seed
    assert 'guacamole_connection_permission' in seed
    assert "'hostname', '100.111.82.1'" in seed
    assert "'port', '3389'" in seed
    assert 'proxy_hostname' not in seed
    result = orch.teardown(name='alpha', confirm_name='alpha', device_id='device-1', revoke=revoked.append)
    assert result['ok'] is True
    assert revoked == ['device-1']
    assert any('down' in call[0] for call in calls)
    assert list((tmp_path / 'quarantine').glob('alpha-*'))


def test_guest_ip_and_digest_gates_are_fail_closed():
    with pytest.raises(ConsoleOrchestrationError):
        build_rdp_connection(guest_ip='192.168.1.10')
    with pytest.raises(ConsoleOrchestrationError):
        make_orchestrator('.', digests={}).build_compose(name='alpha', guest_ip='100.111.82.1')


def test_storage_and_route_collision_gates_prevent_staging(tmp_path):
    plan = make_orchestrator(tmp_path).build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')
    with pytest.raises(ConsoleOrchestrationError) as storage:
        make_orchestrator(tmp_path, disk_probe=lambda: False).stage_plan(plan)
    assert storage.value.code == 'storage_gate'
    with pytest.raises(ConsoleOrchestrationError) as collision:
        make_orchestrator(tmp_path, route_owner_probe=lambda *_: False).stage_plan(plan)
    assert collision.value.code == 'route_collision'


def test_teardown_rejects_a_bundle_without_exact_epicvm_ownership(tmp_path):
    def runner(args, **kwargs):
        stdout = 'CREATE TABLE guacamole_entity (entity_id integer);' if any(str(arg).endswith('initdb.sh') for arg in args) else ''
        return SimpleNamespace(stdout=stdout, returncode=0)
    orch = make_orchestrator(tmp_path, command_runner=runner)
    plan = orch.build_plan(name='alpha', guest_ip='100.111.82.1', username='operator', password='transient-password')
    target = orch.stage_plan(plan)
    (target / 'plan.json').write_text('{"owner":"someone-else","name":"alpha"}')
    with pytest.raises(ConsoleOrchestrationError) as exc:
        orch.teardown(name='alpha', confirm_name='alpha')
    assert exc.value.code == 'ownership_required'


def test_routing_probe_accepts_only_an_actively_reused_auth_middleware_and_resolver(tmp_path):
    records = [{"Config": {"Labels": {
        "traefik.http.routers.known.middlewares": "epic-root-auth@file,known-strip",
        "traefik.http.routers.known.tls.certresolver": "letsencrypt",
    }}}]
    def runner(args, **kwargs):
        if args[:3] == ['docker', 'ps', '-q']:
            return SimpleNamespace(stdout='container-1\n', returncode=0)
        if args[:2] == ['docker', 'inspect']:
            return SimpleNamespace(stdout=json.dumps(records), returncode=0)
        return SimpleNamespace(stdout='', returncode=0)
    orch = make_orchestrator(tmp_path, command_runner=runner, auth_middleware='epic-root-auth@file')
    assert orch._routing_available() is True
    records[0]['Config']['Labels'].pop('traefik.http.routers.known.middlewares')
    assert orch._routing_available() is False
