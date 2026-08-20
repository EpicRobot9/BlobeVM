"""Personal Cloud PC registry for EpicVM (bring-your-own Sunshine over Tailscale).

A Cloud PC is a user-owned record pointing at a PC that already runs Sunshine
and sits on the same Tailscale tailnet as the dashboard server.  The dashboard
stands up a per-PC Moonlight Web proxy (reusing MoonlightOrchestrator) and pairs
it to the user's Sunshine endpoint.  No EpicVM agent is installed on the PC.

Design constraints:
- ``create_cloudpc`` is record-only: it validates and persists the record but
  does NOT stage the Moonlight proxy.  That keeps enrollment testable without the
  user's PC being online.  The proxy is staged lazily on start/pair.
- Sunshine credentials are consumed by one pairing call and never stored.
- The registry file is 0600, mirroring remote-hosts.json.
- Records are owner-scoped: only the owner (and admins via SSO) may act on them.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .moonlight_orchestrator import MoonlightOrchestrator, ConsoleOrchestrationError
except ImportError:  # pragma: no cover - direct module loading
    from moonlight_orchestrator import MoonlightOrchestrator, ConsoleOrchestrationError


DEFAULT_PATH = "/opt/blobe-vm/cloud-pcs.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


class CloudPcConfigError(ValueError):
    """The Cloud PC configuration is malformed or unsafe."""


def cloud_pcs_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path:
        return Path(path)
    env_file = os.environ.get("EPICVM_CLOUD_PCS_FILE")
    if env_file:
        return Path(env_file)
    state = os.environ.get("BLOBEDASH_STATE")
    if state:
        return Path(state) / "cloud-pcs.json"
    return Path(DEFAULT_PATH)


def validate_tailnet_ip(value: Any) -> str:
    """Sunshine must be reachable by the dashboard server, which already lives on
    the tailnet (it reaches guest VMs at 100.x).  Reject anything outside CGNAT."""
    s = str(value or "").strip()
    try:
        addr = ipaddress.ip_address(s)
    except ValueError as exc:
        raise CloudPcConfigError("tailnet_ip must be a valid IP address") from exc
    if addr.version != 4 or addr not in TAILSCALE_NET:
        raise CloudPcConfigError("tailnet_ip must be a Tailscale (100.64.0.0/10) address")
    return s


def _normalize(raw: Mapping[str, Any], *, persisted: bool = False) -> dict[str, Any]:
    rid = str(raw.get("id") or "").strip().lower()
    if not ID_RE.fullmatch(rid):
        raise CloudPcConfigError("id must match [a-z0-9][a-z0-9._-]{0,62}")
    owner = str(raw.get("owner") or "").strip()
    if not owner:
        raise CloudPcConfigError("owner is required")
    try:
        tailnet_ip = validate_tailnet_ip(raw.get("tailnet_ip"))
    except CloudPcConfigError:
        if persisted:
            # A persisted record that fails re-validation (e.g. env changed) must
            # still load; surface the bad value but don't crash the listing.
            tailnet_ip = str(raw.get("tailnet_ip") or "").strip()
        else:
            raise
    return {
        "id": rid,
        "display_name": str(raw.get("display_name") or rid)[:120],
        "owner": owner,
        "tailnet_ip": tailnet_ip,
        "paired": bool(raw.get("paired", False)),
        "enabled": raw.get("enabled", True) is not False,
        "created_at": int(raw.get("created_at", 0) or 0),
    }


def _read_all(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    p = cloud_pcs_path(path)
    if not p.exists():
        return []
    try:
        if os.name != "nt" and stat.S_IMODE(p.stat().st_mode) & 0o077:
            raise CloudPcConfigError("cloud-pc registry must not be group/world readable")
        data = json.loads(p.read_text(encoding="utf-8"))
    except CloudPcConfigError:
        raise
    except OSError as exc:
        raise CloudPcConfigError(f"cannot read cloud-pc registry: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CloudPcConfigError(f"invalid cloud-pc registry JSON: {exc}") from exc
    raw = data if isinstance(data, list) else data.get("pcs", [])
    if not isinstance(raw, list):
        raise CloudPcConfigError("cloud-pc registry must be a list")
    return [_normalize(r, persisted=True) for r in raw if isinstance(r, Mapping)]


def _write(pcs: Iterable[Mapping[str, Any]], path: str | os.PathLike[str] | None = None) -> None:
    p = cloud_pcs_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cloud-pcs.", dir=str(p.parent), text=True)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(list(pcs), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, p)
        os.chmod(p, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def load_cloud_pcs(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in _read_all(path) if r.get("enabled", True) is not False]


def load_cloud_pc(name: str, path: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    safe = str(name or "").strip().lower()
    for r in _read_all(path):
        if r["id"] == safe:
            return dict(r)
    return None


def is_cloudpc(name: str, path: str | os.PathLike[str] | None = None) -> bool:
    return load_cloud_pc(name, path=path) is not None


def delete_cloud_pc(name: str, path: str | os.PathLike[str] | None = None) -> bool:
    """Remove the record (and stop its proxy if running). Returns True if removed."""
    safe = str(name or "").strip().lower()
    existing = _read_all(path)
    kept = [r for r in existing if r["id"] != safe]
    if len(kept) == len(existing):
        return False
    _write(kept, path)
    return True


def upsert_cloud_pc(raw: Mapping[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    rec = _normalize(raw)
    existing = _read_all(path)
    updated = [r for r in existing if r["id"] != rec["id"]]
    updated.append(rec)
    _write(updated, path)
    return dict(rec)


def generate_cloudpc_id(owner: str) -> str:
    return f"cloudpc-{str(owner).lower()}-{secrets.token_hex(3)}"


# --- Orchestrator-backed operations ------------------------------------------------

def _orchestrator() -> MoonlightOrchestrator:
    return MoonlightOrchestrator()


def _tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((str(host), int(port)), timeout=float(timeout)):
            return True
    except OSError:
        return False


def _proxy_running(orch: MoonlightOrchestrator, name: str) -> bool:
    try:
        return bool(orch._runtime_isolated(name))
    except Exception:
        return False


def create_cloudpc(
    *,
    owner: str,
    display_name: str,
    tailnet_ip: str,
    sunshine_username: str = "",
    sunshine_password: str = "",
) -> dict[str, Any]:
    """Record-only enrollment.  Does NOT stage the proxy (keeps it testable without
    the user's PC online).  Proxy is staged lazily on start/pair."""
    tid = generate_cloudpc_id(owner)
    rec = upsert_cloud_pc({
        "id": tid,
        "display_name": display_name,
        "owner": owner,
        "tailnet_ip": validate_tailnet_ip(tailnet_ip),
        "paired": False,
        "created_at": int(time.time()),
    })
    # If the caller supplied Sunshine creds, attempt immediate pair; if the PC is
    # offline this gracefully records an unpaired PC rather than crashing create.
    if sunshine_username and sunshine_password:
        try:
            pair_cloudpc(tid, sunshine_username=sunshine_username, sunshine_password=sunshine_password)
        except Exception:
            pass
    return load_cloud_pc(tid) or rec


def start_cloudpc(name: str) -> dict[str, Any]:
    rec = load_cloud_pc(name)
    if not rec:
        raise CloudPcConfigError("Cloud PC not found")
    orch = _orchestrator()
    if not _proxy_running(orch, name):
        plan = orch.build_plan(name=name, guest_ip=rec["tailnet_ip"])
        orch.stage_plan(plan)
    orch.start_staged(name)
    return rec


def stop_cloudpc(name: str) -> dict[str, Any]:
    rec = load_cloud_pc(name)
    if not rec:
        raise CloudPcConfigError("Cloud PC not found")
    try:
        _orchestrator().stop_staged(name)
    except Exception:
        pass
    return rec


def pair_cloudpc(name: str, *, sunshine_username: str, sunshine_password: str) -> dict[str, Any]:
    rec = load_cloud_pc(name)
    if not rec:
        raise CloudPcConfigError("Cloud PC not found")
    if not sunshine_username or not sunshine_password:
        raise CloudPcConfigError("Sunshine credentials are required for pairing")
    orch = _orchestrator()
    if not _proxy_running(orch, name):
        plan = orch.build_plan(name=name, guest_ip=rec["tailnet_ip"])
        orch.stage_plan(plan)
        orch.start_staged(name)
    orch.pair_staged(name, sunshine_username=sunshine_username, sunshine_password=sunshine_password)
    _set_paired(name, True)
    return load_cloud_pc(name) or rec


def _set_paired(name: str, value: bool, path: str | os.PathLike[str] | None = None) -> None:
    pcs = _read_all(path)
    for r in pcs:
        if r["id"] == str(name).strip().lower():
            r["paired"] = bool(value)
    _write(pcs, path)


def cloudpc_proxy_up(name: str) -> bool:
    """Public probe: is this Cloud PC's Moonlight proxy container running?"""
    orch = _orchestrator()
    return _proxy_running(orch, name)


def cloudpc_status(name: str, *, tailnet_ip: str, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Best-effort readiness.  Does not require the user's PC to be online."""
    rec = load_cloud_pc(name, path=path)
    paired = bool(rec.get("paired")) if rec else False
    orch = _orchestrator()
    proxy_up = _proxy_running(orch, name)
    sunshine_up = _tcp(tailnet_ip, 47989, 2.0) and _tcp(tailnet_ip, 47990, 2.0)
    if not proxy_up:
        readiness = "stopped"
    elif not sunshine_up:
        readiness = "offline"
    elif not paired:
        readiness = "provisioning"
    else:
        readiness = "ready"
    # Only a paired + reachable PC is streamable.
    running = readiness == "ready"
    return {
        "name": name,
        "type": "cloudpc",
        "readiness": readiness,
        "proxyUp": proxy_up,
        "sunshineUp": sunshine_up,
        "paired": paired,
        "running": running,
        "healthy": running,
        "crashed": False,
        "exists": True,
        "recoveryState": "healthy",
        "state": readiness,
        "status": readiness,
    }
