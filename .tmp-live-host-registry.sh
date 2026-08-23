#!/usr/bin/env bash
set -u
python3 - <<'PY'
import json, os, stat
paths=(
 "/opt/blobe-vm/remote-hosts.json",
 "/opt/blobe-vm/remote-hosts.json.inventory.json",
 "/var/blobe/remote-hosts.json",
 "/var/blobe/remote-hosts.json.inventory.json",
)
for p in paths:
 print("FILE",p,"exists",os.path.exists(p))
 if not os.path.exists(p): continue
 s=os.stat(p)
 print("META","mode=%03o"%(s.st_mode&0o777),"uid",s.st_uid,"gid",s.st_gid,"size",s.st_size)
 if p.endswith("remote-hosts.json"):
  try:
   v=json.load(open(p)); rs=v.get("hosts",v) if isinstance(v,dict) else v
   if isinstance(rs,dict): rs=list(rs.values())
   print("RECORD_COUNT",len(rs) if isinstance(rs,list) else "na")
   if isinstance(rs,list):
    for r in rs:
     if isinstance(r,dict):
      print("RECORD", {k:r.get(k) for k in ("id","display_name","agent_url","provider","platform","enabled","timeout")})
  except Exception as e: print("PARSE",type(e).__name__)
PY
echo CONTAINER_ENV
docker inspect blobedash --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(BLOBEDASH_STATE|BLOBEVM_REMOTE_HOSTS_FILE|EPICVM_REMOTE_HOSTS_FILE|EPICVM_ALLOW_NON_TAILSCALE_HOSTS|EPICVM_CONSOLE_BACKEND|EPICVM_MOONLIGHT_ROOT|EPICVM_CONSOLE_ROOT)=' | sed -E 's#^(BLOBEDASH_STATE|BLOBEVM_REMOTE_HOSTS_FILE|EPICVM_REMOTE_HOSTS_FILE|EPICVM_ALLOW_NON_TAILSCALE_HOSTS|EPICVM_CONSOLE_BACKEND|EPICVM_MOONLIGHT_ROOT|EPICVM_CONSOLE_ROOT)=#\1=#'
