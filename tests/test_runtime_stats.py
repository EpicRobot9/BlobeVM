import math
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

from runtime_stats import DOCKER_STATS_COMMAND, DockerStatsCache, get_docker_stats


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_cache_hit_within_ttl_and_parses_canonical_records():
    calls = []
    clock = FakeClock()

    def runner(argv):
        calls.append(argv)
        return "blobevm_a|12.5%|3.25%|12.3MiB / 1GiB\n"

    cache = DockerStatsCache(runner=runner, clock=clock, ttl=2)
    first = cache.get()
    clock.advance(1.9)
    second = cache.get()

    assert first == second == [{
        "name": "blobevm_a", "cpu_percent": 12.5,
        "mem_percent": 3.25, "mem_usage": "12.3MiB / 1GiB",
    }]
    assert len(calls) == 1
    assert calls[0] == list(DOCKER_STATS_COMMAND)


def test_cached_records_are_not_mutable_through_return_value():
    cache = DockerStatsCache(
        runner=lambda argv: "a|1%|2%|3MiB / 1GiB\n",
        clock=FakeClock(),
        ttl=2,
    )

    first = cache.get()
    first[0]["name"] = "corrupted"

    assert cache.get() == [{
        "name": "a", "cpu_percent": 1.0, "mem_percent": 2.0,
        "mem_usage": "3MiB / 1GiB",
    }]


def test_cache_expires_after_ttl():
    calls = []
    clock = FakeClock()

    def runner(argv):
        calls.append(argv)
        return f"blobevm_{len(calls)}|1%|2%|3MiB / 1GiB\n"

    cache = DockerStatsCache(runner=runner, clock=clock, ttl=2)
    assert cache.get()[0]["name"] == "blobevm_1"
    clock.advance(2)
    assert cache.get()[0]["name"] == "blobevm_2"
    assert len(calls) == 2


def test_concurrent_get_is_single_flight():
    calls = []
    barrier = threading.Barrier(3)

    def runner(argv):
        calls.append(argv)
        time.sleep(0.05)
        return "a|1%|2%|3MiB / 1GiB\n"

    cache = DockerStatsCache(runner=runner, clock=time.monotonic, ttl=2)
    results = []

    def read():
        barrier.wait()
        results.append(cache.get())

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert results == [[{"name": "a", "cpu_percent": 1.0, "mem_percent": 2.0, "mem_usage": "3MiB / 1GiB"}]] * 2


def test_failed_refresh_uses_successful_value_for_ten_seconds_then_empty():
    clock = FakeClock()
    calls = []

    def runner(argv):
        calls.append(1)
        if len(calls) == 1:
            return "a|1%|2%|3MiB / 1GiB\n"
        raise RuntimeError("docker unavailable")

    cache = DockerStatsCache(runner=runner, clock=clock, ttl=2)
    assert cache.get()
    clock.advance(2)
    assert cache.get()  # stale successful value
    assert cache.last_error == "docker unavailable"
    clock.advance(8.1)
    assert cache.get() == []
    assert len(calls) == 3


def test_malformed_and_nonfinite_metrics_are_ignored():
    cache = DockerStatsCache(
        runner=lambda argv: "bad\na|nan|2%|1MiB / 1GiB\nb|1%|inf|1MiB / 1GiB\nc|3%|4%|5MiB / 1GiB\n",
        clock=FakeClock(),
        ttl=2,
    )
    assert cache.get() == [{
        "name": "c", "cpu_percent": 3.0, "mem_percent": 4.0,
        "mem_usage": "5MiB / 1GiB",
    }]


def test_ttl_environment_is_bounded(monkeypatch):
    monkeypatch.setenv("BLOBEVM_DOCKER_STATS_TTL", "0.01")
    assert DockerStatsCache(runner=lambda argv: "", clock=FakeClock()).ttl == 0.5
    monkeypatch.setenv("BLOBEVM_DOCKER_STATS_TTL", "99")
    assert DockerStatsCache(runner=lambda argv: "", clock=FakeClock()).ttl == 10.0
    monkeypatch.setenv("BLOBEVM_DOCKER_STATS_TTL", "not-a-number")
    assert DockerStatsCache(runner=lambda argv: "", clock=FakeClock()).ttl == 2.0


def test_module_helper_uses_fixed_command_without_shell(monkeypatch):
    seen = {}

    def fake_check_output(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return "a|1%|2%|3MiB / 1GiB\n"

    monkeypatch.setattr("runtime_stats.subprocess.check_output", fake_check_output)
    get_docker_stats.cache_clear()
    assert get_docker_stats() and seen["argv"] == list(DOCKER_STATS_COMMAND)
    assert seen["kwargs"] == {"text": True, "timeout": 3.0}
    get_docker_stats.cache_clear()
