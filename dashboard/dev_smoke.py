#!/usr/bin/env python3
"""
Local smoke test for the BlobeVM dashboard.
- Imports the Flask app from dashboard/app.py
- Exercises a few endpoints with Flask's test client
- Authenticates via /Dashboard/api/auth/login when BLOBEDASH_USER/PASS are set
  to real (non-placeholder) credentials. The dashboard requires a session, so
  without usable creds it only asserts the app imports and the auth-gated routes
  respond (401/403) rather than crashing.
Run:
  python3 dashboard/dev_smoke.py
  BLOBEDASH_USER=operator BLOBEDASH_PASS=secret python3 dashboard/dev_smoke.py
"""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_PATH = HERE / 'app.py'

spec = importlib.util.spec_from_file_location('blobedash', str(APP_PATH))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

client = mod.app.test_client()

# Only attempt auth when real plaintext creds exist. Deployments typically set
# BLOBEDASH_PASS_HASH (scrypt) and may leave BLOBEDASH_PASS as a '***' placeholder,
# in which case we can't log in and simply skip the authed assertions.
creds_user = os.environ.get('BLOBEDASH_USER')
creds_pass = os.environ.get('BLOBEDASH_PASS')
have_creds = bool(creds_user) and bool(creds_pass) and creds_pass != '***'
headers = {}
if have_creds:
    login = client.post(
        '/Dashboard/api/auth/login',
        json={'username': creds_user, 'password': creds_pass},
        headers={'Origin': 'http://localhost'},
    )
    assert login.status_code == 200, f'login failed: {login.status_code} {login.data}'
    cookie = login.headers.get('Set-Cookie', '')
    if cookie:
        headers['Cookie'] = cookie.split(';')[0]
else:
    print('(no usable BLOBEDASH_USER/PASS; authed assertions skipped)')

# /dashboard should return HTML for an authed session. Without creds it is
# expected to be auth-gated (401/403) rather than crashing.
r = client.get('/dashboard', headers=headers)
print('DASHBOARD', r.status_code, 'bytes:', len(r.data))
if have_creds:
    assert r.status_code == 200, 'Expected 200 from /dashboard with session'
else:
    assert r.status_code in (200, 401, 403), 'dashboard route should not error'

# /modeinfo should be JSON with required keys (public route)
r = client.get('/dashboard/api/modeinfo', headers=headers)
print('MODEINFO', r.status_code, r.json)
assert r.status_code == 200 and isinstance(r.json, dict), 'modeinfo should be JSON'
for k in ['merged', 'basePath', 'domain', 'dashPort', 'ip']:
    assert k in r.json, f'modeinfo missing key: {k}'

# /list should be JSON with instances array (may be empty locally)
r = client.get('/dashboard/api/list', headers=headers)
print('LIST', r.status_code, r.json)
assert r.status_code == 200 and 'instances' in r.json, 'list should return instances'

print('\nSmoke test OK')
