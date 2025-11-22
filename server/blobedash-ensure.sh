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

# If dashboard_v2 sources exist under /opt/blobe-vm, attempt to build them so
# the v2 UI is available at /Dashboard. Capture stderr to last_error.txt
# so the original dashboard can display diagnostics.
if [[ -d "/opt/blobe-vm/dashboard_v2" ]]; then
  DASH_DIR="/opt/blobe-vm/dashboard_v2"
  if [[ -f "$DASH_DIR/docker-compose.yml" ]]; then
    echo "Starting dashboard_v2 using Docker Compose (dir: $DASH_DIR)"
    (cd "$DASH_DIR" && docker compose up -d --build) || {
      echo "dashboard_v2 Docker Compose failed" >&2
      exit 1
    }
    # Wait for dashboard_v2 container to be running (robust check)
    echo "Waiting for dashboard_v2 container to be running..."
    for i in {1..15}; do
      cid=$(docker compose -f "$DASH_DIR/docker-compose.yml" ps -q dashboard_v2)
      if [[ -n "$cid" ]]; then
        is_running=$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)
        if [[ "$is_running" == "true" ]]; then
          echo "dashboard_v2 container is running (ID: $cid). Proceeding to copy dist folder."
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
    # Remove existing dist directory to avoid dist/dist nesting
    echo "Removing any existing $DASH_DIR/dist..."
    rm -rf "$DASH_DIR/dist"
    mkdir -p "$DASH_DIR/dist"
    # Copy contents of /app/dist (not the folder itself) from container to host
    echo "Copying dashboard_v2 dist contents from container to host..."
    if docker cp "$cid:/app/dist/." "$DASH_DIR/dist"; then
      echo "Successfully copied dist contents from container."
    else
      echo "Failed to copy dist contents from dashboard_v2 container." >&2
      exit 1
    fi
    echo "Listing contents of $DASH_DIR/dist after copy:"
    ls -l "$DASH_DIR/dist"
    echo "Listing contents of dist subfolders (if any):"
    find "$DASH_DIR/dist" -type f
  else
    echo "No docker-compose.yml in /opt/blobe-vm/dashboard_v2; skipping dashboard_v2 deployment"
  fi
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
