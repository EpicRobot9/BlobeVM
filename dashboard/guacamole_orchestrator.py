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
import shutil
import socket
import subprocess
import time
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # Production fails closed until the pinned dependency exists.
    padding = Cipher = algorithms = modes = AESGCM = None

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
    if user.casefold() == "guacadmin":
        raise ConsoleOrchestrationError("The reserved Guacamole administrator name cannot be used.", status=400, code="reserved_username")
    if not password:
        raise ConsoleOrchestrationError("Console password policy rejected the claim.", status=400, code="invalid_password")
    salt = salt if salt is not None else secrets.token_bytes(32)
    # Guacamole hashes the UTF-8 password followed by the raw 32-byte salt.
    digest = hashlib.sha256(password.encode("utf-8") + salt).digest()
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
    credential_blob: str = ""


class GuacamoleOrchestrator:
    def __init__(
        self,
        *,
        root: str = "/opt/epicvm/instances",
        proxy_network: str = "proxy",
        public_host: str | None = None,
        tls_resolver: str | None = None,
        auth_middleware: str | None = None,
        router_priority: int | str | None = None,
        digests: Mapping[str, str] | None = None,
        tcp_probe: Callable[[str, int, float], bool] | None = None,
        disk_probe: Callable[[], bool] | None = None,
        route_owner_probe: Callable[[str], bool] | None = None,
        routing_probe: Callable[[], bool] | None = None,
        auth_status_probe: Callable[[str], bool] | None = None,
        command_runner: Callable[..., Any] | None = None,
        credential_secret: str | None = None,
    ):
        self.root = Path(root)
        self.proxy_network = str(proxy_network)
        self.public_host = str(public_host or os.environ.get("EPICVM_PUBLIC_HOST", "")).strip().lower()
        self.tls_resolver = str(tls_resolver or os.environ.get("EPICVM_TRAEFIK_CERTRESOLVER", "")).strip()
        self.auth_middleware = str(auth_middleware or os.environ.get("EPICVM_TRAEFIK_AUTH_MIDDLEWARE", "")).strip()
        self.router_priority = str(router_priority or os.environ.get("EPICVM_TRAEFIK_ROUTER_PRIORITY", "")).strip()
        self.digests = dict(digests or {})
        self.tcp_probe = tcp_probe or self._tcp_probe
        self.disk_probe = disk_probe or self._disk_ready
        self.route_owner_probe = route_owner_probe or self._route_available
        self.routing_probe = routing_probe or self._routing_available
        self.auth_status_probe = auth_status_probe or self._public_auth_rejected
        self.command_runner = command_runner or subprocess.run
        self.credential_secret = str(credential_secret or "")

    def _credential_key(self) -> bytes:
        if AESGCM is None or not self.credential_secret:
            raise ConsoleOrchestrationError("Console credential encryption is unavailable.", status=503, code="credential_encryption_unavailable")
        return hashlib.sha256(b"EpicVM console credentials v1\0" + self.credential_secret.encode("utf-8")).digest()

    def _json_auth_key(self, name: str) -> bytes:
        safe = validate_vm_name(name)
        return hmac.new(self._credential_key(), ("guacamole-json:" + safe).encode("utf-8"), hashlib.sha256).digest()[:16]

    def seal_credentials(self, *, name: str, username: str, password: str) -> str:
        safe = validate_vm_name(name)
        derive_guacamole_verifier(username, password)
        plaintext = json.dumps({"username": username, "password": password}, separators=(",", ":")).encode("utf-8")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._credential_key()).encrypt(nonce, plaintext, safe.encode("utf-8"))
        return base64.urlsafe_b64encode(b"EV1" + nonce + ciphertext).decode("ascii")

    def _open_credentials(self, name: str) -> dict[str, str]:
        safe = validate_vm_name(name)
        path = self._instance_root(safe) / "credentials.enc"
        try:
            raw = base64.urlsafe_b64decode(path.read_text(encoding="ascii"))
            if not raw.startswith(b"EV1"):
                raise ValueError("invalid envelope")
            plaintext = AESGCM(self._credential_key()).decrypt(raw[3:15], raw[15:], safe.encode("utf-8"))
            value = json.loads(plaintext)
            username = str(value.get("username") or "")
            password = str(value.get("password") or "")
            derive_guacamole_verifier(username, password)
            return {"username": username, "password": password}
        except ConsoleOrchestrationError:
            raise
        except Exception as exc:
            raise ConsoleOrchestrationError("Automatic console credentials are unavailable.", status=409, code="console_credentials_unavailable") from exc

    def has_auto_login(self, name: str) -> bool:
        try:
            self._open_credentials(name)
            return True
        except ConsoleOrchestrationError:
            return False

    def build_json_auth_data(self, name: str, *, ttl_seconds: int = 30) -> str:
        safe = validate_vm_name(name)
        target = self._instance_root(safe)
        try:
            plan = json.loads((target / "plan.json").read_text(encoding="utf-8"))
            if plan.get("owner") != "EpicVM" or plan.get("name") != safe:
                raise ValueError("ownership")
            guest_ip = str(plan["guestIp"])
        except Exception as exc:
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required") from exc
        credentials = self._open_credentials(safe)
        payload = json.dumps({
            "username": credentials["username"],
            "expires": int((time.time() + max(5, min(int(ttl_seconds), 60))) * 1000),
            "connections": {safe: {"protocol": "rdp", "parameters": {
                "hostname": guest_ip, "port": "3389", "security": "nla", "ignore-cert": "true",
                "username": credentials["username"], "password": credentials["password"], "domain": ".",
            }}},
        }, separators=(",", ":")).encode("utf-8")
        key = self._json_auth_key(safe)
        signed = hmac.new(key, payload, hashlib.sha256).digest() + payload
        padder = padding.PKCS7(128).padder()
        padded = padder.update(signed) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor()
        return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")

    def _routing_config(self) -> tuple[str, str, int]:
        if not re.fullmatch(r"[a-z0-9.-]+", self.public_host) or not self.tls_resolver or not self.router_priority.isdigit():
            raise ConsoleOrchestrationError("Verified Traefik routing configuration is unavailable.", status=503, code="routing_config_required")
        priority = int(self.router_priority)
        if priority < 1 or priority > 100000:
            raise ConsoleOrchestrationError("The Traefik router priority is invalid.", status=503, code="routing_config_invalid")
        return self.public_host, self.tls_resolver, priority

    def _disk_ready(self) -> bool:
        candidate = self.root
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        usage = shutil.disk_usage(candidate)
        used_percent = ((usage.total - usage.free) / usage.total * 100) if usage.total else 100
        return usage.free >= 20 * 1024**3 and used_percent < 85

    def _route_available(self, route_prefix: str) -> bool:
        try:
            listed = self.command_runner(["docker", "ps", "-aq"], check=True, capture_output=True, text=True)
            ids = str(getattr(listed, "stdout", "") or "").split()
            if not ids:
                return True
            inspected = self.command_runner(["docker", "inspect", *ids], check=True, capture_output=True, text=True)
            records = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))
            route_variants = (route_prefix, route_prefix.rstrip("/"))
            for record in records:
                labels = (((record or {}).get("Config") or {}).get("Labels") or {})
                route_values = (
                    str(value) for key, value in labels.items()
                    if str(key).startswith("traefik.http.routers.") and str(key).endswith(".rule")
                )
                if any(any(variant in value for variant in route_variants) for value in route_values):
                    return False
            return True
        except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConsoleOrchestrationError("Traefik route ownership could not be verified.", status=503, code="route_probe_failed") from exc

    def _routing_available(self) -> bool:
        _, tls_resolver, _ = self._routing_config()
        try:
            self.command_runner(["docker", "network", "inspect", self.proxy_network], check=True, capture_output=True, text=True)
            listed = self.command_runner(["docker", "ps", "-q"], check=True, capture_output=True, text=True)
            ids = str(getattr(listed, "stdout", "") or "").split()
            if not ids:
                return False
            inspected = self.command_runner(["docker", "inspect", *ids], check=True, capture_output=True, text=True)
            records = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))
            labels = [
                (((record or {}).get("Config") or {}).get("Labels") or {})
                for record in records
            ]
            dashboard_ok = any(
                str((record or {}).get("Name") or "").lstrip("/") == "blobedash"
                and self.proxy_network in (((record or {}).get("NetworkSettings") or {}).get("Networks") or {})
                for record in records
            )
            resolver_ok = any(any(str(key).endswith(".tls.certresolver") and str(value) == tls_resolver for key, value in item.items()) for item in labels)
            return dashboard_ok and resolver_ok
        except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConsoleOrchestrationError("Traefik authentication and TLS ownership could not be verified.", status=503, code="routing_probe_failed") from exc

    @staticmethod
    def _tcp_probe(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=float(timeout)):
                return True
        except OSError:
            return False

    def _public_auth_rejected(self, route_prefix: str) -> bool:
        url = f"https://{self.public_host}{route_prefix}"
        class NoRedirect(urlrequest.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urlrequest.build_opener(NoRedirect).open(url, timeout=8):
                return False
        except urlerror.HTTPError as exc:
            if int(exc.code) in (401, 403):
                return True
            if int(exc.code) not in (302, 303, 307, 308):
                return False
            location = urlparse(str(exc.headers.get("Location") or ""))
            return location.path == "/portal/login" and (not location.netloc or location.netloc == self.public_host)
        except (OSError, urlerror.URLError):
            return False

    def _image(self, key: str) -> str:
        value = str(self.digests.get(key) or os.environ.get(f"EPICVM_{key.upper()}_IMAGE", ""))
        if not SHA256_IMAGE_RE.fullmatch(value):
            raise ConsoleOrchestrationError(f"Digest-pinned {key} image is not configured.", status=503, code="digest_required")
        return value

    def _instance_root(self, name: str) -> Path:
        safe = validate_vm_name(name)
        return self.root / safe

    @staticmethod
    def _project_name(name: str) -> str:
        return f"epicvm-{validate_vm_name(name).replace('.', '-')}-rdp"

    def build_compose(self, *, name: str, guest_ip: str) -> str:
        safe = validate_vm_name(name)
        connection = build_rdp_connection(guest_ip=guest_ip)
        public_host, tls_resolver, router_priority = self._routing_config()
        guac_image = self._image("guacamole")
        guacd_image = self._image("guacd")
        postgres_image = self._image("postgres")
        db_name = f"epicvm_{safe.replace('-', '_').replace('.', '_')}"
        auth_middleware = f"epicvm-{safe}-portal-auth"
        labels = {
            "traefik.enable": "true",
            "com.blobevm.managed": "1",
            "com.epicvm.console": "1",
            "com.epicvm.vm.name": safe,
            "traefik.docker.network": self.proxy_network,
            f"traefik.http.routers.epicvm-{safe}.rule": f"Host(`{public_host}`) && PathPrefix(`/vm/{safe}/`)",
            f"traefik.http.routers.epicvm-{safe}.entrypoints": "websecure",
            f"traefik.http.routers.epicvm-{safe}.tls": "true",
            f"traefik.http.routers.epicvm-{safe}.tls.certresolver": tls_resolver,
            f"traefik.http.routers.epicvm-{safe}.priority": str(router_priority),
            f"traefik.http.routers.epicvm-{safe}.service": f"epicvm-{safe}",
            f"traefik.http.routers.epicvm-{safe}.middlewares": f"{auth_middleware},epicvm-{safe}-strip",
            f"traefik.http.middlewares.{auth_middleware}.forwardauth.address": f"http://blobedash:5000/dashboard/auth/vm/{safe}",
            f"traefik.http.middlewares.{auth_middleware}.forwardauth.trustForwardHeader": "true",
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
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U guac -d {db_name}"]
      interval: 5s
      timeout: 3s
      retries: 20
    volumes:
      - ./postgres:/var/lib/postgresql/data
      - ./initdb.sql:/docker-entrypoint-initdb.d/20-epicvm.sql:ro
  guacd:
    image: {_yaml_quote(guacd_image)}
    restart: unless-stopped
    networks:
      - internal
      - egress
    healthcheck:
      test: ["CMD-SHELL", "nc -z 127.0.0.1 4822 || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 20
  guacamole:
    image: {_yaml_quote(guac_image)}
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      guacd:
        condition: service_started
    environment:
      POSTGRESQL_ENABLED: "true"
      WEBAPP_CONTEXT: "ROOT"
      GUACD_HOSTNAME: "guacd"
      POSTGRESQL_HOSTNAME: "postgres"
      POSTGRESQL_DATABASE: {_yaml_quote(db_name)}
      POSTGRESQL_USERNAME: "guac"
      POSTGRESQL_PASSWORD: ""
      POSTGRESQL_SSL_MODE: "disable"
      JSON_ENABLED: "true"
      JSON_SECRET_KEY: "${{EPICVM_JSON_SECRET_KEY:?missing EpicVM JSON authentication key}}"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8080/ >/dev/null"]
      interval: 5s
      timeout: 3s
      retries: 30
      start_period: 20s
    networks:
      - internal
      - proxy
    labels:
{label_lines}
networks:
  internal:
    internal: true
  egress:
    driver: bridge
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
DELETE FROM guacamole_entity WHERE name = 'guacadmin' AND type = 'USER';
INSERT INTO guacamole_entity (name, type)
VALUES ({values['username']}, 'USER');
INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)
SELECT entity_id, decode({values['hash']}, 'base64'), decode({values['salt']}, 'base64'), NOW()
FROM guacamole_entity
WHERE name = {values['username']} AND type = 'USER';
INSERT INTO guacamole_connection (connection_name, protocol)
VALUES ({values['connection']}, 'rdp');
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'hostname', {values['guest_ip']} FROM guacamole_connection WHERE connection_name = {values['connection']};
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'port', '3389' FROM guacamole_connection WHERE connection_name = {values['connection']};
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'security', 'nla' FROM guacamole_connection WHERE connection_name = {values['connection']};
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'ignore-cert', 'true' FROM guacamole_connection WHERE connection_name = {values['connection']};
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'username', '${{GUAC_USERNAME}}' FROM guacamole_connection WHERE connection_name = {values['connection']};
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
SELECT connection_id, 'password', '${{GUAC_PASSWORD}}' FROM guacamole_connection WHERE connection_name = {values['connection']};
INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)
SELECT e.entity_id, c.connection_id, 'READ'
FROM guacamole_entity e, guacamole_connection c
WHERE e.name = {values['username']} AND e.type = 'USER' AND c.connection_name = {values['connection']};
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
            credential_blob=self.seal_credentials(name=safe, username=username, password=password),
        )

    def stage_plan(self, plan: ConsolePlan) -> Path:
        target = self._instance_root(plan.name)
        if target.exists():
            raise ConsoleOrchestrationError("A console instance with this name already exists.", status=409, code="console_exists")
        if not self.disk_probe():
            raise ConsoleOrchestrationError("kvm2 storage is below the provisioning safety threshold.", status=507, code="storage_gate")
        if not self.routing_probe():
            raise ConsoleOrchestrationError("The verified Traefik authentication or TLS route is unavailable.", status=503, code="routing_probe_failed")
        if not self.route_owner_probe(plan.route_prefix):
            raise ConsoleOrchestrationError("The requested console route is already owned.", status=409, code="route_collision")
        try:
            schema_result = self.command_runner(
                ["docker", "run", "--rm", self._image("guacamole"), "/opt/guacamole/bin/initdb.sh", "--postgresql"],
                check=True,
                capture_output=True,
                text=True,
            )
            schema = str(getattr(schema_result, "stdout", "") or "")
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConsoleOrchestrationError("The official Guacamole schema could not be generated.", status=503, code="schema_generation_failed") from exc
        if "CREATE TABLE guacamole_entity" not in schema:
            raise ConsoleOrchestrationError("The generated Guacamole schema was invalid.", status=503, code="schema_generation_failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = target.parent / f".{plan.name}-{secrets.token_hex(8)}"
        stage.mkdir(mode=0o700)
        try:
            (stage / "docker-compose.yml").write_text(plan.compose, encoding="utf-8")
            (stage / "initdb.sql").write_text(schema.rstrip() + "\n" + plan.sql_seed, encoding="utf-8")
            (stage / "plan.json").write_text(json.dumps({"owner": "EpicVM", "version": 1, "name": plan.name, "guestIp": plan.guest_ip, "routePrefix": plan.route_prefix}, separators=(",", ":")), encoding="utf-8")
            (stage / "credentials.enc").write_text(plan.credential_blob, encoding="ascii")
            # The PostgreSQL image runs as its own UID and must be able to read
            # this bind-mounted file.  The containing directory remains 0700,
            # while the file contains only schema and a salted verifier.
            os.chmod(stage / "initdb.sql", 0o644)
            os.chmod(stage / "plan.json", 0o600)
            os.chmod(stage / "credentials.enc", 0o600)
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

    def start_staged(self, name: str) -> dict[str, Any]:
        target = self._instance_root(name)
        if not target.is_dir() or not (target / "docker-compose.yml").is_file():
            raise ConsoleOrchestrationError("The named console plan is not staged.", status=404, code="console_not_found")
        try:
            staged = json.loads((target / "plan.json").read_text(encoding="utf-8"))
            if staged.get("owner") != "EpicVM" or staged.get("name") != validate_vm_name(name):
                raise ConsoleOrchestrationError("The staged console is not owned by EpicVM.", status=403, code="ownership_required")
            if not self.tcp_probe(str(staged["guestIp"]), 3389, 2.0):
                raise ConsoleOrchestrationError("Guest RDP is not reachable from kvm2.", status=409, code="guest_tcp_unavailable")
        except ConsoleOrchestrationError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ConsoleOrchestrationError("The staged console plan is invalid.", status=503, code="console_plan_invalid") from exc
        try:
            env = os.environ.copy()
            env["EPICVM_JSON_SECRET_KEY"] = self._json_auth_key(name).hex()
            self.command_runner(["docker", "compose", "-p", self._project_name(name), "up", "-d", "--wait", "--wait-timeout", "90"], cwd=str(target), check=True, capture_output=True, text=True, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            self.stop_staged(name)
            raise ConsoleOrchestrationError("The console stack failed its startup gate.", status=502, code="console_start_failed") from exc
        if not self._runtime_isolated(name):
            self.stop_staged(name)
            raise ConsoleOrchestrationError("The console stack exposed an internal service.", status=502, code="console_isolation_failed")
        auth_rejected = False
        for _ in range(10):
            if self.auth_status_probe(str(staged["routePrefix"])):
                auth_rejected = True
                break
            time.sleep(1)
        if not auth_rejected:
            self.stop_staged(name)
            raise ConsoleOrchestrationError("The public console route did not reject unauthenticated access.", status=502, code="console_auth_failed")
        return {"ok": True, "routePrefix": str(staged["routePrefix"]), "guestTcpVerified": True}

    def enable_auto_login(self, *, name: str, username: str, password: str) -> None:
        safe = validate_vm_name(name)
        target = self._instance_root(safe)
        try:
            plan = json.loads((target / "plan.json").read_text(encoding="utf-8"))
            if plan.get("owner") != "EpicVM" or plan.get("name") != safe:
                raise ValueError("ownership")
            compose_path = target / "docker-compose.yml"
            compose = compose_path.read_text(encoding="utf-8")
        except Exception as exc:
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required") from exc
        if "JSON_ENABLED:" not in compose:
            anchor = '      POSTGRESQL_SSL_MODE: "disable"\n'
            if anchor not in compose:
                raise ConsoleOrchestrationError("The console bundle cannot be upgraded safely.", status=409, code="console_upgrade_unavailable")
            compose = compose.replace(anchor, anchor + '      JSON_ENABLED: "true"\n      JSON_SECRET_KEY: "${EPICVM_JSON_SECRET_KEY:?missing EpicVM JSON authentication key}"\n', 1)
            compose_path.write_text(compose, encoding="utf-8")
        credential_path = target / "credentials.enc"
        credential_path.write_text(self.seal_credentials(name=safe, username=username, password=password), encoding="ascii")
        os.chmod(credential_path, 0o600)
        env = os.environ.copy()
        env["EPICVM_JSON_SECRET_KEY"] = self._json_auth_key(safe).hex()
        try:
            self.command_runner(["docker", "compose", "-p", self._project_name(safe), "up", "-d", "--wait", "--wait-timeout", "90"], cwd=str(target), check=True, capture_output=True, text=True, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConsoleOrchestrationError("The automatic console login upgrade failed.", status=502, code="console_upgrade_failed") from exc

    def _runtime_isolated(self, name: str) -> bool:
        project = self._project_name(name)
        try:
            listed = self.command_runner(
                ["docker", "ps", "--filter", f"label=com.docker.compose.project={project}", "-q"],
                check=True, capture_output=True, text=True,
            )
            ids = str(getattr(listed, "stdout", "") or "").split()
            if len(ids) != 3:
                return False
            inspected = self.command_runner(["docker", "inspect", *ids], check=True, capture_output=True, text=True)
            records = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))
            services = set()
            for record in records:
                labels = (((record or {}).get("Config") or {}).get("Labels") or {})
                services.add(str(labels.get("com.docker.compose.service") or ""))
                bindings = (((record or {}).get("HostConfig") or {}).get("PortBindings") or {})
                if bindings:
                    return False
            return services == {"postgres", "guacd", "guacamole"}
        except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def stop_staged(self, name: str) -> None:
        target = self._instance_root(name)
        if not target.is_dir() or not (target / "docker-compose.yml").is_file():
            return
        try:
            self.command_runner(["docker", "compose", "-p", self._project_name(name), "down", "--remove-orphans"], cwd=str(target), check=False, capture_output=True, text=True)
        except OSError:
            return

    def quarantine_staged(self, name: str) -> Path | None:
        safe = validate_vm_name(name)
        target = self._instance_root(safe)
        if not target.is_dir():
            return None
        self.stop_staged(safe)
        quarantine = target.parent / "quarantine" / f"{safe}-{int(time.time())}"
        quarantine.parent.mkdir(mode=0o700, exist_ok=True)
        target.rename(quarantine)
        return quarantine

    def teardown(self, *, name: str, confirm_name: str, device_id: str | None = None, revoke: Callable[[str], Any] | None = None) -> dict[str, Any]:
        safe = validate_vm_name(name)
        if str(confirm_name) != safe:
            raise ConsoleOrchestrationError("Exact VM name confirmation is required.", status=400, code="confirmation_required")
        target = self._instance_root(safe)
        if not target.is_dir() or not (target / "docker-compose.yml").is_file():
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required")
        try:
            staged = json.loads((target / "plan.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required") from exc
        if staged.get("owner") != "EpicVM" or staged.get("name") != safe:
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required")
        # Route is disabled first by taking down Guacamole before quarantine.
        self.command_runner(["docker", "compose", "-p", self._project_name(safe), "down", "--remove-orphans"], cwd=str(target), check=True, capture_output=True, text=True)
        if device_id and revoke is not None:
            revoke(device_id)
        quarantine = target.parent / "quarantine" / f"{safe}-{int(time.time())}"
        quarantine.parent.mkdir(mode=0o700, exist_ok=True)
        target.rename(quarantine)
        return {"ok": True, "name": safe, "quarantineUntil": int(time.time()) + 7 * 86400}
