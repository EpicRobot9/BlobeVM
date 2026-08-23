#!/usr/bin/env bash
set -u
echo SERVICE
systemctl is-active blobedash.service 2>/dev/null || true
docker inspect blobedash --format 'image={{.Image}} status={{.State.Status}} exit={{.State.ExitCode}} restart={{.RestartCount}}'
echo ENV
docker inspect blobedash --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^EPICVM_CONSOLE_BACKEND=|^EPICVM_MOONLIGHT_ROOT=|^EPICVM_CONSOLE_ROOT=' | sed -E 's#^(EPICVM_[A-Z_]+)=.*#\1=set#'
echo ROUTES
for p in /vm/epicvm-pilot-01/ /vm/epicvm-pilot-01-moonlight/ /vm/testre/ /portal/; do
  echo -n "$p="
  curl -ksS --resolve techexplore.us:443:127.0.0.1 -o /dev/null -w '%{http_code}\n' --max-time 10 "https://techexplore.us$p" || true
done
echo CONTAINERS
docker ps -a --format '{{.Names}}={{.Status}}' | grep -E 'epicvm-pilot-01|epicvm-testre-rdp' || true
echo PLANS
for p in /opt/epicvm/moonlight-instances/epicvm-pilot-01/plan.json /opt/epicvm/instances/epicvm-pilot-01/plan.json; do
  if [ -f "$p" ]; then
    python3 - "$p" <<'PY'
import json,sys
v=json.load(open(sys.argv[1]))
print(sys.argv[1], {k:v.get(k) for k in ("owner","backend","name","guestIp","routePrefix","paired")})
PY
  fi
done
echo MOUNTS
docker inspect blobedash --format '{{range .Mounts}}{{println .Source " -> " .Destination}}{{end}}' | grep -E '/opt/epicvm|/opt/blobe-vm'
echo ROUTER_LABELS
docker ps -q | while read -r id; do
  docker inspect "$id" --format '{{json .Config.Labels}}' 2>/dev/null | python3 -c 'import json,sys; v=json.load(sys.stdin); [print(k+"="+str(x)) for k,x in sorted(v.items()) if "traefik.http.routers" in k and ("epicvm-pilot-01" in k or "testre" in k)]'
done
