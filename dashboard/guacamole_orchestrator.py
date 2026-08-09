"""Source-only kvm2 Guacamole/Traefik orchestration for EpicVM.

The module emits a per-VM compose plan but never deploys it by itself.  A
caller must explicitly invoke the injected command runner after the TCP gate
passes.  Secrets used to derive the Guacamole verifier are never written in
clear text; the RDP connection deliberately contains Guacamole's tokenized
``${GUAC_USERNAME}``/``${GUAC_PASSWORD}`` values.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

VM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
SHA256_IMAGE_RE = re.compile(r"^[^@]+@sha256:[0-9a-f]{64}$")
TAILSCALE_CGNAT = "100.64.0.0/10"


class ConsoleOrchestrationError(RuntimeError):
    def __init__(self, message: str, *, status: int = 502, code: str = "console_error"):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)


def validate_vm_name(name: str) -> str:
    value = str(name or "").strip().lower()
    if not VM_NAME_RE.fullmatch(value):
        raise ConsoleOrchestrationError("Invalid VM name.", status=400, code="invalid_name")
    return value


def _yaml_quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def derive_guacamole_verifier(username: str, password: str, *, salt: bytes | None = None) -> dict[str, str]:
    """Return the Guacamole SHA-256 verifier without retaining the password."""
    user = str(username or "")
    if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9._-]{2,31}$", user):
        raise ConsoleOrchestrationError("Invalid console username.", status=400, code="invalid_username")
    if not password or len(password) < 12:
        raise ConsoleOrchestrationError("Console password policy rejected the claim.", status=400, code="invalid_password")
    salt = salt if salt is not None else secrets.token_bytes(32)
    digest = hashlib.sha256(salt + password.encode("utf-8")).digest()
    return {
        "username": user,
        "password_hash": base64.b64encode(digest).decode("ascii"),
        "password_salt": base64.b64encode(salt).decode("ascii"),
        "password_date": "now",
        "algorithm": "SHA-256",
    }


def build_rdp_connection(*, guest_ip: str) -> dict[str, str]:
    try:
        socket.inet_aton(str(guest_ip))
    except OSError as exc:
        raise ConsoleOrchestrationError("The guest address is invalid.", status=422, code="invalid_guest_ip") from exc
    first = int(str(guest_ip).split(".", 1)[0])
    second = int(str(guest_ip).split(".")[1])
    if first != 100 or not 64 <= second <= 127:
        raise ConsoleOrchestrationError("The guest address is outside the tailnet range.", status=422, code="invalid_guest_ip")
    return {
        "protocol": "rdp",
        "hostname": str(guest_ip),
        "port": "3389",
        "security": "nla",
        "ignore-cert": "true",
        "username": "${GUAC_USERNAME}",
        "password": "${GUAC_PASSWORD}",
    }


@dataclass(frozen=True)
class ConsolePlan:
    name: str
    guest_ip: str
    route_prefix: str
    compose: str
    verifier: dict[str, str]
    connection: dict[str, str]
    sql_seed: str = ""
    guacamole_properties: str = ""


class GuacamoleOrchestrator:
    def __init__(
        self,
        *,
        root: str = "/opt/epicvm/instances",
        proxy_network: str = "proxy",
        forward_auth: str = "http://blobedash:5000/dashboard/auth/vm",
        digests: Mapping[str, str] | None = None,
        tcp_probe: Callable[[str, int, float], bool] | None = None,
        command_runner: Callable[..., Any] | None = None,
    ):
        self.root = Path(root)
        self.proxy_network = str(proxy_network)
        self.forward_auth = str(forward_auth).rstrip("/")
        self.digests = dict(digests or {})
        self.tcp_probe = tcp_probe or self._tcp_probe
        self.command_runner = command_runner or subprocess.run

    @staticmethod
    def _tcp_probe(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=float(timeout)):
                return True
        except OSError:
            return False

    def _image(self, key: str) -> str:
        value = str(self.digests.get(key) or os.environ.get(f"EPICVM_{key.upper()}_IMAGE", ""))
        if not SHA256_IMAGE_RE.fullmatch(value):
            raise ConsoleOrchestrationError(f"Digest-pinned {key} image is not configured.", status=503, code="digest_required")
        return value

    def _instance_root(self, name: str) -> Path:
        safe = validate_vm_name(name)
        return self.root / safe

    def build_compose(self, *, name: str, guest_ip: str) -> str:
        safe = validate_vm_name(name)
        connection = build_rdp_connection(guest_ip=guest_ip)
        guac_image = self._image("guacamole")
        guacd_image = self._image("guacd")
        postgres_image = self._image("postgres")
        db_name = f"epicvm_{safe.replace('-', '_').replace('.', '_')}"
        labels = {
            "traefik.enable": "true",
            "traefik.docker.network": self.proxy_network,
            f"traefik.http.routers.epicvm-{safe}.rule": f"PathPrefix(`/vm/{safe}/`)",
            f"traefik.http.routers.epicvm-{safe}.entrypoints": "websecure",
            f"traefik.http.routers.epicvm-{safe}.tls": "true",
            f"traefik.http.routers.epicvm-{safe}.service": f"epicvm-{safe}",
            f"traefik.http.routers.epicvm-{safe}.middlewares": f"epicvm-{safe}-auth,epicvm-{safe}-strip",
            f"traefik.http.middlewares.epicvm-{safe}-auth.forwardauth.address": f"{self.forward_auth}/{safe}",
            f"traefik.http.middlewares.epicvm-{safe}-auth.forwardauth.trustForwardHeader": "true",
            f"traefik.http.middlewares.epicvm-{safe}-strip.stripprefix.prefixes": f"/vm/{safe}",
            f"traefik.http.services.epicvm-{safe}.loadbalancer.server.port": "8080",
        }
        label_lines = "\n".join(f"      {key}: {_yaml_quote(value)}" for key, value in labels.items())
        # PostgreSQL is intentionally private to this stack and uses its
        # internal network only.  No database credential is emitted.
        return f'''services:
  postgres:
    image: {_yaml_quote(postgres_image)}
    restart: unless-stopped
    environment:
      POSTGRES_DB: {_yaml_quote(db_name)}
      POSTGRES_USER: "guac"
      POSTGRES_HOST_AUTH_METHOD: "trust"
    networks:
      - internal
    volumes:
      - ./postgres:/var/lib/postgresql/data
      - ./initdb.sql:/docker-entrypoint-initdb.d/20-epicvm.sql:ro
  guacd:
    image: {_yaml_quote(guacd_image)}
    restart: unless-stopped
    networks:
      - internal
  guacamole:
    image: {_yaml_quote(guac_image)}
    restart: unless-stopped
    depends_on:
      - postgres
      - guacd
    environment:
      GUACD_HOSTNAME: "guacd"
      POSTGRESQL_HOSTNAME: "postgres"
      POSTGRESQL_DATABASE: {_yaml_quote(db_name)}
      POSTGRESQL_USERNAME: "guac"
      GUACAMOLE_HOME: "/etc/guacamole"
    volumes:
      - ./guacamole:/etc/guacamole:ro
      - ./guacamole.properties:/etc/guacamole/guacamole.properties:ro
    networks:
      - internal
      - proxy
    labels:
{label_lines}
networks:
  internal:
    internal: true
  proxy:
    external: true
'''

    @staticmethod
    def _sql(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def build_sql_seed(self, *, name: str, guest_ip: str, verifier: Mapping[str, str]) -> str:
        safe = validate_vm_name(name)
        connection = build_rdp_connection(guest_ip=guest_ip)
        username = str(verifier["username"])
        hash_b64 = str(verifier["password_hash"])
        salt_b64 = str(verifier["password_salt"])
        values = {
            "username": self._sql(username),
            "connection": self._sql(safe),
            "guest_ip": self._sql(connection["hostname"]),
            "hash": self._sql(hash_b64),
            "salt": self._sql(salt_b64),
        }
        # This is intentionally a per-instance seed.  It contains only the
        # salted verifier and Guacamole token placeholders, never the claim
        # password itself.
        return f"""BEGIN;
INSERT INTO guacamole_entity (name, type)
VALUES ({values['username']}, 'USER')
ON CONFLICT (name, type) DO NOTHING;
INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)
SELECT entity_id, decode({values['hash']}, 'base64'), decode({values['salt']}, 'base64'), NOW()
FROM guacamole_entity
WHERE name = {values['username']} AND type = 'USER'
ON CONFLICT (entity_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, password_salt = EXCLUDED.password_salt, password_date = EXCLUDED.password_date;
INSERT INTO guacamole_connection (connection_name, protocol, proxy_hostname, proxy_port)
VALUES ({values['connection']}, 'rdp', {values['guest_ip']}, 3389)
ON CONFLICT (connection_name) DO UPDATE SET proxy_hostname = EXCLUDED.proxy_hostname, proxy_port = EXCLUDED.proxy_port;
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'security', 'nla' FROM guacamole_connection WHERE connection_name = {values['connection']}
ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'ignore-cert', 'true' FROM guacamole_connection WHERE connection_name = {values['connection']}
ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'username', '${{GUAC_USERNAME}}' FROM guacamole_connection WHERE connection_name = {values['connection']}
ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'password', '${{GUAC_PASSWORD}}' FROM guacamole_connection WHERE connection_name = {values['connection']}
ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;
COMMIT;
"""

    def build_guacamole_properties(self, *, name: str) -> str:
        safe = validate_vm_name(name)
        db_name = f"epicvm_{safe.replace('-', '_').replace('.', '_')}"
        return "\n".join((
            "postgresql-hostname: postgres",
            "postgresql-port: 5432",
            f"postgresql-database: {db_name}",
            "postgresql-username: guac",
            "postgresql-ssl-mode: disable",
            "guacd-hostname: guacd",
            "guacd-port: 4822",
            "auth-provider: net.sourceforge.guacamole.net.auth.postgresql.PostgreSQLAuthenticationProvider",
            "",
        ))

    def build_plan(self, *, name: str, guest_ip: str, username: str, password: str) -> ConsolePlan:
        safe = validate_vm_name(name)
        if not self.tcp_probe(guest_ip, 3389, 2.0):
            raise ConsoleOrchestrationError("Guest RDP is not reachable from kvm2.", status=409, code="guest_tcp_unavailable")
        verifier = derive_guacamole_verifier(username, password)
        connection = build_rdp_connection(guest_ip=guest_ip)
        sql_seed = self.build_sql_seed(name=safe, guest_ip=guest_ip, verifier=verifier)
        return ConsolePlan(
            name=safe,
            guest_ip=guest_ip,
            route_prefix=f"/vm/{safe}/",
            compose=self.build_compose(name=safe, guest_ip=guest_ip),
            verifier=verifier,
            connection=connection,
            sql_seed=sql_seed,
            guacamole_properties=self.build_guacamole_properties(name=safe),
        )

    def stage_plan(self, plan: ConsolePlan) -> Path:
        target = self._instance_root(plan.name)
        if target.exists():
            raise ConsoleOrchestrationError("A console instance with this name already exists.", status=409, code="console_exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = target.parent / f".{plan.name}-{secrets.token_hex(8)}"
        stage.mkdir(mode=0o700)
        try:
            (stage / "docker-compose.yml").write_text(plan.compose, encoding="utf-8")
            (stage / "guacamole").mkdir(mode=0o700)
            verifier = dict(plan.verifier)
            (stage / "guacamole" / "user-verifier.json").write_text(json.dumps(verifier, separators=(",", ":")), encoding="utf-8")
            (stage / "guacamole" / "connection.json").write_text(json.dumps(plan.connection, separators=(",", ":")), encoding="utf-8")
            (stage / "initdb.sql").write_text(plan.sql_seed, encoding="utf-8")
            (stage / "guacamole.properties").write_text(plan.guacamole_properties, encoding="utf-8")
            (stage / "plan.json").write_text(json.dumps({"name": plan.name, "guestIp": plan.guest_ip, "routePrefix": plan.route_prefix}, separators=(",", ":")), encoding="utf-8")
            os.chmod(stage / "guacamole" / "user-verifier.json", 0o600)
            os.chmod(stage / "guacamole" / "connection.json", 0o600)
            os.chmod(stage / "initdb.sql", 0o600)
            os.chmod(stage / "guacamole.properties", 0o600)
            os.chmod(stage / "plan.json", 0o600)
            stage.rename(target)
            return target
        except Exception:
            if stage.exists():
                for child in sorted(stage.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                stage.rmdir()
            raise

    def start_staged(self, name: str) -> None:
        target = self._instance_root(name)
        if not target.is_dir() or not (target / "docker-compose.yml").is_file():
            raise ConsoleOrchestrationError("The named console plan is not staged.", status=404, code="console_not_found")
        try:
            staged = json.loads((target / "plan.json").read_text(encoding="utf-8"))
            if not self.tcp_probe(str(staged["guestIp"]), 3389, 2.0):
                raise ConsoleOrchestrationError("Guest RDP is not reachable from kvm2.", status=409, code="guest_tcp_unavailable")
        except ConsoleOrchestrationError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ConsoleOrchestrationError("The staged console plan is invalid.", status=503, code="console_plan_invalid") from exc
        self.command_runner(["docker", "compose", "up", "-d"], cwd=str(target), check=True, capture_output=True, text=True)

    def teardown(self, *, name: str, confirm_name: str, device_id: str | None = None, revoke: Callable[[str], Any] | None = None) -> dict[str, Any]:
        safe = validate_vm_name(name)
        if str(confirm_name) != safe:
            raise ConsoleOrchestrationError("Exact VM name confirmation is required.", status=400, code="confirmation_required")
        target = self._instance_root(safe)
        if not target.is_dir() or not (target / "docker-compose.yml").is_file():
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required")
        # Route is disabled first by taking down Guacamole before quarantine.
        self.command_runner(["docker", "compose", "down", "--remove-orphans"], cwd=str(target), check=True, capture_output=True, text=True)
        if device_id and revoke is not None:
            revoke(device_id)
        quarantine = target.parent / "quarantine" / f"{safe}-{int(time.time())}"
        quarantine.parent.mkdir(mode=0o700, exist_ok=True)
        target.rename(quarantine)
        return {"ok": True, "name": safe, "quarantineUntil": int(time.time()) + 7 * 86400}
