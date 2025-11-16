#!/usr/bin/env python3
"""CLI wrapper to call the dashboard optimizer module for use from blobe-vm-manager.
Provides: optimize <vmName>|all, stats <vmName>|all, health-check <vmName>
"""
import sys, os, json
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Ensure dashboard module importable
sys.path.insert(0, os.path.join(ROOT, 'dashboard'))
try:
    from optimize import Optimizer
except Exception as e:
    print('optimizer-missing', e, file=sys.stderr)
    sys.exit(2)

state_dir = os.environ.get('BLOBEDASH_STATE', '/opt/blobe-vm')
opt = Optimizer(state_dir=state_dir)
opt.start()

def cmd_stats(name=None):
    s = opt.get_stats()
    if name:
        print(json.dumps(s.get(name, {})))
    else:
        print(json.dumps(s))

def cmd_optimize(name):
    if name == 'all':
        names = [n for n in os.listdir(os.path.join(state_dir, 'instances')) if os.path.isdir(os.path.join(state_dir, 'instances', n))]
        for n in names:
            print('optimize', n, '->', 'ok' if opt.trigger_optimize(n) else 'fail')
    else:
        ok = opt.trigger_optimize(name)
        print('ok' if ok else 'fail')

def cmd_health_check(name):
    s = opt.get_stats().get(name)
    if not s:
        print('no-stats')
        return
    print(json.dumps(s))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: blobe-optimize.py <stats|optimize|health-check> [vmName|all]')
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == 'stats':
        name = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_stats(name)
    elif cmd == 'optimize':
        name = sys.argv[2] if len(sys.argv) > 2 else None
        if not name:
            print('provide vmName or all'); sys.exit(2)
        cmd_optimize(name)
    elif cmd == 'health-check' or cmd == 'health':
        name = sys.argv[2] if len(sys.argv) > 2 else None
        if not name:
            print('provide vmName'); sys.exit(2)
        cmd_health_check(name)
    else:
        print('unknown cmd', cmd); sys.exit(2)
