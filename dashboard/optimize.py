#!/usr/bin/env python3
import os, time, json, threading, subprocess, traceback
from datetime import datetime, timezone

class Optimizer:
    """Lightweight VM optimizer: polls docker stats, logs actions, and performs
    soft optimization and scheduled restarts based on per-instance settings.
    Settings are stored in instances/<name>/instance.json alongside metadata.
    Logs are appended to instances/<name>/opt-log.jsonl as JSON lines.
    """
    def __init__(self, state_dir='/opt/blobe-vm', manager_bin='blobe-vm-manager', poll_interval=10):
        self.state_dir = state_dir
        self.inst_dir = os.path.join(self.state_dir, 'instances')
        self.manager = manager_bin
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self.stats = {}  # name -> {mem_pct, cpu_pct, uptime_sec, ts}
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def _run_loop(self):
        while not self._stop.is_set():
            try:
                names = [n for n in os.listdir(self.inst_dir) if os.path.isdir(os.path.join(self.inst_dir, n))]
            except Exception:
                names = []
            for name in names:
                try:
                    s = self._collect(name)
                    if s:
                        self.stats[name] = s
                        self._evaluate(name, s)
                except Exception:
                    traceback.print_exc()
            time.sleep(self.poll_interval)

    def _collect(self, name: str):
        cname = f'blobevm_{name}'
        now = time.time()
        mem_pct = None; cpu_pct = None; uptime = None
        # docker stats --no-stream --format "{{.CPUPerc}}|{{.MemPerc}}"
        try:
            r = subprocess.run(['docker','stats','--no-stream','--format','{{.CPUPerc}}|{{.MemPerc}}', cname], capture_output=True, text=True, timeout=5)
            out = (r.stdout or '').strip()
            if out:
                parts = out.split('|')
                if len(parts) >= 2:
                    cpu_pct = float(parts[0].strip().rstrip('%') or 0.0)
                    mem_pct = float(parts[1].strip().rstrip('%') or 0.0)
        except Exception:
            pass
        # uptime from inspect
        try:
            r2 = subprocess.run(['docker','inspect','-f','{{.State.StartedAt}}', cname], capture_output=True, text=True, timeout=3)
            s = (r2.stdout or '').strip()
            if s:
                try:
                    # Example: 2025-11-16T09:12:34.123456789Z
                    if s.endswith('Z'):
                        s2 = s[:-1]
                        dt = datetime.fromisoformat(s2)
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(s)
                    uptime = max(0, int(now - dt.timestamp()))
                except Exception:
                    uptime = None
        except Exception:
            pass
        return {'mem_pct': mem_pct or 0.0, 'cpu_pct': cpu_pct or 0.0, 'uptime': uptime or 0, 'ts': int(now)}

    def _read_instance_meta(self, name: str):
        mf = os.path.join(self.inst_dir, name, 'instance.json')
        try:
            if os.path.isfile(mf):
                with open(mf,'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _write_instance_meta(self, name: str, data: dict):
        mf = os.path.join(self.inst_dir, name, 'instance.json')
        try:
            os.makedirs(os.path.dirname(mf), exist_ok=True)
            existing = {}
            if os.path.isfile(mf):
                try:
                    with open(mf,'r') as f: existing = json.load(f)
                except Exception: existing = {}
            existing.update(data)
            with open(mf,'w') as f:
                json.dump(existing, f, indent=2)
            return True
        except Exception:
            return False

    def _log(self, name: str, action: str, info: dict):
        lf = os.path.join(self.inst_dir, name, 'opt-log.jsonl')
        entry = {'ts': int(time.time()), 'action': action, 'info': info}
        try:
            with open(lf,'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass

    def _evaluate(self, name: str, s: dict):
        meta = self._read_instance_meta(name)
        enabled = str(meta.get('optimize_enabled','1')) not in ('0','false','False')
        if not enabled:
            return
        # thresholds
        mem_th = float(meta.get('mem_threshold_pct', meta.get('max_memory_pct', 85)))
        cpu_th = float(meta.get('cpu_threshold_pct', meta.get('max_cpu_pct', 90)))
        # scheduled restart
        try:
            restart_hours = float(meta.get('restart_interval_hours', 0))
        except Exception:
            restart_hours = 0
        # grace / notification times
        grace = int(meta.get('restart_graceful_seconds', 30))
        # threshold grace minutes
        threshold_grace = int(meta.get('threshold_grace_minutes', 2))

        # scheduled restart check
        if restart_hours and restart_hours > 0:
            last = meta.get('last_auto_restart_ts', 0) or 0
            now = time.time()
            if now - float(last) >= (restart_hours * 3600):
                # queue a graceful restart: write pending flag and log
                self._write_instance_meta(name, {'pending_restart_ts': int(now)})
                self._log(name, 'scheduled_restart_queued', {'since_last': now-last, 'grace': grace})
                def do_restart(nm, grace_s, started_at):
                    try:
                        # Wait loop: periodically check instance meta to allow cancel/postpone
                        mf = os.path.join(self.inst_dir, nm, 'instance.json')
                        while True:
                            now2 = time.time()
                            # reload meta
                            meta2 = {}
                            try:
                                if os.path.isfile(mf):
                                    with open(mf, 'r') as f: meta2 = json.load(f)
                            except Exception:
                                meta2 = {}
                            pending = int(meta2.get('pending_restart_ts', 0) or 0)
                            if pending == 0:
                                # cancelled
                                self._log(nm, 'scheduled_restart_cancelled', {'since_start': now2-started_at})
                                return
                            execute_at = float(pending) + float(meta2.get('restart_graceful_seconds', grace_s))
                            if now2 >= execute_at:
                                break
                            # sleep a short while then re-evaluate (honors postpones)
                            time.sleep(1)
                        # perform restart
                        self._log(nm, 'scheduled_restart_executing', {'started_at': started_at})
                        subprocess.run([self.manager, 'restart', nm], capture_output=True, text=True)
                        self._write_instance_meta(nm, {'last_auto_restart_ts': int(time.time()), 'pending_restart_ts': 0})
                        self._log(nm, 'scheduled_restart_done', {})
                    except Exception as e:
                        self._log(nm, 'scheduled_restart_error', {'error': str(e)})
                threading.Thread(target=do_restart, args=(name, grace, now), daemon=True).start()
                return

        # thresholds check
        mem = s.get('mem_pct') or 0.0
        cpu = s.get('cpu_pct') or 0.0
        if mem >= mem_th or cpu >= cpu_th:
            # record exceed timestamp
            exceed_ts = int(time.time())
            last_ex_ts = int(meta.get('last_threshold_exceed_ts', 0) or 0)
            if last_ex_ts == 0:
                self._write_instance_meta(name, {'last_threshold_exceed_ts': exceed_ts})
                self._log(name, 'threshold_exceeded', {'mem': mem, 'cpu': cpu, 'mem_th': mem_th, 'cpu_th': cpu_th})
                # run soft optimize immediately
                self._soft_optimize(name)
                return
            else:
                # still high: if grace exceeded, perform restart if enabled
                if exceed_ts - last_ex_ts >= (threshold_grace * 60):
                    auto_reboot = str(meta.get('auto_reboot_enabled','1')) not in ('0','false','False')
                    self._log(name, 'threshold_persisted', {'mem': mem, 'cpu': cpu, 'auto_reboot': auto_reboot})
                    if auto_reboot:
                        try:
                            subprocess.run([self.manager, 'restart', name], capture_output=True, text=True)
                            self._write_instance_meta(name, {'last_auto_restart_ts': int(time.time()), 'last_threshold_exceed_ts': 0})
                            self._log(name, 'auto_reboot', {'mem': mem, 'cpu': cpu})
                        except Exception:
                            pass
                        return

    def _soft_optimize(self, name: str):
        cname = f'blobevm_{name}'
        self._log(name, 'soft_optimize_start', {})
        try:
            cmds = [
                'rm -rf /tmp/* /var/tmp/* || true',
                'sync || true',
                'apt-get -y autoclean || true',
                'rm -rf /var/cache/apt/archives/* || true',
            ]
            for c in cmds:
                subprocess.run(['docker','exec','-u','root',cname,'bash','-lc',c], capture_output=True, text=True, timeout=60)
            self._log(name, 'soft_optimize_done', {})
            # update meta last_optimize
            self._write_instance_meta(name, {'last_optimize_ts': int(time.time())})
        except Exception as e:
            self._log(name, 'soft_optimize_error', {'error': str(e)})

    # Public API
    def get_stats(self):
        return self.stats.copy()

    def trigger_optimize(self, name: str):
        # Run optimization once for name
        try:
            self._soft_optimize(name)
            return True
        except Exception:
            return False

    def read_logs(self, name: str, lines: int = 200):
        lf = os.path.join(self.inst_dir, name, 'opt-log.jsonl')
        out = []
        try:
            if os.path.isfile(lf):
                with open(lf,'r') as f:
                    all = f.read().splitlines()
                for l in all[-lines:]:
                    try:
                        out.append(json.loads(l))
                    except Exception:
                        out.append({'raw': l})
        except Exception:
            pass
        return out


if __name__ == '__main__':
    # Simple CLI for script usage
    import sys
    o = Optimizer(state_dir=os.environ.get('BLOBEDASH_STATE','/opt/blobe-vm'))
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'daemon'
    if cmd == 'daemon':
        o.start();
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            o.stop()
    elif cmd == 'stats':
        name = sys.argv[2] if len(sys.argv) > 2 else None
        o.start(); time.sleep(0.5)
        s = o.get_stats()
        if name:
            print(json.dumps(s.get(name, {})))
        else:
            print(json.dumps(s))
    elif cmd == 'optimize':
        name = sys.argv[2] if len(sys.argv) > 2 else None
        if not name:
            print('Usage: blobe-optimize.py optimize <vmName>')
            sys.exit(2)
        ok = o.trigger_optimize(name)
        print('ok' if ok else 'fail')
