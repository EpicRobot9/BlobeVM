#!/usr/bin/env python3
"""Host-side EpicVM escalation watcher.

The dashboard container cannot run the host's `hermes` CLI, so it only writes
an escalation ticket (a <vm>-<ts>.json file) plus a <vm>-<ts>.status.json with
{'state':'queued', ...}. This watcher runs on the HOST, polls the escalations
directory for queued tickets, runs `hermes chat` against each, and writes the
final status back so the portal's escalation-status endpoint can report it.

Idempotent: a ticket is only processed once (guarded by the status file state
and a .processing lock file). Safe to run as a systemd service.
"""
import json
import os
import subprocess
import sys
import time

ESC_DIR = "/opt/blobe-vm/dashboard/escalations"
HERMES = "/usr/local/lib/hermes-agent/venv/bin/hermes"
POLL_INTERVAL = 5
MAX_TURNS = 20
SOURCE = "blobevm-dashboard"
# Skip tickets older than this so the watcher never replays ancient escalations
# left behind by older dashboard versions.
MAX_TICKET_AGE_S = 10 * 60

# Mark ourselves so a crash mid-run doesn't leave a ticket stuck 'processing'
# forever: we treat 'processing' as stale if its mtime is older than this.
STALE_PROCESSING_S = 60 * 30


def log(msg):
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}", flush=True)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def write_status(status_path, data):
    tmp = status_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, status_path)


def process_ticket(ticket_path, status_path):
    ticket = read_json(ticket_path)
    if not ticket:
        return
    name = ticket.get("vm", "unknown")
    reason = ticket.get("reason", "Portal recovery help requested by user")
    status = ticket.get("status", {})
    logs = ticket.get("logs", "")
    host = ticket.get("host", "")
    msg = (
        f"{name} recovery request from the dashboard. Act as the recovery operator: "
        "inspect the VM and its recent logs, determine why it is down, and recover it "
        "if safe and possible. Verify the result instead of assuming success. "
        f"Host: {host}. VM: '{name}'. Reason: {reason}. "
        f"Status: {json.dumps(status)}. Recent logs:\n{logs[:3000]}"
    )
    started = int(time.time())
    write_status(status_path, {"state": "processing", "startedAt": started})
    log(f"dispatching hermes for vm={name} ticket={os.path.basename(ticket_path)}")
    try:
        proc = subprocess.run(
            [HERMES, "chat", "-q", msg, "--toolsets", "terminal",
             "--max-turns", str(MAX_TURNS), "--source", SOURCE, "--quiet"],
            capture_output=True, text=True, timeout=600,
        )
        delivered = proc.returncode == 0
        cli_error = "" if delivered else (proc.stderr or proc.stdout or "").strip()[:1200]
    except Exception as e:
        delivered = False
        cli_error = str(e)[:1200]
    write_status(status_path, {
        "state": "done" if delivered else "failed",
        "startedAt": started,
        "finishedAt": int(time.time()),
        "delivered": delivered,
        "cliError": cli_error,
    })
    log(f"vm={name} hermes {'delivered' if delivered else 'failed: ' + cli_error}")


def is_stale_processing(status_path):
    st = read_json(status_path)
    if not st or st.get("state") != "processing":
        return False
    started = st.get("startedAt", 0)
    return (int(time.time()) - started) > STALE_PROCESSING_S


def main():
    os.makedirs(ESC_DIR, exist_ok=True)
    log(f"watching {ESC_DIR} (hermes={HERMES})")
    while True:
        try:
            for fn in os.listdir(ESC_DIR):
                if not fn.endswith(".json"):
                    continue
                if fn.endswith(".status.json") or fn.endswith(".tmp"):
                    continue
                ticket_path = os.path.join(ESC_DIR, fn)
                status_path = ticket_path[: -len(".json")] + ".status.json"
                status = read_json(status_path)
                state = (status or {}).get("state", "missing")
                if state in ("done", "failed") or (status and status.get("hostHandoff") is False):
                    continue
                if state == "processing" and not is_stale_processing(status_path):
                    continue
                if state == "queued" or state == "missing" or is_stale_processing(status_path):
                    # Don't replay ancient tickets (e.g. from older dashboard builds).
                    try:
                        age = int(time.time()) - int(os.path.getmtime(ticket_path))
                    except Exception:
                        age = 0
                    if age > MAX_TICKET_AGE_S:
                        log(f"skipping stale ticket {fn} (age {age}s)")
                        write_status(status_path, {"state": "skipped", "reason": "too old", "startedAt": int(os.path.getmtime(ticket_path))})
                        continue
                    process_ticket(ticket_path, status_path)
        except Exception as e:
            log(f"watcher error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
