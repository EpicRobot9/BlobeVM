#!/usr/bin/env bash
set -eu
echo ROUTER_MIDDLEWARE_MATCHES
docker ps -aq | sort -u | while read -r id; do
  [ -n "$id" ] || continue
  docker inspect "$id" --format '{{json .Config.Labels}}' | python3 -c 'import json,sys; v=json.load(sys.stdin); [print(k+"="+str(x)) for k,x in sorted(v.items()) if "epicvm-pilot-01-auth" in k or "epicvm-pilot-01.rule" in k or "epicvm-pilot-01.middlewares" in k]' || true
done
echo LOCAL_ORIGIN
for p in /vm/epicvm-pilot-01/ /vm/epicvm-pilot-01-moonlight/; do
  echo -n "$p "
  curl -ksS --resolve techexplore.us:443:127.0.0.1 -D - -o /dev/null --max-time 10 "https://techexplore.us$p" | sed -n '1,8p' | tr '\n' ' '; echo
done
echo TRAEFIK_NAMES
docker ps --format '{{.Names}}' | grep -i traefik | head -5
