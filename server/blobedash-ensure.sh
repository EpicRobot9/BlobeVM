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

# Ensure dashboard image exists (build from repo if possible)
if ! docker image inspect blobevm-dashboard:latest >/dev/null 2>&1; then
  echo "Building blobevm-dashboard:latest image..."
  if [[ -n "${REPO_DIR:-}" && -f "${REPO_DIR}/dashboard/Dockerfile" ]]; then
    docker build -t blobevm-dashboard:latest "${REPO_DIR}/dashboard" || true
  elif [[ -f "/opt/blobe-vm/dashboard/Dockerfile" ]]; then
    docker build -t blobevm-dashboard:latest "/opt/blobe-vm/dashboard" || true
  else
    # Last resort: build minimal image from this script's location
    tmpdir=$(mktemp -d)
    cp -f "$APP_PATH" "$tmpdir/app.py" || true
    cat > "$tmpdir/Dockerfile" <<'DOCK'
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libjpeg-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask Pillow
WORKDIR /app
COPY app.py /app/app.py
CMD ["python", "/app/app.py"]
DOCK
    docker build -t blobevm-dashboard:latest "$tmpdir" || true
    rm -rf "$tmpdir"
  fi
fi

docker run -d --name "$NAME" --restart unless-stopped \
  -p "${DASHBOARD_PORT}:5000" \
  -v "$STATE_DIR:/opt/blobe-vm" \
  -v /usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro \
  -v "${HOST_DOCKER_BIN}:/usr/bin/docker:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$APP_PATH:/app/app.py:ro" \
  -e BLOBEDASH_USER="${BLOBEDASH_USER:-}" \
  -e BLOBEDASH_PASS="${BLOBEDASH_PASS:-}" \
  -e HOST_DOCKER_BIN="${HOST_DOCKER_BIN}" \
  blobevm-dashboard:latest \
  >/dev/null

echo "Dashboard: http://$(hostname -I | awk '{print $1}'):${DASHBOARD_PORT}/dashboard"
