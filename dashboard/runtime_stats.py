"""Cached, canonical Docker container statistics for dashboard processes."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

DOCKER_STATS_COMMAND = (
    "docker",
    "stats",
    "--no-stream",
    "--format",
    "{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}",
)
_DEFAULT_TTL = 2.0
_MIN_TTL = 0.5
_MAX_TTL = 10.0
_STALE_MAX_AGE = 10.0
_DEFAULT_TIMEOUT = 3.0
_MIN_TIMEOUT = 0.5
_MAX_TIMEOUT = 10.0


def _configured_ttl(value: Optional[object] = None) -> float:
    if value is None:
        value = os.environ.get("BLOBEVM_DOCKER_STATS_TTL", _DEFAULT_TTL)
    try:
        return min(_MAX_TTL, max(_MIN_TTL, float(value)))
    except (TypeError, ValueError):
        return _DEFAULT_TTL


def _configured_timeout(value: Optional[object] = None) -> float:
    if value is None:
        value = os.environ.get("BLOBEVM_DOCKER_STATS_TIMEOUT", _DEFAULT_TIMEOUT)
    try:
        return min(_MAX_TIMEOUT, max(_MIN_TIMEOUT, float(value)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def _subprocess_runner(argv: Sequence[str]) -> str:
    return subprocess.check_output(list(argv), text=True, timeout=_configured_timeout())


class DockerStatsCache:
    """Thread-safe TTL cache with bounded stale-on-error behavior."""

    def __init__(self, runner: Optional[Callable[[Sequence[str]], str]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 ttl: Optional[float] = None):
        self.runner = runner or _subprocess_runner
        self.clock = clock or time.monotonic
        self.ttl = _configured_ttl(ttl)
        self._lock = threading.Lock()
        self._records: List[Dict[str, object]] = []
        self._successful_at: Optional[float] = None
        self._refreshed_at: Optional[float] = None
        self.last_error: Optional[str] = None

    def get(self) -> List[Dict[str, object]]:
        now = self.clock()
        with self._lock:
            if self._refreshed_at is not None and now - self._refreshed_at < self.ttl:
                return [record.copy() for record in self._records]
            try:
                records = self._parse(self.runner(list(DOCKER_STATS_COMMAND)))
            except Exception as exc:
                self.last_error = str(exc)
                self._refreshed_at = now
                if (self._successful_at is not None and
                        now - self._successful_at <= _STALE_MAX_AGE):
                    return [record.copy() for record in self._records]
                return []
            self._records = records
            self._successful_at = now
            self._refreshed_at = now
            self.last_error = None
            return [record.copy() for record in records]

    @staticmethod
    def _parse(output: str) -> List[Dict[str, object]]:
        records: List[Dict[str, object]] = []
        for line in output.splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4 or not parts[0].strip():
                continue
            try:
                cpu = float(parts[1].strip().rstrip("%"))
                mem = float(parts[2].strip().rstrip("%"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(cpu) or not math.isfinite(mem):
                continue
            records.append({
                "name": parts[0].strip(),
                "cpu_percent": cpu,
                "mem_percent": mem,
                "mem_usage": parts[3].strip(),
            })
        return records


_default_cache = DockerStatsCache()


def get_docker_stats() -> List[Dict[str, object]]:
    return _default_cache.get()


def _clear_default_cache() -> None:
    global _default_cache
    _default_cache = DockerStatsCache()


get_docker_stats.cache_clear = _clear_default_cache
