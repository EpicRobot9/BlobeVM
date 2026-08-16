"""Fail-closed Moonlight Web/Sunshine orchestration for EpicVM.

The bundle is intentionally smaller than the legacy Guacamole bundle.  The
Moonlight Web database contains only its own client keys and safe host/user
metadata.  Sunshine credentials are accepted by :meth:`pair_staged` for one
request, used for the official Sunshine pairing API, and never written to the
bundle, compose file, process arguments, or logs.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import error as urlerror, request as urlrequest

try:
    from .guacamole_orchestrator import ConsoleOrchestrationError
except ImportError:  # pragma: no cover - direct source execution
    from guacamole_orchestrator import ConsoleOrchestrationError


VM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
TAILSCALE_IP_RE = re.compile(r"^100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}$")
SHA256_IMAGE_RE = re.compile(r"^[^@]+@sha256:[0-9a-f]{64}$")
DEFAULT_MOONLIGHT_IMAGE = "mrcreativ3001/moonlight-web-stream@sha256:694ca7e33266a56bf4c8bb29cb916b0927f126578dae4cf0710a881efce6564b"


def validate_vm_name(name: str) -> str:
    value = str(name or "").strip().lower()
    if not VM_NAME_RE.fullmatch(value):
        raise ConsoleOrchestrationError("Invalid VM name.", status=400, code="invalid_name")
    return value


def validate_guest_ip(address: str) -> str:
    value = str(address or "").strip()
    if not TAILSCALE_IP_RE.fullmatch(value):
        raise ConsoleOrchestrationError("The guest address is outside the tailnet range.", status=422, code="invalid_guest_ip")
    try:
        socket.inet_aton(value)
    except OSError as exc:
        raise ConsoleOrchestrationError("The guest address is invalid.", status=422, code="invalid_guest_ip") from exc
    return value


def _yaml_quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


@dataclass(frozen=True)
class MoonlightPlan:
    name: str
    guest_ip: str
    route_prefix: str
    compose: str
    config: str
    data: str


class MoonlightOrchestrator:
    """Manage one isolated Moonlight Web instance per EpicVM guest."""

    backend = "moonlight"

    def __init__(
        self,
        *,
        root: str = "/opt/epicvm/moonlight-instances",
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
        http_request: Callable[..., Any] | None = None,
    ):
        self.root = Path(root)
        self.proxy_network = str(proxy_network or "proxy")
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
        # Tests inject this.  Production uses urllib directly with a private
        # Docker-network address and a short timeout.
        self.http_request = http_request

    def _image(self) -> str:
        if "moonlight" in self.digests:
            value = str(self.digests.get("moonlight") or "")
        else:
            value = str(os.environ.get("EPICVM_MOONLIGHT_IMAGE", "") or DEFAULT_MOONLIGHT_IMAGE)
        if not SHA256_IMAGE_RE.fullmatch(value):
            raise ConsoleOrchestrationError("Digest-pinned moonlight image is not configured.", status=503, code="digest_required")
        return value

    def _routing_config(self) -> tuple[str, str, int]:
        if (
            not re.fullmatch(r"[a-z0-9.-]+", self.public_host)
            or not self.tls_resolver
            or not self.router_priority.isdigit()
        ):
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
        used = ((usage.total - usage.free) / usage.total * 100) if usage.total else 100
        return usage.free >= 20 * 1024**3 and used < 85

    def _route_available(self, route_prefix: str) -> bool:
        try:
            listed = self.command_runner(["docker", "ps", "-aq"], check=True, capture_output=True, text=True)
            ids = str(getattr(listed, "stdout", "") or "").split()
            if not ids:
                return True
            inspected = self.command_runner(["docker", "inspect", *ids], check=True, capture_output=True, text=True)
            records = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))
            variants = (route_prefix, route_prefix.rstrip("/"))
            for record in records:
                labels = (((record or {}).get("Config") or {}).get("Labels") or {})
                rules = [str(v) for k, v in labels.items() if str(k).startswith("traefik.http.routers.") and str(k).endswith(".rule")]
                if any(any(variant in rule for variant in variants) for rule in rules):
                    return False
            return True
        except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConsoleOrchestrationError("Traefik route ownership could not be verified.", status=503, code="route_probe_failed") from exc

    def _routing_available(self) -> bool:
        _, resolver, _ = self._routing_config()
        try:
            self.command_runner(["docker", "network", "inspect", self.proxy_network], check=True, capture_output=True, text=True)
            listed = self.command_runner(["docker", "ps", "-q"], check=True, capture_output=True, text=True)
            ids = str(getattr(listed, "stdout", "") or "").split()
            if not ids:
                return False
            inspected = self.command_runner(["docker", "inspect", *ids], check=True, capture_output=True, text=True)
            records = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))
            dashboard = any(
                str((record or {}).get("Name") or "").lstrip("/") == "blobedash"
                and self.proxy_network in (((record or {}).get("NetworkSettings") or {}).get("Networks") or {})
                for record in records
            )
            secure_route = any(
                any(str(k).endswith(".tls.certresolver") and str(v) == resolver for k, v in (((record or {}).get("Config") or {}).get("Labels") or {}).items())
                for record in records
            )
            return dashboard and secure_route
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
            return str(exc.headers.get("Location") or "").split("?", 1)[0].endswith("/portal/login")
        except (OSError, urlerror.URLError):
            return False

    def _instance_root(self, name: str) -> Path:
        return self.root / validate_vm_name(name)

    @staticmethod
    def _project_name(name: str) -> str:
        return f"epicvm-{validate_vm_name(name).replace('.', '-')}-moonlight"

    def build_config(self, *, name: str) -> str:
        safe = validate_vm_name(name)
        value = {
            "data_storage": {"type": "json", "path": "server/data.json", "session_expiration_check_interval": {"secs": 300, "nanos": 0}},
            "webrtc": {"ice_servers": [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:3478"], "username": "", "credential": ""}], "ice_server_script": None, "port_range": {"min": 41000, "max": 41010}, "nat_1to1": None, "network_types": ["udp4"], "include_loopback_candidates": False},
            "web_server": {"bind_address": "0.0.0.0:8080", "url_path_prefix": f"/vm/{safe}", "session_cookie_secure": True, "session_cookie_expiration": {"secs": 86400, "nanos": 0}, "first_login_create_admin": True, "first_login_assign_global_hosts": True, "default_user_id": None, "default_role_id": None, "forwarded_header": {"username_header": "X-EpicVM-User", "auto_create_missing_user": True, "ignore_case": True}},
            "moonlight": {"default_http_port": 47989, "pair_device_name": "EpicVM Web"},
            "streamer_path": "./streamer",
            "log": {"level_filter": "INFO", "file_path": None, "dev_venator": False},
            "default_settings": None,
        }
        return json.dumps(value, separators=(",", ":")) + "\n"

    def build_compose(self, *, name: str) -> str:
        safe = validate_vm_name(name)
        public_host, resolver, priority = self._routing_config()
        image = self._image()
        # Console authentication is per-VM and must go through the dashboard's
        # VM-session endpoint.  Do not inherit the legacy global middleware:
        # on the KVM host that value can point at the testre BasicAuth file,
        # which causes a second, unrelated browser credential prompt.
        auth_identity = f"epicvm-{safe}-portal-auth"
        identity = f"epicvm-{safe}-portal-user"
        labels = {
            "traefik.enable": "true",
            "com.blobevm.managed": "1",
            "com.epicvm.console": "moonlight",
            "com.epicvm.vm.name": safe,
            "traefik.docker.network": self.proxy_network,
            f"traefik.http.routers.epicvm-{safe}.rule": f"Host(`{public_host}`) && PathPrefix(`/vm/{safe}/`)",
            f"traefik.http.routers.epicvm-{safe}.entrypoints": "websecure",
            f"traefik.http.routers.epicvm-{safe}.tls": "true",
            f"traefik.http.routers.epicvm-{safe}.tls.certresolver": resolver,
            f"traefik.http.routers.epicvm-{safe}.priority": str(priority),
            f"traefik.http.routers.epicvm-{safe}.service": f"epicvm-{safe}",
            f"traefik.http.routers.epicvm-{safe}.middlewares": f"{auth_identity},{identity}",
            f"traefik.http.middlewares.{auth_identity}.forwardauth.address": f"http://blobedash:5000/dashboard/auth/vm/{safe}",
            f"traefik.http.middlewares.{auth_identity}.forwardauth.trustForwardHeader": "true",
            f"traefik.http.middlewares.{identity}.headers.customrequestheaders.X-EpicVM-User": safe,
            f"traefik.http.services.epicvm-{safe}.loadbalancer.server.port": "8080",
        }
        lines = "\n".join(f"      {key}: {_yaml_quote(value)}" for key, value in labels.items())
        return f'''services:
  moonlight-web:
    image: {_yaml_quote(image)}
    restart: unless-stopped
    environment:
      BIND_ADDRESS: "0.0.0.0:8080"
      PATH_PREFIX: "/vm/{safe}"
      WEBRTC_PORT_RANGE: "41000:41010"
    volumes:
      - ./server:/moonlight-web/server
    networks:
      - proxy
      - egress
    healthcheck:
      test: ["CMD-SHELL", "kill -0 1"]
      interval: 5s
      timeout: 3s
      retries: 30
      start_period: 10s
    labels:
{lines}
networks:
  proxy:
    external: true
  egress:
    driver: bridge
'''

    def build_plan(self, *, name: str, guest_ip: str) -> MoonlightPlan:
        safe = validate_vm_name(name)
        address = validate_guest_ip(guest_ip)
        for port in (47989, 47990):
            if not self.tcp_probe(address, port, 2.0):
                raise ConsoleOrchestrationError("Sunshine is not reachable from kvm2.", status=409, code="sunshine_tcp_unavailable")
        return MoonlightPlan(safe, address, f"/vm/{safe}/", self.build_compose(name=safe), self.build_config(name=safe), '{"version":"3","users":{},"hosts":{},"roles":{}}\n')

    def stage_plan(self, plan: MoonlightPlan) -> Path:
        target = self._instance_root(plan.name)
        if target.exists():
            raise ConsoleOrchestrationError("A console instance with this name already exists.", status=409, code="console_exists")
        if not self.disk_probe():
            raise ConsoleOrchestrationError("kvm2 storage is below the provisioning safety threshold.", status=507, code="storage_gate")
        if not self.routing_probe():
            raise ConsoleOrchestrationError("The verified Traefik authentication or TLS route is unavailable.", status=503, code="routing_probe_failed")
        if not self.route_owner_probe(plan.route_prefix):
            raise ConsoleOrchestrationError("The requested console route is already owned.", status=409, code="route_collision")
        target.parent.mkdir(parents=True, exist_ok=True)
        # The pinned image runs as uid/gid 999.  Keep the bundle private from
        # ordinary users while allowing only that service account to traverse
        # and update Moonlight's client-key database.
        try:
            os.chmod(target.parent, 0o750)
            os.chown(target.parent, 999, 999)
        except (AttributeError, OSError):
            pass
        stage = target.parent / f".{plan.name}-{secrets.token_hex(8)}"
        stage.mkdir(mode=0o750)
        try:
            (stage / "docker-compose.yml").write_text(plan.compose, encoding="utf-8")
            server = stage / "server"
            server.mkdir(mode=0o700)
            (server / "config.json").write_text(plan.config, encoding="utf-8")
            (server / "data.json").write_text(plan.data, encoding="utf-8")
            (stage / "plan.json").write_text(json.dumps({"owner": "EpicVM", "version": 1, "backend": "moonlight", "name": plan.name, "guestIp": plan.guest_ip, "routePrefix": plan.route_prefix, "paired": False}, separators=(",", ":")), encoding="utf-8")
            os.chmod(stage / "docker-compose.yml", 0o600)
            os.chmod(server, 0o750)
            os.chmod(server / "config.json", 0o640)
            os.chmod(server / "data.json", 0o640)
            os.chmod(stage / "plan.json", 0o600)
            stage.rename(target)
            try:
                os.chown(target, 999, 999)
                os.chown(target / "server", 999, 999)
                os.chown(target / "server" / "config.json", 999, 999)
                os.chown(target / "server" / "data.json", 999, 999)
            except (AttributeError, OSError):
                pass
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

    def _read_plan(self, name: str) -> dict[str, Any]:
        safe = validate_vm_name(name)
        target = self._instance_root(safe)
        try:
            value = json.loads((target / "plan.json").read_text(encoding="utf-8"))
            if value.get("owner") != "EpicVM" or value.get("backend") != "moonlight" or value.get("name") != safe:
                raise ValueError("ownership")
            return value
        except Exception as exc:
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required") from exc

    def _runtime_isolated(self, name: str) -> bool:
        project = self._project_name(name)
        try:
            listed = self.command_runner(["docker", "ps", "--filter", f"label=com.docker.compose.project={project}", "-q"], check=True, capture_output=True, text=True)
            ids = str(getattr(listed, "stdout", "") or "").split()
            if len(ids) != 1:
                return False
            inspected = self.command_runner(["docker", "inspect", *ids], check=True, capture_output=True, text=True)
            record = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))[0]
            labels = (((record or {}).get("Config") or {}).get("Labels") or {})
            if str(labels.get("com.docker.compose.service") or "") != "moonlight-web":
                return False
            if (((record or {}).get("HostConfig") or {}).get("PortBindings") or {}):
                return False
            networks = set((((record or {}).get("NetworkSettings") or {}).get("Networks") or {}).keys())
            egress = any(str(value) == "egress" or str(value).endswith("_egress") for value in networks)
            return self.proxy_network in networks and egress
        except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _container_url(self, name: str) -> str:
        project = self._project_name(name)
        listed = self.command_runner(["docker", "ps", "--filter", f"label=com.docker.compose.project={project}", "-q"], check=True, capture_output=True, text=True)
        ids = str(getattr(listed, "stdout", "") or "").split()
        if len(ids) != 1:
            raise ConsoleOrchestrationError("The Moonlight service is not running.", status=502, code="console_runtime_missing")
        inspected = self.command_runner(["docker", "inspect", ids[0]], check=True, capture_output=True, text=True)
        records = json.loads(str(getattr(inspected, "stdout", "[]") or "[]"))
        networks = (((records[0] if records else {}) or {}).get("NetworkSettings") or {}).get("Networks") or {}
        address = str((networks.get(self.proxy_network) or {}).get("IPAddress") or "")
        if not address:
            raise ConsoleOrchestrationError("The Moonlight service address is unavailable.", status=502, code="console_runtime_missing")
        # Moonlight's API is mounted below the configured path prefix.  Keep
        # the internal URL path-correct; hitting the container root happens to
        # serve the UI but leaves the pairing endpoints at 404.
        return f"http://{address}:8080/vm/{validate_vm_name(name)}"

    def start_staged(self, name: str) -> dict[str, Any]:
        plan = self._read_plan(name)
        target = self._instance_root(name)
        if not self.tcp_probe(str(plan["guestIp"]), 47989, 2.0):
            raise ConsoleOrchestrationError("Sunshine is not reachable from kvm2.", status=409, code="sunshine_tcp_unavailable")
        try:
            self.command_runner(["docker", "compose", "-p", self._project_name(name), "up", "-d", "--wait", "--wait-timeout", "90"], cwd=str(target), check=True, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self.stop_staged(name)
            raise ConsoleOrchestrationError("The Moonlight stack failed its startup gate.", status=502, code="console_start_failed") from exc
        if not self._runtime_isolated(name):
            self.stop_staged(name)
            raise ConsoleOrchestrationError("The Moonlight stack exposed an internal service.", status=502, code="console_isolation_failed")
        auth_rejected = False
        for _ in range(10):
            if self.auth_status_probe(str(plan["routePrefix"])):
                auth_rejected = True
                break
            time.sleep(1)
        if not auth_rejected:
            self.stop_staged(name)
            raise ConsoleOrchestrationError("The public console route did not reject unauthenticated access.", status=502, code="console_auth_failed")
        return {"ok": True, "routePrefix": str(plan["routePrefix"]), "guestTcpVerified": True}

    def _http(self, method: str, url: str, *, headers: Mapping[str, str] | None = None, body: bytes | None = None, timeout: float = 15.0):
        if self.http_request is not None:
            return self.http_request(method, url, headers=dict(headers or {}), body=body, timeout=timeout)
        req = urlrequest.Request(url, data=body, headers=dict(headers or {}), method=method)
        if url.lower().startswith("https://"):
            return urlrequest.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
        return urlrequest.urlopen(req, timeout=timeout)

    @staticmethod
    def _json_line(response) -> Any:
        raw = response.readline()
        if not raw:
            raise ConsoleOrchestrationError("Moonlight pairing returned no response.", status=502, code="moonlight_pair_failed")
        try:
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConsoleOrchestrationError("Moonlight pairing returned an invalid response.", status=502, code="moonlight_pair_failed") from exc

    def _hosts(self, base: str, user: str) -> list[dict[str, Any]]:
        response = self._http("GET", base + "/api/hosts", headers={"X-EpicVM-User": user}, timeout=15)
        try:
            raw = response.read()
        finally:
            try:
                response.close()
            except Exception:
                pass
        if isinstance(raw, bytes):
            text = raw.decode("utf-8")
        else:
            text = str(raw or "")
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ConsoleOrchestrationError("Moonlight returned an invalid host list.", status=502, code="moonlight_host_failed") from exc
            if isinstance(value, dict) and isinstance(value.get("hosts"), list):
                records.extend(item for item in value["hosts"] if isinstance(item, dict))
            elif isinstance(value, dict):
                records.append(value)
            elif isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
        return records

    @staticmethod
    def _coerce_host_id(value: Any) -> int:
        try:
            host_id = int(str(value or "").strip(), 10)
        except (TypeError, ValueError) as exc:
            raise ConsoleOrchestrationError("Moonlight returned an invalid guest host id.", status=502, code="moonlight_host_failed") from exc
        if host_id < 0 or host_id > 0xFFFFFFFF:
            raise ConsoleOrchestrationError("Moonlight returned an invalid guest host id.", status=502, code="moonlight_host_failed")
        return host_id

    def _register_host(self, base: str, user: str, guest_ip: str) -> int:
        for host in self._hosts(base, user):
            if str(host.get("address") or "") == guest_ip and str(host.get("http_port") or "47989") == "47989":
                return self._coerce_host_id(host.get("host_id"))
        body = json.dumps({"address": guest_ip, "http_port": 47989}, separators=(",", ":")).encode("utf-8")
        response = self._http("POST", base + "/api/host", headers={"Content-Type": "application/json", "X-EpicVM-User": user}, body=body, timeout=15)
        try:
            raw = response.read()
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "")
            try:
                payload = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = json.loads(next((line for line in text.splitlines() if line.strip()), ""))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConsoleOrchestrationError("Moonlight could not register the guest.", status=502, code="moonlight_host_failed") from exc
        finally:
            try:
                response.close()
            except Exception:
                pass
        host = payload.get("host") if isinstance(payload, dict) else None
        host_id = (host or {}).get("host_id") if isinstance(host, dict) else None
        if host_id in (None, ""):
            raise ConsoleOrchestrationError("Moonlight did not return a guest host id.", status=502, code="moonlight_host_failed")
        return self._coerce_host_id(host_id)

    def _sunshine_pair(self, guest_ip: str, username: str, password: str, pin: str, vm_name: str) -> None:
        if not username or not password:
            raise ConsoleOrchestrationError("Sunshine credentials are required for pairing.", status=400, code="sunshine_credentials_required")
        raw = f"{username}:{password}".encode("utf-8")
        auth = base64.b64encode(raw).decode("ascii")
        body = json.dumps({"pin": pin, "name": f"EpicVM {validate_vm_name(vm_name)}"}, separators=(",", ":")).encode("utf-8")
        deadline = time.monotonic() + 20.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConsoleOrchestrationError("Sunshine did not accept the pairing request in time.", status=502, code="sunshine_pair_failed")
            response = None
            try:
                response = self._http(
                    "POST",
                    f"https://{guest_ip}:47990/api/pin",
                    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                    body=body,
                    timeout=min(5.0, remaining),
                )
                raw_response = response.read()
                text = raw_response.decode("utf-8") if isinstance(raw_response, bytes) else str(raw_response or "")
                try:
                    payload = json.loads(text)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ConsoleOrchestrationError("Sunshine returned an invalid pairing response.", status=502, code="sunshine_pair_failed") from exc
                if isinstance(payload, dict) and payload.get("status") is True:
                    return
                if not (isinstance(payload, dict) and payload.get("status") is False):
                    raise ConsoleOrchestrationError("Sunshine did not accept the pairing request.", status=502, code="sunshine_pair_failed")
            except urlerror.HTTPError as exc:
                code = "sunshine_auth_failed" if int(exc.code) in (401, 403) else "sunshine_pair_failed"
                raise ConsoleOrchestrationError("Sunshine rejected the pairing request.", status=409 if code == "sunshine_auth_failed" else 502, code=code) from exc
            except (OSError, urlerror.URLError) as exc:
                raise ConsoleOrchestrationError("Sunshine pairing could not be completed.", status=502, code="sunshine_pair_failed") from exc
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConsoleOrchestrationError("Sunshine did not accept the pairing request in time.", status=502, code="sunshine_pair_failed")
            time.sleep(min(0.25, remaining))

    def pair_staged(self, name: str, *, sunshine_username: str, sunshine_password: str) -> dict[str, Any]:
        plan = self._read_plan(name)
        if plan.get("paired") is True:
            return {"ok": True, "paired": True, "routePrefix": str(plan["routePrefix"]), "guestTcpVerified": True}
        base = self._container_url(name)
        user = validate_vm_name(name)
        host_id = self._register_host(base, user, str(plan["guestIp"]))
        if not host_id:
            raise ConsoleOrchestrationError("Moonlight did not return a guest host id.", status=502, code="moonlight_host_failed")
        headers = {"Content-Type": "application/json", "X-EpicVM-User": user}
        body = json.dumps({"host_id": host_id}, separators=(",", ":")).encode("utf-8")
        try:
            response = self._http("POST", base + "/api/pair", headers=headers, body=body, timeout=120)
            try:
                first = self._json_line(response)
                pin = str(first.get("Pin") or "") if isinstance(first, dict) else ""
                if not pin:
                    raise ConsoleOrchestrationError("Moonlight did not provide a pairing PIN.", status=502, code="moonlight_pair_failed")
                self._sunshine_pair(str(plan["guestIp"]), str(sunshine_username), str(sunshine_password), pin, str(plan["name"]))
                second = self._json_line(response)
                paired = isinstance(second, dict) and str(second.get("Paired") or "")
                if not paired:
                    raise ConsoleOrchestrationError("Moonlight did not confirm pairing.", status=502, code="moonlight_pair_failed")
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        except ConsoleOrchestrationError:
            raise
        except urlerror.HTTPError as exc:
            raise ConsoleOrchestrationError("Moonlight pairing was rejected.", status=409 if int(exc.code) in (401, 403) else 502, code="moonlight_pair_failed") from exc
        except (OSError, urlerror.URLError) as exc:
            raise ConsoleOrchestrationError("Moonlight pairing could not be completed.", status=502, code="moonlight_pair_failed") from exc
        target = self._instance_root(name)
        plan["paired"] = True
        plan["pairedAt"] = int(time.time())
        (target / "plan.json").write_text(json.dumps(plan, separators=(",", ":")), encoding="utf-8")
        os.chmod(target / "plan.json", 0o600)
        sunshine_username = sunshine_password = ""
        return {"ok": True, "paired": True, "routePrefix": str(plan["routePrefix"]), "guestTcpVerified": True}

    def has_auto_login(self, name: str) -> bool:
        try:
            return bool(self._read_plan(name).get("paired"))
        except ConsoleOrchestrationError:
            return False

    def enable_auto_login(self, *, name: str, username: str, password: str, sunshine_username: str | None = None, sunshine_password: str | None = None) -> None:
        self.pair_staged(name, sunshine_username=str(sunshine_username or username), sunshine_password=str(sunshine_password or password))

    def build_json_auth_data(self, name: str, **_kwargs) -> str:
        # Compatibility hook for older callers.  Moonlight uses the dashboard
        # route and its forwarded user header; it has no Guacamole assertion.
        if not self.has_auto_login(name):
            raise ConsoleOrchestrationError("The Moonlight console is not paired.", status=409, code="console_not_paired")
        return ""

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
        staged = self._read_plan(safe)
        if not target.is_dir() or not (target / "docker-compose.yml").is_file() or staged.get("name") != safe:
            raise ConsoleOrchestrationError("The named console instance is not owned by EpicVM.", status=403, code="ownership_required")
        self.command_runner(["docker", "compose", "-p", self._project_name(safe), "down", "--remove-orphans"], cwd=str(target), check=True, capture_output=True, text=True)
        if device_id and revoke is not None:
            revoke(device_id)
        quarantine = target.parent / "quarantine" / f"{safe}-{int(time.time())}"
        quarantine.parent.mkdir(mode=0o700, exist_ok=True)
        target.rename(quarantine)
        return {"ok": True, "name": safe, "quarantineUntil": int(time.time()) + 7 * 86400}
