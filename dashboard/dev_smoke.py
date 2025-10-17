#!/usr/bin/env python3
"""
Local smoke test for the BlobeVM dashboard.
- Imports the Flask app from dashboard/app.py
- Exercies a few endpoints with Flask's test client
- Adds optional Basic Auth if BLOBEDASH_USER/PASS are set
Run:
  python3 dashboard/dev_smoke.py
"""
import base64
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_PATH = HERE / 'app.py'

spec = importlib.util.spec_from_file_location('blobedash', str(APP_PATH))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

client = mod.app.test_client()
headers = {}
user = os.environ.get('BLOBEDASH_USER')
password = os.environ.get('BLOBEDASH_PASS')
if user and password:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers['Authorization'] = f"Basic {token}"

# /dashboard should return HTML
r = client.get('/dashboard', headers=headers)
print('DASHBOARD', r.status_code, 'bytes:', len(r.data))
assert r.status_code == 200, 'Expected 200 from /dashboard'

# /modeinfo should be JSON with required keys
r = client.get('/dashboard/api/modeinfo', headers=headers)
print('MODEINFO', r.status_code, r.json)
assert r.status_code == 200 and isinstance(r.json, dict), 'modeinfo should be JSON'
for k in ['merged','basePath','domain','dashPort','ip']:
    assert k in r.json, f'modeinfo missing key: {k}'

# /list should be JSON with instances array (may be empty locally)
r = client.get('/dashboard/api/list', headers=headers)
print('LIST', r.status_code, r.json)
assert r.status_code == 200 and 'instances' in r.json, 'list should return instances'

print('\nSmoke test OK')
