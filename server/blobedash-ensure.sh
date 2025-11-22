#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/opt/blobe-vm/.env"
STATE_DIR="/opt/blobe-vm"
APP_PATH="/opt/blobe-vm/dashboard/app.py"
NAME="blobedash"

# Load env file if present
if [[ -f "$ENV_FILE" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^# ]] && continue
    v="${v%\'}"; v="${v#\'}"; v="${v%\"}"; v="${v#\"}"
    export "$k"="$v"
  done < "$ENV_FILE"
fi

NO_TRAEFIK=${NO_TRAEFIK:-0}
ENABLE_DASHBOARD=${ENABLE_DASHBOARD:-0}
DIRECT_PORT_START=${DIRECT_PORT_START:-20000}
HOST_DOCKER_BIN=${HOST_DOCKER_BIN:-}

if [[ -z "$HOST_DOCKER_BIN" || ! -e "$HOST_DOCKER_BIN" ]]; then
  HOST_DOCKER_BIN="$(command -v docker || true)"
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

# Ensure dashboard app exists
if [[ ! -f "$APP_PATH" ]]; then
  if [[ -n "${REPO_DIR:-}" && -f "${REPO_DIR}/dashboard/app.py" ]]; then
    mkdir -p "$(dirname "$APP_PATH")"
    cp -f "${REPO_DIR}/dashboard/app.py" "$APP_PATH"
  else
    echo "dashboard app not found at $APP_PATH and REPO_DIR unknown" >&2
  fi
fi

# If dashboard_v2 sources exist under /opt/blobe-vm, deploy as a standalone container like a VM
if [[ -d "/opt/blobe-vm/dashboard_v2" ]]; then
  DASH_DIR="/opt/blobe-vm/dashboard_v2"
  DASHBOARD_V2_NAME="dashboard_v2"
  DASHBOARD_V2_PORT=${DASHBOARD_V2_PORT:-3000}
  # Find a free port if needed
  if port_in_use "$DASHBOARD_V2_PORT"; then
    new_port=$(find_free_port "$DIRECT_PORT_START" 1000 || true)
    if [[ -z "$new_port" ]]; then
      echo "Unable to find a free port for dashboard_v2" >&2
      exit 1
    fi
    DASHBOARD_V2_PORT="$new_port"
  fi
  # Remove any existing container
  if docker ps -a --format '{{.Names}}' | grep -qx "$DASHBOARD_V2_NAME"; then
    docker rm -f "$DASHBOARD_V2_NAME" >/dev/null 2>&1 || true
  fi
  # Build the dashboard_v2 image
  echo "Building dashboard_v2 image..."
  (cd "$DASH_DIR" && docker build -t dashboard_v2:latest .) || {
    echo "dashboard_v2 Docker build failed" >&2
    exit 1
  }
  # Run the dashboard_v2 container
  echo "Running dashboard_v2 container on port $DASHBOARD_V2_PORT..."
  docker run -d --name "$DASHBOARD_V2_NAME" --restart unless-stopped \
    -p "${DASHBOARD_V2_PORT}:3000" \
    -v "$STATE_DIR:/opt/blobe-vm" \
    -e NODE_ENV=production \
    dashboard_v2:latest \
    >/dev/null
  # Wait for the container to be running
  echo "Waiting for dashboard_v2 container to be running..."
  for i in {1..15}; do
    cid=$(docker ps -q -f name="^${DASHBOARD_V2_NAME}$")
    if [[ -n "$cid" ]]; then
      is_running=$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)
      if [[ "$is_running" == "true" ]]; then
        echo "dashboard_v2 container is running (ID: $cid)."
        break
      fi
    fi
    echo "dashboard_v2 not running yet, retry $i/15..."
    sleep 2
    if [[ $i -eq 15 ]]; then
      echo "dashboard_v2 container did not start in time." >&2
      exit 1
    fi
  done
  echo "Dashboard V2: http://$(hostname -I | awk '{print $1}'):${DASHBOARD_V2_PORT}/Dashboard"
else
  echo "No dashboard_v2 sources found at /opt/blobe-vm/dashboard_v2; skipping dashboard_v2 deployment"
fi

# Determine or assign port
DASHBOARD_PORT=${DASHBOARD_PORT:-}
# If no port set or the current one is busy, (re)assign a free port
if [[ -z "$DASHBOARD_PORT" ]] || port_in_use "$DASHBOARD_PORT"; then
  new_port=$(find_free_port "$DIRECT_PORT_START" 1000 || true)
  if [[ -z "$new_port" ]]; then
    echo "Unable to find a free port for dashboard" >&2
    exit 1
  fi
  DASHBOARD_PORT="$new_port"
  # Persist into .env
  if [[ -f "$ENV_FILE" ]]; then
    if grep -q '^DASHBOARD_PORT=' "$ENV_FILE"; then
      sed -i -E "s|^DASHBOARD_PORT=.*|DASHBOARD_PORT='$DASHBOARD_PORT'|" "$ENV_FILE"
    else
      printf "\nDASHBOARD_PORT='%s'\n" "$DASHBOARD_PORT" >> "$ENV_FILE"
    fi
  fi
fi

# Recreate container to ensure correct port mapping
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
fi

docker run -d --name "$NAME" --restart unless-stopped \
  -p "${DASHBOARD_PORT}:5000" \
  -v "$STATE_DIR:/opt/blobe-vm" \
  -v /var/blobe:/var/blobe \
  -v /usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro \
  -v "${HOST_DOCKER_BIN}:/usr/bin/docker:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$STATE_DIR/dashboard:/app:ro" \
  -e BLOBEDASH_USER="${BLOBEDASH_USER:-}" \
  -e BLOBEDASH_PASS="${BLOBEDASH_PASS:-}" \
  -e HOST_DOCKER_BIN="${HOST_DOCKER_BIN}" \
  python:3.11-slim \
    bash -c "apt-get update && apt-get install -y curl jq && pip install --no-cache-dir flask && python /app/app.py" \
  >/dev/null

echo "Dashboard: http://$(hostname -I | awk '{print $1}'):${DASHBOARD_PORT}/dashboard"
