#!/usr/bin/env bash
set -u
python3 - <<'PY'
import json, os
paths=(
 "/opt/epicvm/templates/win11-25h2/manifest.json",
 "/opt/epicvm/provisioning-jobs.json",
 "/opt/epicvm/remote-hosts.json",
 "/opt/blobe-vm/.env",
)
for p in paths:
 print("FILE",p,"exists",os.path.exists(p))
 if not os.path.exists(p):
  continue
 st=os.stat(p)
 print("META","mode=%03o"% (st.st_mode & 0o777),"uid",st.st_uid,"gid",st.st_gid,"size",st.st_size)
 if p.endswith("manifest.json"):
  try:
   v=json.load(open(p))
   print("MANIFEST_KEYS",sorted(v) if isinstance(v,dict) else type(v).__name__)
   for k in ("name","template","format","diskType","sha256","sizeBytes","immutable"):
    if isinstance(v,dict) and k in v:
     val=v[k]
     print("MANIFEST",k, ("set" if k=="sha256" else val if k in ("name","template","format","diskType","immutable") else type(val).__name__))
  except Exception as e: print("MANIFEST_PARSE",type(e).__name__)
 elif p.endswith("provisioning-jobs.json"):
  try:
   v=json.load(open(p))
   print("JOBS",type(v).__name__,len(v) if hasattr(v,"__len__") else "na")
  except Exception as e: print("JOBS_PARSE",type(e).__name__)
 elif p.endswith("remote-hosts.json"):
  try:
   v=json.load(open(p))
   print("REMOTE_HOSTS_TYPE",type(v).__name__)
   records=v.get("hosts",v) if isinstance(v,dict) else v
   if isinstance(records,dict): records=list(records.values())
   if isinstance(records,list):
    for r in records:
     if isinstance(r,dict):
      print("REMOTE_HOST", {k:r.get(k) for k in ("id","display_name","agent_url","provider","platform")})
  except Exception as e: print("REMOTE_HOSTS_PARSE",type(e).__name__)
 else:
  keys=[]
  for line in open(p,errors="replace"):
   if "=" in line:
    k=line.split("=",1)[0].strip()
    if k.startswith("EPICVM_"): keys.append(k)
  print("ENV_KEYS",sorted(set(keys)))
PY
echo REMOTE_CONFIG_FILES
find /opt/epicvm -maxdepth 3 -type f \( -iname '*host*.json' -o -iname '*config*.json' \) -printf '%p\n' 2>/dev/null | head -40
echo DASH_HEALTH
curl -ksS -o /dev/null -w 'dashboard_status=%{http_code}\n' --max-time 10 http://127.0.0.1:20000/dashboard/api/v2status || true
