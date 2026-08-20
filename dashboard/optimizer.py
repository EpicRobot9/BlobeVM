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
import shutil
from runtime_stats import get_docker_stats

STATE_DIR = os.environ.get('BLOBEDASH_STATE', '/opt/blobe-vm')
LOG_DIR = '/var/blobe/logs/optimizer'
CFG_PATH = os.path.join(STATE_DIR, '.optimizer.json')
RESTART_META_DIR = os.path.join(STATE_DIR, '.optimizer_restarts')
LAST_RESTART_PATH = os.path.join(STATE_DIR, '.optimizer_last_restart')
LAST_RUN_PATH = os.path.join(STATE_DIR, '.optimizer_last_run.json')
ACTION_META_DIR = os.path.join(STATE_DIR, '.optimizer_actions')
ACTIVITY_META_DIR = os.path.join(STATE_DIR, '.optimizer_activity')
PROFILE_META_PATH = os.path.join(STATE_DIR, '.optimizer_profiles.json')
HISTORY_META_PATH = os.path.join(STATE_DIR, '.optimizer_history.json')
TREND_META_PATH = os.path.join(STATE_DIR, '.optimizer_trends.json')
NOTIFICATION_META_DIR = os.path.join(STATE_DIR, '.optimizer_notifications')
CPU_PRIORITY_META_PATH = os.path.join(STATE_DIR, '.optimizer_cpu_priority.json')

DENSITY_PROFILES = {
    'single-user': {
        'hostCpuSoftLimit': 75,
        'hostCpuHardLimit': 90,
        'minAvailableMemoryMb': 2048,
        'gamingVmCpuBudgetPercent': 30,
        'interactiveVmCpuBudgetPercent': 20,
        'gamingVmMemoryMb': 3072,
        'interactiveVmMemoryMb': 2048,
    },
    'small-group': {
        'hostCpuSoftLimit': 80,
        'hostCpuHardLimit': 92,
        'minAvailableMemoryMb': 1536,
        'gamingVmCpuBudgetPercent': 24,
        'interactiveVmCpuBudgetPercent': 16,
        'gamingVmMemoryMb': 2560,
        'interactiveVmMemoryMb': 1792,
    },
    'multi-user': {
        'hostCpuSoftLimit': 82,
        'hostCpuHardLimit': 94,
        'minAvailableMemoryMb': 1536,
        'gamingVmCpuBudgetPercent': 20,
        'interactiveVmCpuBudgetPercent': 14,
        'gamingVmMemoryMb': 2304,
        'interactiveVmMemoryMb': 1536,
    },
}

DEFAULT_CFG = {
    'enabled': True,
    'guards': {'memory': True, 'cpu': True, 'swap': True, 'health': True},
    'schedulerEnabled': True,
    'restartIntervalHours': 24,
    'strictMemoryLimit': False,
    'memoryLimit': '1g',
    'memorySwappiness': 10,
    'containerRestartCooldownMinutes': 10,
    'guardCooldownSeconds': 300,
    'maxActionsPerRun': 3,
    'hostCpuSoftLimit': 75,
    'hostCpuHardLimit': 90,
    'minAvailableMemoryMb': 2048,
    'maxSwapPercent': 10,
    'protectActiveVms': True,
    'activityWindowSeconds': 300,
    'idleGraceSeconds': 1800,
    'idleShutdownSeconds': 1800,
    'notificationLeadSeconds': 10,
    'blockStartsOnPressure': True,
    'allowForceStartUnderPressure': True,
    'protectedRestartCooldownSeconds': 900,
    'densityProfile': 'single-user',
    'gamingVmCpuBudgetPercent': 30,
    'interactiveVmCpuBudgetPercent': 20,
    'gamingVmMemoryMb': 3072,
    'interactiveVmMemoryMb': 2048,
    'activityCpuPriorityEnabled': False,
    'activeCpuShares': 2048,
    'warmCpuShares': 1024,
    'idleCpuShares': 512,
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


def available_density_profiles():
    return {k: dict(v) for k, v in DENSITY_PROFILES.items()}


def apply_density_profile(profile: str):
    chosen = (profile or 'single-user').strip().lower()
    if chosen not in DENSITY_PROFILES:
        chosen = 'single-user'
    cfg = load_config()
    for key, value in DENSITY_PROFILES[chosen].items():
        cfg[key] = value
    cfg['densityProfile'] = chosen
    save_config(cfg)
    return {'profile': chosen, 'cfg': cfg}


def _docker_ps_names():
    try:
        out = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True)
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def _list_vm_names():
    names = set()
    inst_root = os.path.join(STATE_DIR, 'instances')
    try:
        if os.path.isdir(inst_root):
            for n in os.listdir(inst_root):
                if os.path.isdir(os.path.join(inst_root, n)):
                    names.add(n)
    except Exception:
        pass
    for cname in _docker_ps_names():
        if cname.startswith('blobevm_'):
            names.add(cname[len('blobevm_'):])
    return sorted(names)


def _read_json_file(path, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default


def _write_json_file(path, payload):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception as e:
        log(f'failed writing json file {path}: {e}')
        return False


def load_profiles():
    data = _read_json_file(PROFILE_META_PATH, {})
    if isinstance(data, dict):
        return data
    return {}


def set_vm_profile(name: str, profile: str):
    profile = (profile or 'desktop').strip().lower()
    allowed = {'light', 'desktop', 'interactive', 'gaming', 'background', 'disposable'}
    if profile not in allowed:
        profile = 'desktop'
    profiles = load_profiles()
    profiles[name] = profile
    _write_json_file(PROFILE_META_PATH, profiles)
    return profile


def note_vm_activity(name: str, source: str = 'unknown'):
    try:
        os.makedirs(ACTIVITY_META_DIR, exist_ok=True)
        path = os.path.join(ACTIVITY_META_DIR, re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '.json')
        payload = {
            'name': name,
            'source': source,
            'lastActivityTs': int(time.time()),
        }
        _write_json_file(path, payload)
        return True
    except Exception:
        return False


def _activity_payload(name: str):
    path = os.path.join(ACTIVITY_META_DIR, re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '.json')
    data = _read_json_file(path, {})
    if not isinstance(data, dict):
        data = {}
    return data


def _notification_path(name: str):
    return os.path.join(NOTIFICATION_META_DIR, re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '.json')


def push_vm_notification(name: str, kind: str, title: str, body: str, ttl_seconds: int = 60, extra: dict | None = None):
    try:
        os.makedirs(NOTIFICATION_META_DIR, exist_ok=True)
        now = int(time.time())
        payload = {
            'id': f'{name}-{kind}-{now}',
            'name': name,
            'kind': kind,
            'title': title,
            'body': body,
            'createdAt': now,
            'expiresAt': now + max(5, int(ttl_seconds or 60)),
            'extra': extra or {},
        }
        path = _notification_path(name)
        existing = _read_json_file(path, {'items': []})
        items = [x for x in (existing.get('items') or []) if int(x.get('expiresAt') or 0) > now]
        items.append(payload)
        _write_json_file(path, {'items': items[-10:]})
        return payload
    except Exception as e:
        log(f'failed pushing vm notification for {name}: {e}')
        return None


def get_vm_notifications(name: str, clear: bool = False):
    path = _notification_path(name)
    now = int(time.time())
    data = _read_json_file(path, {'items': []})
    items = [x for x in (data.get('items') or []) if int(x.get('expiresAt') or 0) > now]
    if clear:
        _write_json_file(path, {'items': []})
    elif len(items) != len(data.get('items') or []):
        _write_json_file(path, {'items': items})
    return items


def _notify_before_action(name: str, vm_state: dict | None, action: str, reason: str, cfg: dict):
    vm_state = vm_state or {}
    activity = str(vm_state.get('activityClass') or 'idle')
    if activity != 'active':
        return
    lead = max(1, int(cfg.get('notificationLeadSeconds', 10) or 10))
    push_vm_notification(
        name,
        'optimizer-action',
        'Optimizer action incoming',
        f'The optimizer will {action} this VM in {lead} seconds ({reason}). Save your work if needed.',
        ttl_seconds=lead + 25,
        extra={'action': action, 'reason': reason, 'leadSeconds': lead},
    )
    log(f'notified active VM {name} about upcoming {action} ({reason}) in {lead}s')
    time.sleep(lead)


def _stop_vm(name: str, vm_state: dict | None, cfg: dict, reason: str, cooldown_key: str):
    cooldown = int(cfg.get('guardCooldownSeconds', 300))
    if not _action_allowed(name, cooldown_key, cooldown):
        return None
    _notify_before_action(name, vm_state, 'stop', reason, cfg)
    try:
        subprocess.check_call(['docker', 'stop', f'blobevm_{name}'])
        log(f'stopped VM {name} due to {reason}')
        return {'action': 'stop', 'reason': reason, 'container': f'blobevm_{name}', 'name': name}
    except Exception as e:
        log(f'failed stopping VM {name} due to {reason}: {e}')
        return None


def _restart_vm_container(name: str, vm_state: dict | None, cfg: dict, reason: str, action_key: str):
    cooldown = int(cfg.get('guardCooldownSeconds', 300))
    if not _action_allowed(name, action_key, cooldown):
        return None
    _notify_before_action(name, vm_state, 'restart', reason, cfg)
    try:
        subprocess.check_call(['docker', 'restart', f'blobevm_{name}'])
        log(f'restarted VM {name} due to {reason}')
        return {'action': 'restart', 'reason': reason, 'container': f'blobevm_{name}', 'name': name}
    except Exception as e:
        log(f'failed restarting VM {name} due to {reason}: {e}')
        return None


def _history_state():
    data = _read_json_file(HISTORY_META_PATH, {'events': [], 'vms': {}})
    if not isinstance(data, dict):
        data = {'events': [], 'vms': {}}
    data.setdefault('events', [])
    data.setdefault('vms', {})
    return data


def _save_history_state(data):
    if not isinstance(data, dict):
        return False
    data.setdefault('events', [])
    data.setdefault('vms', {})
    data['events'] = list(data.get('events') or [])[-120:]
    return _write_json_file(HISTORY_META_PATH, data)


def _record_history_event(event: dict):
    try:
        if not isinstance(event, dict):
            return
        data = _history_state()
        ev = dict(event)
        ev.setdefault('ts', int(time.time()))
        vm_name = ev.get('name') or ev.get('vm')
        if not vm_name and ev.get('container', '').startswith('blobevm_'):
            vm_name = ev['container'][len('blobevm_'):]
        if vm_name:
            ev['vm'] = vm_name
            rec = data['vms'].get(vm_name, {}) if isinstance(data.get('vms'), dict) else {}
            rec.setdefault('history', [])
            rec['lastAction'] = ev.get('action') or ev.get('reason') or 'event'
            rec['lastEventTs'] = ev['ts']
            rec['lastReason'] = ev.get('reason') or ''
            if ev.get('action') in ('restart', 'restart_container'):
                rec['restartCount'] = int(rec.get('restartCount') or 0) + 1
            if ev.get('action') == 'recreate':
                rec['recreateCount'] = int(rec.get('recreateCount') or 0) + 1
            if ev.get('action') == 'warn':
                rec['warnCount'] = int(rec.get('warnCount') or 0) + 1
            rec['history'] = (list(rec.get('history') or []) + [ev])[-20:]
            recent_bad = [x for x in rec['history'] if x.get('action') in ('restart', 'restart_container', 'recreate') and (ev['ts'] - int(x.get('ts') or ev['ts'])) <= 1800]
            rec['unstable'] = len(recent_bad) >= 3
            data['vms'][vm_name] = rec
        data['events'] = (list(data.get('events') or []) + [ev])[-120:]
        _save_history_state(data)
    except Exception as e:
        log(f'failed recording history event: {e}')


def _trend_state():
    data = _read_json_file(TREND_META_PATH, {'points': []})
    if not isinstance(data, dict):
        data = {'points': []}
    data.setdefault('points', [])
    return data


def _record_trend_point(host_pressure: dict, capacity: dict, vm_states):
    try:
        data = _trend_state()
        point = {
            'ts': int(time.time()),
            'pressureLevel': host_pressure.get('level'),
            'pressureScore': host_pressure.get('score'),
            'vmCpuTotal': host_pressure.get('vmCpuTotal'),
            'availableMemoryMb': host_pressure.get('availableMemoryMb'),
            'swapPercent': host_pressure.get('swapPercent'),
            'gamingSuitability': capacity.get('gamingSuitability'),
            'estimatedAdditionalGamingSlots': capacity.get('estimatedAdditionalGamingSlots'),
            'estimatedAdditionalInteractiveSlots': capacity.get('estimatedAdditionalInteractiveSlots'),
            'activeVmCount': capacity.get('activeVmCount'),
            'interactiveVmCount': capacity.get('interactiveVmCount'),
            'gamingVmCount': capacity.get('gamingVmCount'),
            'unstableVmCount': len([v for v in (vm_states or []) if v.get('unstable')]),
            'recoveringVmCount': len([v for v in (vm_states or []) if str(v.get('recoveryState', '')).startswith('recover') or 'restart' in str(v.get('recoveryState', '')) or 'degraded' in str(v.get('recoveryState', ''))]),
        }
        pts = list(data.get('points') or [])
        pts.append(point)
        data['points'] = pts[-180:]
        _write_json_file(TREND_META_PATH, data)
    except Exception as e:
        log(f'failed recording trend point: {e}')


def _derive_host_pressure(stats: dict, cfg: dict):
    mem = stats.get('mem') or {}
    swap = stats.get('swap') or {}
    available = mem.get('available', max(0, int(mem.get('total', 0)) - int(mem.get('used', 0))))
    total_mem_mb = int((mem.get('total') or 0) / 1024 / 1024) if mem.get('total') else 0
    available_mb = int(available / 1024 / 1024) if available else 0
    cpu_values = [float(c.get('cpu') or 0.0) for c in (stats.get('containers') or []) if str(c.get('name', '')).startswith('blobevm_')]
    vm_cpu_total = sum(cpu_values)
    swap_total = int(swap.get('total') or 0)
    swap_used = int(swap.get('used') or 0)
    swap_percent = int(round((swap_used / swap_total) * 100)) if swap_total else 0
    score = 0
    reasons = []
    if vm_cpu_total >= float(cfg.get('hostCpuHardLimit', 90)):
        score += 3; reasons.append(f'vm cpu {vm_cpu_total:.1f}% >= hard limit')
    elif vm_cpu_total >= float(cfg.get('hostCpuSoftLimit', 75)):
        score += 2; reasons.append(f'vm cpu {vm_cpu_total:.1f}% >= soft limit')
    if available_mb and available_mb <= int(cfg.get('minAvailableMemoryMb', 2048)):
        score += 2; reasons.append(f'available memory {available_mb}MB below reserve')
    if swap_percent >= int(cfg.get('maxSwapPercent', 10)):
        score += 3; reasons.append(f'swap {swap_percent}% >= max')
    if score >= 5:
        level = 'critical'
    elif score >= 3:
        level = 'pressured'
    elif score >= 1:
        level = 'warm'
    else:
        level = 'healthy'
    return {
        'level': level,
        'score': score,
        'reasons': reasons,
        'vmCpuTotal': round(vm_cpu_total, 2),
        'availableMemoryMb': available_mb,
        'totalMemoryMb': total_mem_mb,
        'swapPercent': swap_percent,
    }


def _meminfo_stats():
    stats = {'mem': {}, 'swap': {}}
    try:
        meminfo = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if ':' not in line:
                    continue
                key, val = line.split(':', 1)
                num = re.findall(r'\d+', val)
                if num:
                    meminfo[key.strip()] = int(num[0]) * 1024
        total = int(meminfo.get('MemTotal') or 0)
        available = int(meminfo.get('MemAvailable') or 0)
        if not available:
            freeish = int(meminfo.get('MemFree') or 0) + int(meminfo.get('Buffers') or 0) + int(meminfo.get('Cached') or 0)
            available = freeish
        used = max(0, total - available) if total else 0
        stats['mem'] = {'total': total, 'used': used, 'available': available}
        swap_total = int(meminfo.get('SwapTotal') or 0)
        swap_free = int(meminfo.get('SwapFree') or 0)
        stats['swap'] = {'total': swap_total, 'used': max(0, swap_total - swap_free)}
    except Exception:
        pass
    return stats


def gather_stats():
    out = {'mem': {}, 'swap': {}, 'containers': []}
    try:
        if shutil.which('free'):
            free = subprocess.check_output(['free', '-b'], text=True)
            lines = free.split('\n')
            memLine = next((l for l in lines if l.lower().startswith('mem:')), '')
            parts = re.split(r'\s+', memLine.strip()) if memLine else []
            if len(parts) >= 3:
                out['mem']['total'] = int(parts[1])
                out['mem']['used'] = int(parts[2])
                if len(parts) >= 7:
                    out['mem']['available'] = int(parts[6])
            swapLine = next((l for l in lines if l.lower().startswith('swap:')), '')
            sp = re.split(r'\s+', swapLine.strip()) if swapLine else []
            if len(sp) >= 3:
                out['swap']['total'] = int(sp[1])
                out['swap']['used'] = int(sp[2])
        else:
            fallback = _meminfo_stats()
            out['mem'] = fallback.get('mem') or {}
            out['swap'] = fallback.get('swap') or {}
    except Exception:
        fallback = _meminfo_stats()
        out['mem'] = fallback.get('mem') or {}
        out['swap'] = fallback.get('swap') or {}
    try:
        for record in get_docker_stats():
            name = record['name']
            cpu = record['cpu_percent']
            memperc = record['mem_percent']
            memusage = record['mem_usage']
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


def _cpu_share_value(value, default):
    """Return a safe Docker CPU-share value from potentially bad config input."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return min(262144, max(2, value))


def _desired_cpu_shares(vm_states, cfg):
    """Map valid running VM names to their configured activity-class shares."""
    shares = {
        'active': _cpu_share_value(cfg.get('activeCpuShares', DEFAULT_CFG['activeCpuShares']), DEFAULT_CFG['activeCpuShares']),
        'warm': _cpu_share_value(cfg.get('warmCpuShares', DEFAULT_CFG['warmCpuShares']), DEFAULT_CFG['warmCpuShares']),
        'idle': _cpu_share_value(cfg.get('idleCpuShares', DEFAULT_CFG['idleCpuShares']), DEFAULT_CFG['idleCpuShares']),
    }
    desired = {}
    for state in vm_states or []:
        name = str(state.get('name') or '')
        activity_class = state.get('activityClass')
        if (not state.get('running') or activity_class not in shares or
                name in {'dashboard', 'traefik'} or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)):
            continue
        desired[name] = shares[activity_class]
    return dict(sorted(desired.items()))


def _cpu_priority_metadata(data):
    """Normalize current and legacy CPU-priority metadata."""
    if not isinstance(data, dict):
        return {'enabled': False, 'vms': {}}
    current_format = isinstance(data.get('vms'), dict)
    if current_format:
        vms = data['vms']
    else:
        vms = {
            name: {'share': value, 'containerId': None}
            for name, value in data.items()
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', str(name))
        }
    return {'enabled': data.get('enabled') is True or (not current_format and bool(vms)), 'vms': vms}


def _container_identity(name):
    try:
        identity = subprocess.check_output(
            ['docker', 'inspect', '--format={{.ID}}', f'blobevm_{name}'],
            text=True,
        ).strip()
        return identity or None
    except Exception:
        return None


def _apply_cpu_priority(cfg, vm_states):
    """Apply or safely reset activity CPU shares, tolerating Docker failures."""
    try:
        metadata = _cpu_priority_metadata(_read_json_file(CPU_PRIORITY_META_PATH, {}))
        if cfg.get('activityCpuPriorityEnabled') is not True:
            remaining = {}
            for name, entry in metadata['vms'].items():
                valid_name = isinstance(name, str) and re.fullmatch(
                    r'[A-Za-z0-9][A-Za-z0-9_.-]*', name
                )
                valid_identity = (
                    isinstance(entry, dict)
                    and isinstance(entry.get('containerId'), str)
                    and bool(entry['containerId'].strip())
                )
                if not valid_name or not valid_identity:
                    remaining[name] = entry
                    continue
                identity = _container_identity(name)
                if identity != entry['containerId']:
                    remaining[name] = entry
                    log(f'cpu priority reset skipped for {name}: container identity changed or unavailable')
                    continue
                try:
                    subprocess.check_call([
                        'docker', 'update', '--cpu-shares=1024', entry['containerId']
                    ])
                    log(f'cpu priority reset for {name} -> 1024 shares')
                except Exception as e:
                    remaining[name] = entry
                    log(f'docker cpu priority reset failed for {name}: {e}')
            if remaining:
                _write_json_file(CPU_PRIORITY_META_PATH, {'enabled': False, 'vms': remaining})
            else:
                try:
                    os.remove(CPU_PRIORITY_META_PATH)
                except FileNotFoundError:
                    pass
            return {}

        desired = _desired_cpu_shares(vm_states, cfg)
        previous = metadata['vms']
        applied = {}
        for name, share in desired.items():
            identity = _container_identity(name)
            prior = previous.get(name) if isinstance(previous.get(name), dict) else {}
            if (prior.get('share') == share and identity and
                    prior.get('containerId') == identity):
                applied[name] = {'share': share, 'containerId': identity}
                continue
            try:
                subprocess.check_call(['docker', 'update', f'--cpu-shares={share}', f'blobevm_{name}'])
                applied[name] = {'share': share, 'containerId': identity}
                log(f'cpu priority for {name} -> {share} shares')
            except Exception as e:
                log(f'docker cpu priority update failed for {name}: {e}')
        if applied or not desired:
            _write_json_file(CPU_PRIORITY_META_PATH, {'enabled': True, 'vms': applied})
        return {name: item['share'] for name, item in applied.items()}
    except Exception as e:
        log(f'cpu priority error: {e}')
        return {}


def _action_allowed(name: str, action: str, cooldown: int):
    try:
        os.makedirs(ACTION_META_DIR, exist_ok=True)
        safe = re.sub(r'[^A-Za-z0-9_.-]', '_', f'{name}.{action}')
        path = os.path.join(ACTION_META_DIR, safe + '.last')
        now = int(time.time())
        last = 0
        if os.path.isfile(path):
            try:
                last = int(open(path, 'r').read().strip())
            except Exception:
                last = 0
        if now - last < max(0, int(cooldown)):
            log(f'skip {action} for {name} (cooldown {cooldown}s)')
            return False
        with open(path, 'w') as f:
            f.write(str(now))
        return True
    except Exception:
        return True


def _record_last_run(events, stats=None):
    try:
        payload = {'ts': int(time.time()), 'events': events or [], 'stats': stats or {}}
        with open(LAST_RUN_PATH, 'w') as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        log(f'failed writing last run: {e}')


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
        current_states = _vm_state_map(_derive_vm_states(cfg, gather_stats()))
        names = _docker_ps_names()
        restarted = 0
        cooldown = int(cfg.get('containerRestartCooldownMinutes', 10)) * 60
        maxPerRun = 10
        os.makedirs(RESTART_META_DIR, exist_ok=True)
        for name in names:
            if not name.startswith('blobevm_'):
                continue
            vm_name = name[len('blobevm_'):]
            vm_state = current_states.get(vm_name)
            if _is_vm_protected(vm_state):
                log(f'skip scheduled restart {name} (protected/active VM)')
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
                restarted_event = _restart_vm_container(vm_name, vm_state, cfg, 'scheduled-restart', 'scheduled-restart')
                if not restarted_event:
                    continue
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


def _run_memory_guard(cfg, vm_state_map=None, host_pressure=None):
    # analogous to MemoryGuard.js
    vm_state_map = vm_state_map or {}
    host_pressure = host_pressure or {}
    try:
        for record in get_docker_stats():
            name = record['name']
            if not name.startswith('blobevm_'):
                continue
            vm_name = name[len('blobevm_'):]
            vm_state = vm_state_map.get(vm_name)
            perc = record['mem_percent']
            threshold = cfg.get('memoryThreshold', 60)
            if perc >= threshold:
                if _is_vm_protected(vm_state):
                    log(f'skip memory restart for {name} at {perc}% (protected active VM)')
                    continue
                cooldown = int(cfg.get('guardCooldownSeconds', 300))
                if not _action_allowed(name, 'memory-restart', cooldown):
                    continue
                log(f'Restarting {name} due to memory {perc}%')
                restarted = _restart_vm_container(vm_name, vm_state, cfg, 'memory', 'memory-restart')
                if restarted:
                    restarted['perc'] = perc
                    restarted['container'] = name
                    return restarted
    except Exception as e:
        log(f'memguard error {e}')
    return None


def _run_cpu_guard(cfg, vm_state_map=None, host_pressure=None):
    vm_state_map = vm_state_map or {}
    host_pressure = host_pressure or {}
    try:
        for record in get_docker_stats():
            name = record['name']
            if not name.startswith('blobevm_'):
                continue
            vm_name = name[len('blobevm_'):]
            vm_state = vm_state_map.get(vm_name)
            perc = record['cpu_percent']
            threshold = cfg.get('cpuThreshold', 70)
            if perc >= threshold:
                if _is_vm_protected(vm_state):
                    log(f'skip cpu restart for {name} at {perc}% (protected active VM)')
                    continue
                if host_pressure.get('level') in ('pressured', 'critical'):
                    log(f'skip cpu restart for {name} at {perc}% while host pressure={host_pressure.get("level")} (prefer relief actions)')
                    continue
                cooldown = int(cfg.get('guardCooldownSeconds', 300))
                if not _action_allowed(name, 'cpu-restart', cooldown):
                    continue
                log(f'Restarting {name} due to cpu {perc}%')
                restarted = _restart_vm_container(vm_name, vm_state, cfg, 'cpu', 'cpu-restart')
                if restarted:
                    restarted['perc'] = perc
                    restarted['container'] = name
                    return restarted
    except Exception as e:
        log(f'cpuguard error {e}')
    return None


def _run_swap_guard(cfg, vm_state_map=None, host_pressure=None):
    vm_state_map = vm_state_map or {}
    try:
        swap = (gather_stats().get('swap') or {})
        total = int(swap.get('total') or 0)
        used = int(swap.get('used') or 0)
        perc = int(round(used / total * 100)) if total else 0
        threshold = cfg.get('swapThreshold', cfg.get('maxSwapPercent', 10))
        try:
            threshold = max(1, int(threshold))
        except (TypeError, ValueError):
            threshold = 10
        if total or used:
            if perc >= threshold:
                relief = _stop_idle_pressure_vm(cfg, list(vm_state_map.values()), {'level': 'critical' if perc >= max(threshold, int(cfg.get('maxSwapPercent', 10))) else 'pressured'})
                if relief:
                    relief['reason'] = 'swap-pressure-relief'
                    relief['perc'] = perc
                    return relief
                heaviest = None; maxBytes = 0
                for record in get_docker_stats():
                    name = record['name']
                    if not name.startswith('blobevm_'): continue
                    vm_name = name[len('blobevm_'):]
                    if _is_vm_protected(vm_state_map.get(vm_name)):
                        continue
                    usage = record['mem_usage']
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
                    heaviest_vm = heaviest[len('blobevm_'):] if heaviest.startswith('blobevm_') else heaviest
                    restarted = _restart_vm_container(heaviest_vm, vm_state_map.get(heaviest_vm), cfg, 'swap', 'swap-restart')
                    if restarted:
                        log(f'Restarting {heaviest} due to swap {perc}%')
                        restarted.update({'perc': perc, 'heaviest': heaviest})
                        return restarted
    except Exception as e:
        log(f'swapguard error {e}')
    return None


def _health_probe_urls(name: str, advertised_url: str):
    urls = []
    if advertised_url:
        urls.append(advertised_url)
    env_path = os.path.join(STATE_DIR, 'instances', name, '.env')
    path_override = ''
    try:
        if os.path.isfile(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.startswith('PATH_OVERRIDE='):
                        path_override = line.split('=', 1)[1].strip().strip("'\"")
                        break
    except Exception:
        path_override = ''
    base_path = os.environ.get('BASE_PATH', '/vm') or '/vm'
    if not base_path.startswith('/'):
        base_path = '/' + base_path
    base_path = base_path.rstrip('/')
    local_path = path_override or f'{base_path}/{name}/'
    if not local_path.startswith('/'):
        local_path = '/' + local_path
    if not local_path.endswith('/'):
        local_path += '/'
    schemes = ['https', 'http']
    primary_scheme = 'https' if str(os.environ.get('ENABLE_TLS', '0')) == '1' else 'http'
    if primary_scheme in schemes:
        schemes = [primary_scheme] + [s for s in schemes if s != primary_scheme]
    for scheme in schemes:
        urls.append(f'{scheme}://127.0.0.1{local_path}')
        urls.append(f'{scheme}://172.18.0.1{local_path}')
    return urls


def _probe_healthy(urls):
    for url in urls:
        try:
            r = subprocess.check_output(['curl', '-k', '-Is', '--max-time', '6', url], text=True)
            if re.search(r'HTTP\/(1|2) [23]', r):
                return True, url
        except Exception:
            continue
    return False, ''


def _run_health_guard(cfg, vm_state_map=None, host_pressure=None):
    vm_state_map = vm_state_map or {}
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
                protected = _is_vm_protected(vm_state_map.get(name))
                stateDir = STATE_DIR
                f1 = os.path.join(stateDir, 'instances', name, '.health_warn')
                f2 = os.path.join(stateDir, 'instances', name, '.health_fail')
                ok, good_url = _probe_healthy(_health_probe_urls(name, url))
                if ok:
                    if os.path.exists(f1):
                        try:
                            os.remove(f1)
                        except Exception:
                            pass
                    if os.path.exists(f2):
                        try:
                            os.remove(f2)
                        except Exception:
                            pass
                    continue
                if not os.path.exists(f1):
                    log(f'Health warn for {name}'); open(f1, 'w').write(str(int(time.time())))
                    return {'action': 'warn', 'name': name}
                if not os.path.exists(f2):
                    if protected:
                        log(f'skip health restart for {name} (protected active VM); emitting degraded warning only')
                        return {'action': 'warn', 'name': name, 'reason': 'health-protected'}
                    cooldown = int(cfg.get('guardCooldownSeconds', 300))
                    if not _action_allowed(name, 'health-restart', cooldown):
                        return {'action': 'cooldown', 'name': name, 'reason': 'health-restart'}
                    log(f'Health restart container {name}'); open(f2, 'w').write(str(int(time.time())))
                    restarted = _restart_vm_container(name, vm_state_map.get(name), cfg, 'health', 'health-restart')
                    if restarted:
                        restarted['action'] = 'restart_container'
                        return restarted
                    return {'action': 'cooldown', 'name': name, 'reason': 'health-restart-skipped'}
                if protected:
                    return {'action': 'warn', 'name': name, 'reason': 'health-protected-recreate-skipped'}
                cooldown = int(cfg.get('guardCooldownSeconds', 300))
                if not _action_allowed(name, 'health-recreate', cooldown):
                    return {'action': 'cooldown', 'name': name, 'reason': 'health-recreate'}
                log(f'Health recreate {name}')
                try:
                    subprocess.check_call(['blobe-vm-manager', 'recreate', name])
                except Exception:
                    pass
                return {'action': 'recreate', 'name': name}
            except Exception:
                pass
    except Exception as e:
        log(f'healthguard error {e}')
    return None


def _derive_vm_states(cfg: dict, stats: dict):
    profiles = load_profiles()
    history = _history_state().get('vms', {})
    by_name = {}
    for c in stats.get('containers') or []:
        name = str(c.get('name') or '')
        if name.startswith('blobevm_'):
            by_name[name[len('blobevm_'):]] = c
    now = int(time.time())
    active_window = int(cfg.get('activityWindowSeconds', 300))
    idle_grace = int(cfg.get('idleGraceSeconds', 1800))
    states = []
    for name in _list_vm_names():
        c = by_name.get(name) or {}
        activity = _activity_payload(name)
        last_activity = int(activity.get('lastActivityTs') or 0)
        age = max(0, now - last_activity) if last_activity else None
        activity_source = activity.get('source') or ''
        profile = profiles.get(name, 'desktop')
        activity_class = 'idle'
        protected = False
        if age is not None and age <= active_window:
            activity_class = 'active'
            protected = bool(cfg.get('protectActiveVms', True)) or profile in ('interactive', 'gaming')
        elif profile in ('interactive', 'gaming') and age is not None and age <= idle_grace:
            activity_class = 'warm'
            protected = bool(cfg.get('protectActiveVms', True))
        pressure = 'low'
        cpu = float(c.get('cpu') or 0.0)
        mem = float(c.get('memperc') or 0.0)
        if cpu >= 85 or mem >= 90:
            pressure = 'high'
        elif cpu >= 60 or mem >= 75:
            pressure = 'medium'
        hist = history.get(name, {}) if isinstance(history, dict) else {}
        last_action = hist.get('lastAction')
        last_reason = hist.get('lastReason')
        unstable = bool(hist.get('unstable'))
        recovery_state = 'healthy'
        if not c:
            recovery_state = 'stopped'
        elif unstable:
            recovery_state = 'restart-loop'
        elif last_action in ('recreate',):
            recovery_state = 'recovering'
        elif last_action in ('restart', 'restart_container'):
            recovery_state = 'restarting'
        elif last_action == 'warn':
            recovery_state = 'degraded'
        if protected and recovery_state in ('degraded', 'restarting', 'recovering'):
            recovery_state = 'protected-' + recovery_state
        state = {
            'name': name,
            'profile': profile,
            'activityClass': activity_class,
            'protected': protected,
            'lastActivityTs': last_activity,
            'secondsSinceActivity': age,
            'activitySource': activity_source,
            'cpuPercent': round(cpu, 2),
            'memPercent': round(mem, 2),
            'pressure': pressure,
            'running': bool(c),
            'unstable': unstable,
            'recoveryState': recovery_state,
            'lastAction': last_action,
            'lastReason': last_reason,
            'lastEventTs': hist.get('lastEventTs'),
            'restartCount': int(hist.get('restartCount') or 0),
            'recreateCount': int(hist.get('recreateCount') or 0),
            'warnCount': int(hist.get('warnCount') or 0),
        }
        states.append(state)
    return states


def _vm_state_map(vm_states):
    return {v.get('name'): v for v in (vm_states or []) if v.get('name')}


def _is_vm_protected(vm_state):
    if not vm_state:
        return False
    return bool(vm_state.get('protected')) or vm_state.get('profile') in ('interactive', 'gaming')


def _relief_candidates(vm_states):
    ranked = []
    for v in (vm_states or []):
        if not v.get('running'):
            continue
        if v.get('activityClass') != 'idle':
            continue
        if v.get('profile') not in ('background', 'disposable', 'light', 'desktop'):
            continue
        score = 0
        reasons = []
        profile = v.get('profile')
        if profile in ('background', 'disposable'):
            score += 5; reasons.append('low priority profile')
        elif profile == 'light':
            score += 4; reasons.append('light profile')
        else:
            score += 2; reasons.append('idle desktop candidate')
        mem = float(v.get('memPercent') or 0)
        cpu = float(v.get('cpuPercent') or 0)
        score += min(5, int(mem / 20))
        score += min(4, int(cpu / 25))
        if mem >= 60:
            reasons.append(f'high memory ({mem:.0f}%)')
        if cpu >= 30:
            reasons.append(f'non-trivial cpu ({cpu:.0f}%)')
        if v.get('unstable'):
            score += 2; reasons.append('already unstable')
        ranked.append({
            'name': v.get('name'),
            'profile': profile,
            'activityClass': v.get('activityClass'),
            'cpuPercent': round(cpu, 2),
            'memPercent': round(mem, 2),
            'recoveryState': v.get('recoveryState'),
            'score': score,
            'reasons': reasons,
        })
    ranked.sort(key=lambda x: (-int(x.get('score') or 0), -float(x.get('memPercent') or 0), -float(x.get('cpuPercent') or 0), x.get('name') or ''))
    return ranked


def _stop_idle_pressure_vm(cfg: dict, vm_states, host_pressure):
    if host_pressure.get('level') not in ('pressured', 'critical'):
        return None
    candidates = _relief_candidates(vm_states)
    for vm in candidates:
        name = vm.get('name')
        cooldown = int(cfg.get('guardCooldownSeconds', 300))
        if not _action_allowed(name, 'pressure-stop', cooldown):
            continue
        relief = _stop_vm(name, next((x for x in (vm_states or []) if x.get('name') == name), None), cfg, 'pressure-relief', 'pressure-stop')
        if relief:
            relief.update({'pressureLevel': host_pressure.get('level'), 'candidateScore': vm.get('score'), 'candidateReasons': vm.get('reasons')})
            return relief
    return None


def _stop_idle_inactive_vms(cfg: dict, vm_states):
    threshold = max(60, int(cfg.get('idleShutdownSeconds', cfg.get('idleGraceSeconds', 1800)) or 1800))
    for vm in (vm_states or []):
        if not vm.get('running'):
            continue
        if vm.get('activityClass') != 'idle':
            continue
        seconds = vm.get('secondsSinceActivity')
        if seconds is None or int(seconds) < threshold:
            continue
        stopped = _stop_vm(vm.get('name'), vm, cfg, 'idle-timeout', 'idle-shutdown-stop')
        if stopped:
            stopped['idleSeconds'] = int(seconds)
            stopped['thresholdSeconds'] = threshold
            return stopped
    return None


def _can_start_vm(cfg: dict, vm_states, host_pressure, profile: str = 'desktop', force: bool = False):
    if force and cfg.get('allowForceStartUnderPressure', True):
        return {'ok': True, 'reason': 'force override allowed'}
    if not cfg.get('blockStartsOnPressure', True):
        return {'ok': True, 'reason': 'start blocking disabled'}
    level = host_pressure.get('level') or 'healthy'
    active_count = len([v for v in (vm_states or []) if v.get('activityClass') in ('active', 'warm') and v.get('running')])
    protected_count = len([v for v in (vm_states or []) if _is_vm_protected(v) and v.get('running')])
    capacity = _estimate_capacity(cfg, {'mem': {'total': int(host_pressure.get('totalMemoryMb', 0)) * 1024 * 1024, 'available': int(host_pressure.get('availableMemoryMb', 0)) * 1024 * 1024}}, vm_states, host_pressure)
    if level == 'critical':
        return {'ok': False, 'reason': 'Host pressure is critical; new VM starts are temporarily blocked.', 'code': 'host-critical', 'capacity': capacity}
    if profile == 'gaming' and capacity.get('estimatedAdditionalGamingSlots', 0) <= 0:
        return {'ok': False, 'reason': 'No safe additional gaming capacity is available on this host right now.', 'code': 'gaming-capacity', 'capacity': capacity}
    if profile in ('interactive', 'gaming') and capacity.get('estimatedAdditionalInteractiveSlots', 0) <= 0:
        return {'ok': False, 'reason': f'There is no safe interactive capacity left for another {profile} VM right now.', 'code': 'interactive-capacity', 'capacity': capacity}
    if level == 'pressured' and profile in ('gaming', 'interactive'):
        return {'ok': False, 'reason': f'Host pressure is high; refusing to start a {profile} VM right now.', 'code': 'profile-blocked', 'capacity': capacity}
    if level == 'pressured' and active_count >= 2 and protected_count >= 1:
        return {'ok': False, 'reason': 'There are already active protected VMs; starting another VM may degrade responsiveness.', 'code': 'capacity-guard', 'capacity': capacity}
    return {'ok': True, 'reason': 'capacity available', 'capacity': capacity}


def _estimate_capacity(cfg: dict, stats: dict, vm_states, host_pressure: dict):
    available_mb = int(host_pressure.get('availableMemoryMb') or 0)
    total_mb = int(host_pressure.get('totalMemoryMb') or 0)
    reserve_mb = int(cfg.get('minAvailableMemoryMb', 2048) or 2048)
    free_for_vms_mb = max(0, available_mb - reserve_mb)
    active = [v for v in (vm_states or []) if v.get('running') and v.get('activityClass') in ('active', 'warm')]
    gaming = [v for v in active if v.get('profile') == 'gaming']
    interactive = [v for v in active if v.get('profile') in ('interactive', 'gaming')]
    soft_limit = float(cfg.get('hostCpuSoftLimit', 75) or 75)
    cpu_headroom = max(0.0, soft_limit - float(host_pressure.get('vmCpuTotal') or 0.0))
    game_mem_budget = max(256, int(cfg.get('gamingVmMemoryMb', 3072) or 3072))
    game_cpu_budget = max(1.0, float(cfg.get('gamingVmCpuBudgetPercent', 30) or 30))
    interactive_mem_budget = max(256, int(cfg.get('interactiveVmMemoryMb', 2048) or 2048))
    interactive_cpu_budget = max(1.0, float(cfg.get('interactiveVmCpuBudgetPercent', 20) or 20))
    est_game_slots_by_mem = max(0, int(free_for_vms_mb / game_mem_budget)) if free_for_vms_mb else 0
    est_game_slots_by_cpu = max(0, int(cpu_headroom / game_cpu_budget)) if cpu_headroom else 0
    est_interactive_slots_by_mem = max(0, int(free_for_vms_mb / interactive_mem_budget)) if free_for_vms_mb else 0
    est_interactive_slots_by_cpu = max(0, int(cpu_headroom / interactive_cpu_budget)) if cpu_headroom else 0
    projected_game_capacity = min(est_game_slots_by_mem, est_game_slots_by_cpu)
    projected_interactive_capacity = min(est_interactive_slots_by_mem, est_interactive_slots_by_cpu)
    suitability = 'good'
    if host_pressure.get('level') == 'critical' or projected_game_capacity <= 0:
        suitability = 'poor'
    elif host_pressure.get('level') == 'pressured' or projected_game_capacity == 1:
        suitability = 'tight'
    return {
        'availableMemoryMb': available_mb,
        'reserveMemoryMb': reserve_mb,
        'freeForVmsMb': free_for_vms_mb,
        'cpuHeadroomPercent': round(cpu_headroom, 2),
        'activeVmCount': len(active),
        'interactiveVmCount': len(interactive),
        'gamingVmCount': len(gaming),
        'estimatedAdditionalGamingSlots': projected_game_capacity,
        'estimatedAdditionalInteractiveSlots': projected_interactive_capacity,
        'gamingSuitability': suitability,
        'totalMemoryMb': total_mb,
        'densityProfile': cfg.get('densityProfile', 'single-user'),
        'gamingVmCpuBudgetPercent': game_cpu_budget,
        'interactiveVmCpuBudgetPercent': interactive_cpu_budget,
        'gamingVmMemoryMb': game_mem_budget,
        'interactiveVmMemoryMb': interactive_mem_budget,
    }


def _recommend_vm_action(vm_state: dict, host_pressure: dict, capacity: dict):
    if not isinstance(vm_state, dict):
        return {'action': 'observe', 'label': 'Observe', 'detail': 'No VM state available.'}
    recovery = str(vm_state.get('recoveryState') or 'healthy').lower()
    profile = str(vm_state.get('profile') or 'desktop')
    protected = bool(vm_state.get('protected'))
    unstable = bool(vm_state.get('unstable'))
    activity = str(vm_state.get('activityClass') or 'idle')
    if recovery == 'restart-loop' or unstable:
        return {'action': 'escalate', 'label': 'Escalate or recreate', 'detail': 'Repeated disruptive interventions detected; this VM likely needs deeper repair.'}
    if recovery.startswith('protected-'):
        return {'action': 'cautious-recover', 'label': 'Cautious recovery', 'detail': 'The VM looks important/active, so avoid aggressive rebuilds unless manually approved.'}
    if recovery in ('recovering', 'restarting'):
        return {'action': 'wait', 'label': 'Wait for recovery', 'detail': 'Recovery is already in flight; watch trend and logs before acting again.'}
    if recovery == 'degraded':
        return {'action': 'recover', 'label': 'Try recovery', 'detail': 'The VM looks degraded but not yet in a loop; a standard recovery attempt is reasonable.'}
    if host_pressure.get('level') in ('pressured', 'critical') and activity == 'idle' and profile in ('background', 'disposable', 'light', 'desktop'):
        return {'action': 'stop', 'label': 'Stop to relieve pressure', 'detail': 'This idle VM is a reasonable pressure-relief candidate.'}
    if profile == 'gaming' and capacity.get('gamingSuitability') in ('tight', 'poor'):
        return {'action': 'preserve', 'label': 'Preserve and reduce neighbors', 'detail': 'Gaming VM should stay up; reduce density around it instead.'}
    return {'action': 'observe', 'label': 'Observe', 'detail': 'No immediate optimizer action recommended.'}


def _build_recommendations(cfg: dict, stats: dict, vm_states, host_pressure):
    recs = []
    capacity = _estimate_capacity(cfg, stats, vm_states, host_pressure)
    if host_pressure.get('level') in ('pressured', 'critical'):
        recs.append({
            'level': 'warn',
            'title': 'Host pressure is elevated',
            'detail': '; '.join(host_pressure.get('reasons') or ['pressure increasing'])
        })
    if capacity.get('gamingSuitability') == 'poor':
        recs.append({
            'level': 'warn',
            'title': 'Host is currently a poor fit for new gaming sessions',
            'detail': f"Estimated additional gaming slots: {capacity.get('estimatedAdditionalGamingSlots', 0)}; CPU headroom {capacity.get('cpuHeadroomPercent', 0)}%; free VM memory budget {capacity.get('freeForVmsMb', 0)} MB"
        })
    elif capacity.get('gamingSuitability') == 'tight':
        recs.append({
            'level': 'info',
            'title': 'Gaming capacity is tight',
            'detail': f"Estimated additional gaming slots: {capacity.get('estimatedAdditionalGamingSlots', 0)}; active interactive VMs: {capacity.get('interactiveVmCount', 0)}"
        })
    idle_background = [v for v in vm_states if v.get('activityClass') == 'idle' and v.get('profile') in ('background', 'disposable') and v.get('running')]
    if idle_background and host_pressure.get('level') in ('pressured', 'critical'):
        recs.append({
            'level': 'info',
            'title': 'Stop idle background VMs before disruptive recovery',
            'detail': 'Candidates: ' + ', '.join(v['name'] for v in idle_background[:4])
        })
    protected_hot = [v for v in vm_states if v.get('protected') and v.get('pressure') == 'high']
    if protected_hot:
        recs.append({
            'level': 'info',
            'title': 'Protected active VMs are consuming heavy resources',
            'detail': 'Consider increasing host headroom or lowering VM density for: ' + ', '.join(v['name'] for v in protected_hot[:4])
        })
    unstable = [v['name'] for v in vm_states if v.get('unstable')]
    if unstable:
        recs.append({
            'level': 'warn',
            'title': 'Some VMs look unstable',
            'detail': 'Repeated optimizer interventions detected for: ' + ', '.join(unstable[:5])
        })
    recovering = [v['name'] for v in vm_states if str(v.get('recoveryState', '')).startswith('recover') or str(v.get('recoveryState', '')).startswith('restarting')]
    if recovering:
        recs.append({
            'level': 'info',
            'title': 'Some VMs are in recovery flow',
            'detail': 'Currently recovering/restarting: ' + ', '.join(recovering[:5])
        })
    protected_degraded = [v['name'] for v in vm_states if str(v.get('recoveryState', '')).startswith('protected-')]
    if protected_degraded:
        recs.append({
            'level': 'info',
            'title': 'Protected VMs are degraded but being preserved',
            'detail': 'Manual review may be better than forced recovery for: ' + ', '.join(protected_degraded[:5])
        })
    unknown_profiles = [v['name'] for v in vm_states if v.get('profile') == 'desktop']
    if unknown_profiles:
        recs.append({
            'level': 'info',
            'title': 'Assign explicit VM profiles',
            'detail': 'Still using default desktop profile: ' + ', '.join(unknown_profiles[:5])
        })
    if not recs:
        recs.append({'level': 'ok', 'title': 'No optimizer recommendations right now', 'detail': 'Host pressure is stable and VM activity looks calm.'})
    return recs[:8]


def run_once():
    cfg = load_config()
    events = []
    max_actions = max(1, int(cfg.get('maxActionsPerRun', 3)))
    try:
        pre_stats = gather_stats()
        host_pressure = _derive_host_pressure(pre_stats, cfg)
        vm_states = _derive_vm_states(cfg, pre_stats)
        _apply_cpu_priority(cfg, vm_states)
        vm_state_map = _vm_state_map(vm_states)
        idle_shutdown = _stop_idle_inactive_vms(cfg, vm_states)
        if idle_shutdown:
            events.append(idle_shutdown)
        if host_pressure.get('level') in ('pressured', 'critical') and len(events) < max_actions:
            relief = _stop_idle_pressure_vm(cfg, vm_states, host_pressure)
            if relief:
                events.append(relief)
        guards = []
        if cfg.get('guards', {}).get('memory'):
            guards.append(_run_memory_guard)
        if cfg.get('guards', {}).get('cpu') and host_pressure.get('level') != 'critical':
            guards.append(_run_cpu_guard)
        if cfg.get('guards', {}).get('swap'):
            guards.append(_run_swap_guard)
        if cfg.get('guards', {}).get('health'):
            guards.append(_run_health_guard)
        for guard in guards:
            if len(events) >= max_actions:
                log(f'maxActionsPerRun reached ({max_actions}), stopping guard execution early')
                break
            r = guard(cfg, vm_state_map=vm_state_map, host_pressure=host_pressure)
            if r:
                events.append(r)
        if cfg.get('strictMemoryLimit'):
            try:
                enforce_strict_memory(cfg)
            except Exception as e:
                log(f'error enforcing strictMemoryLimit: {e}')
        for ev in events:
            _record_history_event(ev)
        post_stats = gather_stats()
        post_host_pressure = _derive_host_pressure(post_stats, cfg)
        post_vm_states = _derive_vm_states(cfg, post_stats)
        post_capacity = _estimate_capacity(cfg, post_stats, post_vm_states, post_host_pressure)
        post_vm_states = [dict(v, recommendedAction=_recommend_vm_action(v, post_host_pressure, post_capacity)) for v in post_vm_states]
        _record_trend_point(post_host_pressure, post_capacity, post_vm_states)
        payload_stats = {
            'raw': post_stats,
            'hostPressure': post_host_pressure,
            'vmStates': post_vm_states,
            'capacity': post_capacity,
            'reliefCandidates': _relief_candidates(post_vm_states),
            'recommendations': _build_recommendations(cfg, post_stats, post_vm_states, post_host_pressure),
            'history': _history_state(),
            'trends': _trend_state(),
        }
    except Exception as e:
        log(f'error in run_once: {e}')
        payload_stats = {'raw': gather_stats()}
    _record_last_run(events, payload_stats)
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
    raw_stats = gather_stats()
    host_pressure = _derive_host_pressure(raw_stats, cfg)
    vm_states = _derive_vm_states(cfg, raw_stats)
    capacity = _estimate_capacity(cfg, raw_stats, vm_states, host_pressure)
    vm_states = [dict(v, recommendedAction=_recommend_vm_action(v, host_pressure, capacity)) for v in vm_states]
    stats = {
        'raw': raw_stats,
        'hostPressure': host_pressure,
        'vmStates': vm_states,
        'capacity': capacity,
        'reliefCandidates': _relief_candidates(vm_states),
        'recommendations': _build_recommendations(cfg, raw_stats, vm_states, host_pressure),
        'profiles': load_profiles(),
        'densityProfiles': available_density_profiles(),
        'history': _history_state(),
        'trends': _trend_state(),
    }
    last = 0
    last_run = {}
    try:
        if os.path.isfile(LAST_RESTART_PATH):
            last = int(open(LAST_RESTART_PATH, 'r').read().strip())
    except Exception:
        last = 0
    try:
        if os.path.isfile(LAST_RUN_PATH):
            last_run = json.load(open(LAST_RUN_PATH, 'r'))
    except Exception:
        last_run = {}
    return {'cfg': cfg, 'stats': stats, 'lastRestart': last, 'lastRun': last_run}


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
