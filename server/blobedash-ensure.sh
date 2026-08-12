#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/opt/blobe-vm/.env"
STATE_DIR="/opt/blobe-vm"
APP_PATH="/opt/blobe-vm/dashboard/app.py"
NAME="blobedash"
IMAGE_HASH_FILE="/opt/blobe-vm/.blobedash-image.hash"

# Load env file if present
if [[ -f "$ENV_FILE" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^# ]] && continue
    v="${v%\'}"; v="${v#\'}"; v="${v%\"}"; v="${v#\"}"
    export "$k"="$v"
  done < "$ENV_FILE"
fi

IMAGE_NAME="${EPICVM_BLOBEDASH_IMAGE:-blobedash:local}"

NO_TRAEFIK=${NO_TRAEFIK:-0}
ENABLE_DASHBOARD=${ENABLE_DASHBOARD:-0}
DIRECT_PORT_START=${DIRECT_PORT_START:-20000}
HOST_DOCKER_BIN=${HOST_DOCKER_BIN:-}
HOST_DOCKER_COMPOSE_BIN=${HOST_DOCKER_COMPOSE_BIN:-}

if [[ -z "$HOST_DOCKER_BIN" || ! -e "$HOST_DOCKER_BIN" ]]; then
  HOST_DOCKER_BIN="$(command -v docker || true)"
fi

if [[ -z "$HOST_DOCKER_COMPOSE_BIN" || ! -x "$HOST_DOCKER_COMPOSE_BIN" ]]; then
  for candidate in /usr/libexec/docker/cli-plugins/docker-compose /usr/lib/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose; do
    if [[ -x "$candidate" ]]; then HOST_DOCKER_COMPOSE_BIN="$candidate"; break; fi
  done
fi

if [[ -z "$HOST_DOCKER_COMPOSE_BIN" || ! -x "$HOST_DOCKER_COMPOSE_BIN" ]]; then
  echo "Unable to locate the Docker Compose plugin for console orchestration." >&2
  exit 1
fi

if [[ -z "$HOST_DOCKER_BIN" || ! -e "$HOST_DOCKER_BIN" ]]; then
  echo "Unable to locate docker CLI for dashboard ensure script." >&2
  exit 1
fi

# Note: we always run the dashboard in direct mode now. If a proxy exists, you can still access it via IP:port.
# If dashboard disabled, nothing to do
if [[ "$ENABLE_DASHBOARD" -ne 1 ]]; then
  exit 0
fi

port_in_use() {
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | awk '{print $4}' | grep -E "(^|:)${p}$" >/dev/null 2>&1
  else
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -E "(^|:)${p}$" >/dev/null 2>&1
  fi
}

find_free_port() {
  local start="$1"; local attempts="${2:-1000}"; local p="$start"; local i=0
  while (( i < attempts )); do
    if ! port_in_use "$p"; then echo "$p"; return 0; fi
    p=$((p+1)); i=$((i+1))
  done
  return 1
}

blobedash_build_hash() {
  local hash_input=""
  for p in \
    "$STATE_DIR/dashboard/app.py" \
    "$STATE_DIR/dashboard/guacamole_orchestrator.py" \
    "$STATE_DIR/dashboard/moonlight_orchestrator.py" \
    "$STATE_DIR/dashboard/remote_agent_client.py" \
    "$STATE_DIR/dashboard/optimizer.py" \
    "$STATE_DIR/server/blobedash.Dockerfile"
  do
    if [[ -f "$p" ]]; then
      hash_input+="$(sha256sum "$p")\n"
    fi
  done
  printf "%b" "$hash_input" | sha256sum | awk '{print $1}'
}

ensure_blobedash_image() {
  # A promoted release may already be present as an immutable image.  Reuse
  # it across service restarts rather than rebuilding through a package
  # network that may be unavailable; the operator changes the tag explicitly
  # when a new release is ready.
  if [[ -n "${EPICVM_BLOBEDASH_IMAGE:-}" ]] && docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    return 0
  fi
  local dockerfile="$STATE_DIR/server/blobedash.Dockerfile"
  [[ -f "$dockerfile" ]] || { echo "blobedash Dockerfile not found: $dockerfile" >&2; exit 1; }
  local new_hash old_hash image_id build_ctx
  new_hash="$(blobedash_build_hash)"
  old_hash="$(cat "$IMAGE_HASH_FILE" 2>/dev/null || true)"
  image_id="$(docker images -q "$IMAGE_NAME" 2>/dev/null | head -n1 || true)"
  if [[ -n "$image_id" && -n "$old_hash" && "$old_hash" == "$new_hash" ]]; then
    return 0
  fi
  build_ctx="$(mktemp -d)"
  trap 'rm -rf "$build_ctx"' RETURN
  cp -f "$dockerfile" "$build_ctx/Dockerfile"
  echo "Building $IMAGE_NAME ..."
  docker build -t "$IMAGE_NAME" "$build_ctx"
  printf "%s" "$new_hash" > "$IMAGE_HASH_FILE"
  rm -rf "$build_ctx"
  trap - RETURN
}

# Ensure dashboard app exists
if [[ ! -f "$APP_PATH" ]]; then
  if [[ -n "${REPO_DIR:-}" && -f "${REPO_DIR}/dashboard/app.py" ]]; then
    mkdir -p "$(dirname "$APP_PATH")"
    cp -f "${REPO_DIR}/dashboard/app.py" "$APP_PATH"
  else
    echo "dashboard app not found at $APP_PATH and REPO_DIR unknown" >&2
  fi
fi


container_uses_port() {
  local name="$1"; local port="$2"
  docker inspect "$name" --format '{{range $k,$v := .HostConfig.PortBindings}}{{range $v}}{{.HostPort}}{{"\n"}}{{end}}{{end}}' 2>/dev/null | grep -qx "$port"
}

# Determine or assign port
DASHBOARD_PORT=${DASHBOARD_PORT:-}
# If no port set or the current one is busy for something other than the current blobedash container, (re)assign a free port
if [[ -z "$DASHBOARD_PORT" ]] || { port_in_use "$DASHBOARD_PORT" && ! container_uses_port "$NAME" "$DASHBOARD_PORT"; }; then
  new_port=$(find_free_port "$DIRECT_PORT_START" 1000 || true)
  if [[ -z "$new_port" ]]; then
    echo "Unable to find a free port for dashboard" >&2
    exit 1
  fi
  DASHBOARD_PORT="$new_port"
  if [[ -f "$ENV_FILE" ]]; then
    if grep -q '^DASHBOARD_PORT=' "$ENV_FILE"; then
      sed -i -E "s|^DASHBOARD_PORT=.*|DASHBOARD_PORT='$DASHBOARD_PORT'|" "$ENV_FILE"
    else
      printf "\nDASHBOARD_PORT='%s'\n" "$DASHBOARD_PORT" >> "$ENV_FILE"
    fi
  fi
fi

ensure_blobedash_image
install -d -m 700 /opt/epicvm /opt/epicvm/instances /opt/epicvm/moonlight-instances

# Recreate container to ensure correct port mapping
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
fi

NET_ARGS=()
if docker network inspect proxy >/dev/null 2>&1; then
  NET_ARGS+=(--network proxy)
  NET_ARGS+=(--label 'traefik.enable=true')
  NET_ARGS+=(--label 'com.blobevm.managed=1')
  NET_ARGS+=(--label 'traefik.docker.network=proxy')
  NET_ARGS+=(--label 'traefik.http.services.blobedash.loadbalancer.server.port=5000')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash.rule=PathPrefix(`/dashboard`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash.entrypoints=web')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-secure.rule=PathPrefix(`/dashboard`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-secure.entrypoints=websecure')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-secure.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-secure.tls=true')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-secure.tls.certresolver=myresolver')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2.rule=PathPrefix(`/Dashboard`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2.entrypoints=web')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2-secure.rule=PathPrefix(`/Dashboard`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2-secure.entrypoints=websecure')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2-secure.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2-secure.tls=true')
  NET_ARGS+=(--label 'traefik.http.routers.blobedashv2-secure.tls.certresolver=myresolver')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static.rule=PathPrefix(`/static/`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static.entrypoints=web')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static-secure.rule=PathPrefix(`/static/`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static-secure.entrypoints=websecure')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static-secure.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static-secure.tls=true')
  NET_ARGS+=(--label 'traefik.http.routers.blobedash-static-secure.tls.certresolver=myresolver')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper.rule=PathPrefix(`/vm/`) || PathPrefix(`/portal`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper.entrypoints=web')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper.priority=10')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper-secure.rule=PathPrefix(`/vm/`) || PathPrefix(`/portal`)')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper-secure.entrypoints=websecure')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper-secure.service=blobedash')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper-secure.tls=true')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper-secure.priority=10')
  NET_ARGS+=(--label 'traefik.http.routers.blobevm-wrapper-secure.tls.certresolver=myresolver')
fi

docker run -d --name "$NAME" --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -p "${DASHBOARD_PORT}:5000" \
  "${NET_ARGS[@]}" \
  -v "$STATE_DIR:/opt/blobe-vm" \
  -v /opt/epicvm:/opt/epicvm \
  -v /var/blobe:/var/blobe \
  -v /usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro \
  -v "${HOST_DOCKER_BIN}:/usr/bin/docker:ro" \
  -v "${HOST_DOCKER_COMPOSE_BIN}:/usr/libexec/docker/cli-plugins/docker-compose:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$STATE_DIR/dashboard:/app:ro" \
  -e BLOBEVM_ALLOW_INSECURE_DASHBOARD="${BLOBEVM_ALLOW_INSECURE_DASHBOARD:-0}" \
  -e HOST_DOCKER_BIN="${HOST_DOCKER_BIN}" \
  -e EPICVM_CONSOLE_ROOT="${EPICVM_CONSOLE_ROOT:-/opt/epicvm/instances}" \
  -e EPICVM_CONSOLE_BACKEND="${EPICVM_CONSOLE_BACKEND:-moonlight}" \
  -e EPICVM_MOONLIGHT_ROOT="${EPICVM_MOONLIGHT_ROOT:-/opt/epicvm/moonlight-instances}" \
  -e EPICVM_TRAEFIK_NETWORK="${EPICVM_TRAEFIK_NETWORK:-}" \
  -e EPICVM_PUBLIC_HOST="${EPICVM_PUBLIC_HOST:-}" \
  -e EPICVM_TRAEFIK_CERTRESOLVER="${EPICVM_TRAEFIK_CERTRESOLVER:-}" \
  -e EPICVM_TRAEFIK_AUTH_MIDDLEWARE="${EPICVM_TRAEFIK_AUTH_MIDDLEWARE:-}" \
  -e EPICVM_TRAEFIK_ROUTER_PRIORITY="${EPICVM_TRAEFIK_ROUTER_PRIORITY:-}" \
  -e EPICVM_GUACAMOLE_IMAGE="${EPICVM_GUACAMOLE_IMAGE:-}" \
  -e EPICVM_GUACD_IMAGE="${EPICVM_GUACD_IMAGE:-}" \
  -e EPICVM_POSTGRES_IMAGE="${EPICVM_POSTGRES_IMAGE:-}" \
  -e EPICVM_MOONLIGHT_IMAGE="${EPICVM_MOONLIGHT_IMAGE:-}" \
  "$IMAGE_NAME" \
  >/dev/null

echo "Dashboard: http://$(hostname -I | awk '{print $1}'):${DASHBOARD_PORT}/dashboard"
