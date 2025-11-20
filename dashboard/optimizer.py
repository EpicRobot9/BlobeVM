#!/usr/bin/env python3
"""Embedded Optimizer module for Blobe dashboard.

Provides:
 - run_once(): perform one optimization pass (guards + optional strict memory enforcement)
 - start_background_loop(): spawn a thread that runs every 15s
 - status(): return {'cfg':..., 'stats':..., 'lastRestart': ...}
 - set_config(key, val): update persisted config
 - tail_logs(): return optimizer log contents

This is a Python port of the previous Node optimizer so it runs inside the Flask process.
"""
import os
import json
import time
import threading
import subprocess
import re

STATE_DIR = os.environ.get('BLOBEDASH_STATE', '/opt/blobe-vm')
LOG_DIR = '/var/blobe/logs/optimizer'
CFG_PATH = os.path.join(STATE_DIR, '.optimizer.json')
RESTART_META_DIR = os.path.join(STATE_DIR, '.optimizer_restarts')
LAST_RESTART_PATH = os.path.join(STATE_DIR, '.optimizer_last_restart')

DEFAULT_CFG = {
    'enabled': True,
    'guards': {'memory': True, 'cpu': True, 'swap': True, 'health': True},
    'schedulerEnabled': True,
    'restartIntervalHours': 24,
    'strictMemoryLimit': False,
    'memoryLimit': '1g',
    'memorySwappiness': 10,
    'containerRestartCooldownMinutes': 10,
}


def ensure_log_dir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


def log(msg: str):
    ensure_log_dir()
    ts = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())
    line = f'[{ts}] {msg}\n'
    try:
        with open(os.path.join(LOG_DIR, 'optimizer.log'), 'a') as f:
            f.write(line)
    except Exception:
        pass


def load_config():
    try:
        with open(CFG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_CFG)


def save_config(cfg: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        with open(CFG_PATH, 'w') as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception as e:
        log(f'failed saving cfg: {e}')
        return False


def _docker_ps_names():
    try:
        out = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True)
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def gather_stats():
    out = {'mem': {}, 'swap': {}, 'containers': []}
    # free -b
    try:
        free = subprocess.check_output(['free', '-b'], text=True)
        lines = free.split('\n')
        memLine = next((l for l in lines if l.lower().startswith('mem:')), '')
        parts = re.split(r'\s+', memLine.strip()) if memLine else []
        if len(parts) >= 3:
            out['mem']['total'] = int(parts[1])
            out['mem']['used'] = int(parts[2])
        swapLine = next((l for l in lines if l.lower().startswith('swap:')), '')
        sp = re.split(r'\s+', swapLine.strip()) if swapLine else []
        if len(sp) >= 3:
            out['swap']['total'] = int(sp[1])
            out['swap']['used'] = int(sp[2])
    except Exception:
        pass
    # docker stats
    try:
        d = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', '{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}'], text=True)
        for l in d.splitlines():
            if not l.strip():
                continue
            parts = l.split('|')
            if len(parts) >= 4:
                name = parts[0]
                try:
                    cpu = float(parts[1].strip().replace('%', ''))
                except Exception:
                    cpu = 0.0
                try:
                    memperc = float(parts[2].strip().replace('%', ''))
                except Exception:
                    memperc = 0.0
                memusage = parts[3].strip()
                m = re.search(r'([0-9.]+)\s*([KMG]i?)B', memusage)
                memBytes = 0
                if m:
                    n = float(m.group(1)); u = m.group(2).upper()
                    mul = 1024
                    if u.startswith('M'):
                        mul = 1024*1024
                    elif u.startswith('G'):
                        mul = 1024*1024*1024
                    memBytes = int(n * mul)
                out['containers'].append({'name': name, 'cpu': cpu, 'memperc': memperc, 'memBytes': memBytes})
    except Exception:
        pass
    return out


def enforce_strict_memory(cfg: dict):
    try:
        names = _docker_ps_names()
        mem = cfg.get('memoryLimit', '1g')
        swappiness = cfg.get('memorySwappiness', 10)
        for name in names:
            if not name.startswith('blobevm_'):
                continue
            try:
                subprocess.check_call(['docker', 'update', f'--memory={mem}', f'--memory-swap={mem}', f'--memory-swappiness={swappiness}', name])
                log(f'enforce memory on {name} -> {mem} swappiness={swappiness}')
            except Exception as e:
                log(f'docker update failed for {name} : {e}')
    except Exception as e:
        log(f'enforceStrictMemory error {e}')


def _read_last_restart():
    try:
        with open(LAST_RESTART_PATH, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _write_last_restart(ts: int):
    try:
        os.makedirs(os.path.dirname(LAST_RESTART_PATH), exist_ok=True)
        with open(LAST_RESTART_PATH, 'w') as f:
            f.write(str(ts))
    except Exception:
        pass


def perform_scheduled_restart(cfg: dict):
    try:
        last = _read_last_restart()
        now = int(time.time())
        interval = int(cfg.get('restartIntervalHours', 0)) * 3600
        if not cfg.get('schedulerEnabled') or not interval:
            return
        if now - last < interval:
            return
        names = _docker_ps_names()
        restarted = 0
        cooldown = int(cfg.get('containerRestartCooldownMinutes', 10)) * 60
        maxPerRun = 10
        os.makedirs(RESTART_META_DIR, exist_ok=True)
        for name in names:
            if not name.startswith('blobevm_'):
                continue
            safe = re.sub(r'[^A-Za-z0-9_.-]', '_', name)
            p = os.path.join(RESTART_META_DIR, safe + '.last')
            lastc = 0
            try:
                with open(p, 'r') as f:
                    lastc = int(f.read().strip())
            except Exception:
                lastc = 0
            if now - lastc < cooldown:
                log(f'skip restart {name} (cooldown)')
                continue
            try:
                subprocess.check_call(['docker', 'restart', name])
                restarted += 1
                log(f'scheduler restart {name}')
                try:
                    with open(p, 'w') as f:
                        f.write(str(now))
                except Exception:
                    pass
                time.sleep(2)
                if restarted >= maxPerRun:
                    break
            except Exception as e:
                log(f'scheduler restart failed {name} : {e}')
        if restarted > 0:
            _write_last_restart(now)
            log(f'performed scheduled restart of {restarted} containers')
    except Exception as e:
        log(f'performScheduledRestart error {e}')


def _run_memory_guard(cfg):
    # analogous to MemoryGuard.js
    try:
        out = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', '{{.Name}} {{.MemPerc}} {{.MemUsage}}'], text=True)
        for l in out.splitlines():
            parts = l.strip().split()
            if not parts:
                continue
            name = parts[0]
            if not name.startswith('blobevm_'):
                continue
            percRaw = parts[1] if len(parts) > 1 else '0%'
            try:
                perc = float(percRaw.replace('%', ''))
            except Exception:
                perc = 0.0
            threshold = cfg.get('memoryThreshold', 60)
            if perc >= threshold:
                log(f'Restarting {name} due to memory {perc}%')
                try:
                    subprocess.check_call(['docker', 'restart', name])
                except Exception:
                    pass
                return {'action': 'restart', 'reason': 'memory', 'container': name, 'perc': perc}
    except Exception as e:
        log(f'memguard error {e}')
    return None


def _run_cpu_guard(cfg):
    try:
        out = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', '{{.Name}} {{.CPUPerc}}'], text=True)
        for l in out.splitlines():
            parts = l.strip().split()
            if not parts:
                continue
            name = parts[0]
            if not name.startswith('blobevm_'):
                continue
            percRaw = parts[1] if len(parts) > 1 else '0%'
            try:
                perc = float(percRaw.replace('%', ''))
            except Exception:
                perc = 0.0
            threshold = cfg.get('cpuThreshold', 70)
            if perc >= threshold:
                # best-effort second check omitted
                log(f'Restarting {name} due to cpu {perc}%')
                try:
                    subprocess.check_call(['docker', 'restart', name])
                except Exception:
                    pass
                return {'action': 'restart', 'reason': 'cpu', 'container': name, 'perc': perc}
    except Exception as e:
        log(f'cpuguard error {e}')
    return None


def _run_swap_guard(cfg):
    try:
        out = subprocess.check_output(['free', '-b'], text=True)
        lines = out.split('\n')
        swapLine = next((l for l in lines if l.lower().startswith('swap')), '')
        if swapLine:
            parts = re.split(r'\s+', swapLine.strip())
            total = int(parts[1]) if len(parts) > 1 else 0
            used = int(parts[2]) if len(parts) > 2 else 0
            perc = int(round(used / total * 100)) if total else 0
            threshold = cfg.get('swapThreshold', 10)
            if perc >= threshold:
                # restart heaviest VM by memory
                stats = subprocess.check_output(['docker', 'stats', '--no-stream', '--format', '{{.Name}} {{.MemUsage}}'], text=True)
                heaviest = None; maxBytes = 0
                for l in stats.splitlines():
                    p = l.strip().split()
                    if not p: continue
                    name = p[0]
                    if not name.startswith('blobevm_'): continue
                    usage = p[1] if len(p) > 1 else '0'
                    m = re.search(r'([0-9.]+)([KMG]i?)B', usage)
                    bytes_ = 0
                    if m:
                        n = float(m.group(1)); u = m.group(2).upper(); mul = 1024
                        if u.startswith('G'): mul = 1024*1024*1024
                        elif u.startswith('M'): mul = 1024*1024
                        bytes_ = int(n * mul)
                    if bytes_ > maxBytes:
                        maxBytes = bytes_; heaviest = name
                try:
                    subprocess.check_call(['bash', '-c', 'sync; echo 3 > /proc/sys/vm/drop_caches'])
                except Exception:
                    pass
                if heaviest:
                    try:
                        subprocess.check_call(['docker', 'restart', heaviest])
                        log(f'Restarting {heaviest} due to swap {perc}%')
                    except Exception:
                        pass
                    return {'action': 'restart', 'reason': 'swap', 'perc': perc, 'heaviest': heaviest}
    except Exception as e:
        log(f'swapguard error {e}')
    return None


def _run_health_guard(cfg):
    try:
        # use blobe-vm-manager list output
        out = subprocess.check_output(['blobe-vm-manager', 'list'], text=True)
        lines = [l for l in out.splitlines() if l.strip().startswith('- ')]
        for l in lines:
            try:
                parts = l[2:].split('->')
                name = parts[0].strip().split()[0]
                url = (parts[2] if len(parts) > 2 else '').strip()
                if not url:
                    continue
                try:
                    r = subprocess.check_output(['curl', '-Is', '--max-time', '6', url], text=True)
                    if not re.search(r'HTTP\/(1|2) [23]', r):
                        stateDir = STATE_DIR
                        f1 = os.path.join(stateDir, 'instances', name, '.health_warn')
                        f2 = os.path.join(stateDir, 'instances', name, '.health_fail')
                        if not os.path.exists(f1):
                            log(f'Health warn for {name}'); open(f1, 'w').write(str(int(time.time())))
                            return {'action': 'warn', 'name': name}
                        if not os.path.exists(f2):
                            log(f'Health restart container {name}'); open(f2, 'w').write(str(int(time.time())))
                            subprocess.check_call(['docker', 'restart', f'blobevm_{name}'])
                            return {'action': 'restart_container', 'name': name}
                        # recreate
                        log(f'Health recreate {name}')
                        try:
                            subprocess.check_call(['blobe-vm-manager', 'recreate', name])
                        except Exception:
                            pass
                        return {'action': 'recreate', 'name': name}
                except Exception:
                    stateDir = STATE_DIR
                    f1 = os.path.join(stateDir, 'instances', name, '.health_warn')
                    if not os.path.exists(f1):
                        log(f'Health warn (curl fail) for {name}'); open(f1, 'w').write(str(int(time.time())))
                        return {'action': 'warn', 'name': name}
                    log(f'Health restart container (curl fail) {name}'); subprocess.check_call(['docker', 'restart', f'blobevm_{name}'])
                    return {'action': 'restart_container', 'name': name}
            except Exception:
                pass
    except Exception as e:
        log(f'healthguard error {e}')
    return None


def run_once():
    cfg = load_config()
    events = []
    try:
        if cfg.get('guards', {}).get('memory'):
            r = _run_memory_guard(cfg)
            if r: events.append(r)
        if cfg.get('guards', {}).get('cpu'):
            r = _run_cpu_guard(cfg)
            if r: events.append(r)
        if cfg.get('guards', {}).get('swap'):
            r = _run_swap_guard(cfg)
            if r: events.append(r)
        if cfg.get('guards', {}).get('health'):
            r = _run_health_guard(cfg)
            if r: events.append(r)
        if cfg.get('strictMemoryLimit'):
            try:
                enforce_strict_memory(cfg)
            except Exception as e:
                log(f'error enforcing strictMemoryLimit: {e}')
    except Exception as e:
        log(f'error in run_once: {e}')
    return events


_loop_thread = None
_loop_lock = threading.Lock()


def _background_loop():
    log('optimizer background loop starting')
    while True:
        try:
            cfg = load_config()
            if cfg.get('enabled'):
                run_once()
            try:
                if cfg.get('schedulerEnabled'):
                    perform_scheduled_restart(cfg)
            except Exception as e:
                log(f'scheduler error {e}')
        except Exception as e:
            log(f'optimizer loop error {e}')
        time.sleep(15)


def start_background_loop():
    global _loop_thread
    with _loop_lock:
        if _loop_thread and _loop_thread.is_alive():
            return False
        t = threading.Thread(target=_background_loop, daemon=True)
        _loop_thread = t
        t.start()
        return True


def status():
    cfg = load_config()
    stats = gather_stats()
    last = 0
    try:
        if os.path.isfile(LAST_RESTART_PATH):
            last = int(open(LAST_RESTART_PATH, 'r').read().strip())
    except Exception:
        last = 0
    return {'cfg': cfg, 'stats': stats, 'lastRestart': last}


def set_config(key, val):
    cfg = load_config()
    if key == 'guards' and isinstance(val, dict):
        cfg.setdefault('guards', {}).update(val)
    else:
        cfg[key] = val
    save_config(cfg)
    return True


def tail_logs():
    p = os.path.join(LOG_DIR, 'optimizer.log')
    try:
        if os.path.isfile(p):
            return open(p, 'r').read()
    except Exception:
        pass
    return ''
