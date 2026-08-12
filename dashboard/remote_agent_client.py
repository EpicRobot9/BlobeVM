"""HTTP client and provider adapter for RemoteVM host agents.

The dashboard owns orchestration and inventory; this module is the deliberately
small transport boundary.  No virtualization-specific commands belong here.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

try:
    from .vm_hosts import VmHostUnavailable
except ImportError:  # pragma: no cover - direct script/module loading
    from vm_hosts import VmHostUnavailable


class RemoteAgentError(RuntimeError):
    """A transport or agent-level failure, with no secret material in the text."""

    def __init__(self, message: str, *, status: int | None = None, data: Any = None):
        super().__init__(message)
        self.status = status
        self.data = data


@dataclass
class RemoteOperationResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    request_id: str = ""


class RemoteAgentClient:
    """Small JSON-over-HTTP client for the Windows/Linux host agent."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 2.0,
        opener: Callable[..., Any] | None = None,
    ):
        self.base_url = str(base_url).rstrip("/") + "/"
        self.token = str(token)
        self.timeout = max(0.5, float(timeout))
        # Full-copy Windows templates can take several minutes on real Hyper-V
        # storage. The create response carries the one-time claim token, so an
        # upstream timeout must never discard a still-running create response.
        self.operation_timeout = max(self.timeout, 600.0)
        self._opener = opener or urlopen

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        body = None
        request_id = ""
        headers = {"Accept": "application/json", "User-Agent": "EpicVM-RemoteHost/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)[:128]
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method.upper())
        try:
            with self._opener(request, timeout=timeout if timeout is not None else self.timeout) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
                response_headers = getattr(response, "headers", None)
                request_id = str(response_headers.get("X-Request-Id", "") or "") if response_headers is not None else ""
        except HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            response_headers = getattr(exc, "headers", None)
            request_id = str(response_headers.get("X-Request-Id", "") or "") if response_headers is not None else ""
            data = self._decode(raw)
            message = self._error_message(data, f"remote agent returned HTTP {exc.code}")
            raise RemoteAgentError(message, status=int(exc.code), data=data) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RemoteAgentError(f"remote agent unavailable: {exc}") from exc
        data = self._decode(raw)
        if request_id and isinstance(data, dict):
            data.setdefault("_request_id", request_id)
        if status >= 400:
            raise RemoteAgentError(self._error_message(data, f"remote agent returned HTTP {status}"), status=status, data=data)
        if isinstance(data, dict) and data.get("ok") is False:
            raise RemoteAgentError(self._error_message(data, "remote agent rejected the request"), status=status, data=data)
        return data

    @staticmethod
    def _decode(raw: bytes | str) -> Any:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            raise RemoteAgentError("remote agent returned invalid JSON")

    @staticmethod
    def _error_message(data: Any, fallback: str) -> str:
        if isinstance(data, dict):
            for key in ("error", "message", "detail"):
                value = data.get(key)
                if value:
                    return str(value)[:500]
        return fallback

    def health(self) -> dict[str, Any]:
        result = self._request("GET", "/v1/health")
        return result if isinstance(result, dict) else {"ok": True, "value": result}

    def capabilities(self) -> dict[str, Any]:
        result = self._request("GET", "/v1/capabilities")
        return result if isinstance(result, dict) else {}

    def list_vms(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/v1/vms")
        if isinstance(result, dict):
            result = result.get("vms", result.get("instances", result.get("items", [])))
        if not isinstance(result, list):
            raise RemoteAgentError("remote agent returned an invalid VM inventory")
        return [dict(item) for item in result if isinstance(item, Mapping)]

    def status(self, name: str) -> dict[str, Any]:
        safe_name = quote(str(name), safe="")
        result = self._request("GET", f"/v1/vms/{safe_name}")
        return result if isinstance(result, dict) else {"status": result}

    def logs(self, name: str, *, tail: int = 400) -> str:
        safe_name = quote(str(name), safe="")
        result = self._request("GET", f"/v1/vms/{safe_name}/logs?tail={max(1, min(int(tail), 2000))}")
        if isinstance(result, dict):
            return str(result.get("logs", result.get("output", "")) or "")
        return str(result or "")

    def create(
        self,
        name: str,
        spec: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> RemoteOperationResult:
        payload = {"name": name}
        if spec:
            payload.update(dict(spec))
        result = self._request(
            "POST",
            "/v1/vms",
            payload,
            idempotency_key=idempotency_key or uuid.uuid4().hex,
            timeout=self.operation_timeout,
        )
        return self._result(result)

    def provision(self, name: str, profile: str = "standard", *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Create a validated full-copy VM provisioning job."""
        result = self._request(
            "POST", "/v1/provisioning-jobs", {"name": str(name), "profile": str(profile)},
            idempotency_key=idempotency_key or uuid.uuid4().hex, timeout=self.operation_timeout,
        )
        return result if isinstance(result, dict) else {"ok": True, "job": result}

    def provisioning_status(self, job_id: str) -> dict[str, Any]:
        safe_id = quote(str(job_id), safe="")
        result = self._request("GET", f"/v1/provisioning-jobs/{safe_id}")
        return result if isinstance(result, dict) else {"job": result}

    def claim(self, job_id: str, username: str, password: str, claim_token: str) -> dict[str, Any]:
        """Forward a one-time claim without persisting or logging credentials."""
        safe_id = quote(str(job_id), safe="")
        payload = {"username": str(username), "password": str(password), "claimToken": str(claim_token)}
        result = self._request("POST", f"/v1/provisioning-jobs/{safe_id}/claim", payload, timeout=self.operation_timeout)
        return result if isinstance(result, dict) else {"ok": True, "job": result}

    def console_complete(self, job_id: str, *, route_prefix: str, guest_tcp_verified: bool) -> dict[str, Any]:
        safe_id = quote(str(job_id), safe="")
        payload = {"routePrefix": str(route_prefix), "guestTcpVerified": bool(guest_tcp_verified)}
        result = self._request("POST", f"/v1/provisioning-jobs/{safe_id}/console-complete", payload, timeout=self.operation_timeout)
        return result if isinstance(result, dict) else {"ok": True, "job": result}

    def console_credentials(
        self,
        job_id: str,
        *,
        guest_username: str,
        guest_password: str,
        sunshine_username: str,
        sunshine_password: str,
    ) -> dict[str, Any]:
        """Send request-only guest/Sunshine credentials to the Windows agent."""
        safe_id = quote(str(job_id), safe="")
        payload = {
            "username": str(guest_username),
            "password": str(guest_password),
            "sunshineUsername": str(sunshine_username),
            "sunshinePassword": str(sunshine_password),
        }
        result = self._request(
            "POST",
            f"/v1/provisioning-jobs/{safe_id}/console-credentials",
            payload,
            timeout=self.operation_timeout,
        )
        return result if isinstance(result, dict) else {"ok": True, "job": result}

    def console_failed(self, job_id: str, *, code: str = "console_failed") -> dict[str, Any]:
        safe_id = quote(str(job_id), safe="")
        result = self._request("POST", f"/v1/provisioning-jobs/{safe_id}/console-failed", {"code": str(code)}, timeout=self.operation_timeout)
        return result if isinstance(result, dict) else {"ok": True, "job": result}

    def deprovision(self, name: str, *, confirm_name: str, idempotency_key: str | None = None) -> dict[str, Any]:
        result = self._request(
            "POST", "/v1/deprovisioning-jobs", {"name": str(name), "confirmName": str(confirm_name)},
            idempotency_key=idempotency_key or uuid.uuid4().hex, timeout=self.operation_timeout,
        )
        return result if isinstance(result, dict) else {"ok": True, "job": result}

    def deprovisioning_status(self, job_id: str) -> dict[str, Any]:
        safe_id = quote(str(job_id), safe="")
        result = self._request("GET", f"/v1/deprovisioning-jobs/{safe_id}")
        return result if isinstance(result, dict) else {"job": result}

    def lifecycle(self, action: str, name: str, *, idempotency_key: str | None = None, **options: Any) -> RemoteOperationResult:
        action = str(action).lower()
        safe_name = quote(str(name), safe="")
        if action not in {"start", "stop", "restart", "delete"}:
            raise RemoteAgentError(f"unsupported remote VM action: {action}")
        request_key = idempotency_key or uuid.uuid4().hex
        if action == "delete":
            result = self._request("DELETE", f"/v1/vms/{safe_name}", idempotency_key=request_key, timeout=self.operation_timeout)
        else:
            result = self._request(
                "POST",
                f"/v1/vms/{safe_name}/{action}",
                options or {},
                idempotency_key=request_key,
                timeout=self.operation_timeout,
            )
        return self._result(result)

    @staticmethod
    def _result(data: Any) -> RemoteOperationResult:
        if not isinstance(data, dict):
            return RemoteOperationResult(stdout=json.dumps(data))
        return RemoteOperationResult(
            returncode=int(data.get("returncode", 0 if data.get("ok", True) else 1)),
            stdout=str(data.get("stdout", data.get("message", "")) or ""),
            stderr=str(data.get("stderr", data.get("error", "")) or ""),
            request_id=str(data.get("_request_id", data.get("request_id", "")) or ""),
        )


class RemoteAgentHost:
    """Provider adapter implementing the local provider's manager-shaped API."""

    kind = "remote"

    def __init__(self, config: Mapping[str, Any], *, client: RemoteAgentClient | None = None):
        self.config = dict(config)
        self.host_id = str(self.config["id"])
        self.host_name = str(self.config.get("display_name") or self.host_id)
        self.provider = str(self.config.get("provider") or "unknown")
        self.platform = str(self.config.get("platform") or "windows")
        self.agent_url = str(self.config.get("agent_url") or "")
        self.client = client or RemoteAgentClient(
            self.agent_url,
            str(self.config.get("token") or ""),
            timeout=float(self.config.get("timeout", 2.0)),
        )
        self._probe_cache: tuple[float, dict[str, Any]] | None = None

    @property
    def id(self) -> str:
        return self.host_id

    def provision(self, name: str, profile: str = "standard", *, idempotency_key: str | None = None) -> dict[str, Any]:
        try:
            return self.client.provision(name, profile, idempotency_key=idempotency_key)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def provisioning_status(self, job_id: str) -> dict[str, Any]:
        try:
            return self.client.provisioning_status(job_id)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def claim(self, job_id: str, username: str, password: str, claim_token: str) -> dict[str, Any]:
        try:
            return self.client.claim(job_id, username, password, claim_token)
        except RemoteAgentError as exc:
            # Do not reflect transport/error text from a credential-bearing
            # request; retain the normalized status/code for the API layer.
            status = int(exc.status or 503)
            code = {400: "invalid_request", 401: "authentication_failed", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "claim_failed"}.get(status, "host_unavailable")
            raise VmHostUnavailable("Guest claim was rejected.", status=status, code=code) from exc

    def console_complete(self, job_id: str, *, route_prefix: str, guest_tcp_verified: bool) -> dict[str, Any]:
        try:
            return self.client.console_complete(job_id, route_prefix=route_prefix, guest_tcp_verified=guest_tcp_verified)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def console_credentials(
        self,
        job_id: str,
        *,
        guest_username: str,
        guest_password: str,
        sunshine_username: str,
        sunshine_password: str,
    ) -> dict[str, Any]:
        try:
            return self.client.console_credentials(
                job_id,
                guest_username=guest_username,
                guest_password=guest_password,
                sunshine_username=sunshine_username,
                sunshine_password=sunshine_password,
            )
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def console_failed(self, job_id: str, *, code: str = "console_failed") -> dict[str, Any]:
        try:
            return self.client.console_failed(job_id, code=code)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def deprovision(self, name: str, *, confirm_name: str, idempotency_key: str | None = None) -> dict[str, Any]:
        try:
            return self.client.deprovision(name, confirm_name=confirm_name, idempotency_key=idempotency_key)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def deprovisioning_status(self, job_id: str) -> dict[str, Any]:
        try:
            return self.client.deprovisioning_status(job_id)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    @property
    def online(self) -> bool:
        return bool(self._probe().get("online"))

    def _probe(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._probe_cache and now - self._probe_cache[0] < 5:
            return dict(self._probe_cache[1])
        try:
            health = self.client.health()
            caps = self.client.capabilities()
            capabilities = caps.get("capabilities", caps) if isinstance(caps, dict) else {}
            resources = caps.get("resources", {}) if isinstance(caps, dict) else {}
            result = {
                "online": bool(health.get("ok", True)) if isinstance(health, dict) else True,
                "capabilities": self._normalize_capabilities(capabilities),
                "resources": resources if isinstance(resources, dict) else {},
                "last_error": "",
            }
        except RemoteAgentError as exc:
            result = {
                "online": False,
                "capabilities": self._normalize_capabilities({}),
                "resources": {},
                "last_error": str(exc),
            }
        self._probe_cache = (now, result)
        return dict(result)

    @staticmethod
    def _normalize_capabilities(value: Mapping[str, Any] | None) -> dict[str, Any]:
        value = value if isinstance(value, Mapping) else {}
        result: dict[str, Any] = {
            "create_vm": False,
            "start": False,
            "stop": False,
            "restart": False,
            "delete": False,
            "console": False,
            "provisioning": False,
        }
        if value.get("available") is False:
            return result
        aliases = {
            "create": "create_vm",
            "create_vm": "create_vm",
            "start": "start",
            "stop": "stop",
            "restart": "restart",
            "delete": "delete",
            "console": "console",
            "provisioning": "provisioning",
        }
        for key, output in aliases.items():
            if key in value:
                result[output] = bool(value[key])
        if "gaming_provisioning" in value:
            result["gaming_provisioning"] = bool(value["gaming_provisioning"])
        checks = value.get("provisioningChecks")
        if isinstance(checks, Mapping):
            result["provisioningChecks"] = {
                str(key): bool(checks[key])
                for key in (
                    "template",
                    "bootstrapCredential",
                    "tailscaleOAuthClient",
                    "tailscaleTailnet",
                    "tailscaleOAuthSecret",
                    "gpuPartitionable",
                )
                if key in checks
            }
        features = value.get("features", [])
        if isinstance(features, str):
            features = [features]
        if isinstance(features, (list, tuple, set)):
            normalized = {str(item).lower() for item in features}
            if "create" in normalized:
                result["create_vm"] = True
            if "lifecycle" in normalized:
                for action in ("start", "stop", "restart"):
                    result[action] = True
            if "delete-owned" in normalized or "delete" in normalized:
                result["delete"] = True
        return result

    def public_record(self) -> dict[str, Any]:
        probe = self._probe()
        return {
            "id": self.host_id,
            "display_name": self.host_name,
            "kind": "remote",
            "platform": self.platform,
            "provider": self.provider,
            "agent_url": self.agent_url,
            "transport": "tailscale",
            "online": bool(probe["online"]),
            "capabilities": dict(probe["capabilities"]),
            "resources": dict(probe["resources"]),
            "last_error": probe.get("last_error", ""),
        }

    def list_vms(self) -> list[dict[str, Any]]:
        try:
            result = self.client.list_vms()
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc
        return self.normalize_inventory(result)

    @staticmethod
    def _host_error(exc: RemoteAgentError) -> VmHostUnavailable:
        status = int(exc.status or 503)
        if status >= 500:
            code = "host_unavailable"
        else:
            code = {
                400: "invalid_request",
                401: "authentication_failed",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
            }.get(status, "remote_request_failed")
        return VmHostUnavailable(str(exc), status=status, code=code)

    def normalize_inventory(self, instances: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for raw in instances:
            item = dict(raw)
            item.setdefault("name", "")
            item.setdefault("status", item.get("state", "unknown"))
            item.setdefault("url", item.get("url", ""))
            item.update({
                "placement": "remote",
                "host_id": self.host_id,
                "host_name": self.host_name,
                "provider": self.provider,
                "host_online": self.online,
            })
            result.append(item)
        return result

    def create(self, name: str, spec: Mapping[str, Any] | None = None, **options: Any) -> RemoteOperationResult:
        idempotency_key = options.pop("idempotency_key", None)
        payload = dict(spec) if spec is not None else dict(options)
        try:
            return self.client.create(name, payload, idempotency_key=idempotency_key)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    @staticmethod
    def _request_options(options: Mapping[str, Any]) -> dict[str, Any]:
        transport_keys = {"capture_output", "text", "timeout", "check", "encoding", "errors"}
        return {key: value for key, value in options.items() if key not in transport_keys and key != "idempotency_key"}

    def run_manager(self, *args: Any, **options: Any) -> RemoteOperationResult:
        if not args:
            return RemoteOperationResult(returncode=2, stderr="missing remote action")
        action = str(args[0])
        name = str(args[1]) if len(args) > 1 else ""
        request_options = self._request_options(options)
        idempotency_key = options.get("idempotency_key")
        if action == "create":
            return self.create(name, request_options, idempotency_key=idempotency_key)
        try:
            return self.client.lifecycle(action, name, idempotency_key=idempotency_key, **request_options)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def check_call(self, action: str, name: str, **options: Any) -> None:
        result = self.run_manager(action, name, **options)
        if result.returncode != 0:
            raise RemoteAgentError(result.stderr or result.stdout or f"remote {action} failed")

    def command(self, *args: Any) -> list[str]:
        return [self.agent_url, *map(str, args)]

    def check_output(self, action: str, name: str, **options: Any) -> str:
        if action == "url":
            return ""
        if action == "port":
            return ""
        raise RemoteAgentError(f"remote manager output is not supported for {action}")

    def health(self) -> dict[str, Any]:
        return self._probe(force=True)

    def status(self, name: str) -> dict[str, Any]:
        try:
            return self.client.status(name)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc

    def logs(self, name: str, *, tail: int = 400) -> str:
        try:
            return self.client.logs(name, tail=tail)
        except RemoteAgentError as exc:
            raise self._host_error(exc) from exc
