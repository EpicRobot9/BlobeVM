#!/usr/bin/env bash
set -euo pipefail

# optimizer-ensure.sh
# Idempotent ensure script for BlobeVM Optimizer service.
# - Ensures Node.js is installed (via NodeSource for a modern LTS)
# - Ensures optimizer files are present under /opt/blobe-vm
# - Runs `npm install` if a package.json exists
# - Ensures log dir exists
# - Execs the optimizer JS in daemon mode so systemd manages it

REPO_DIR=${REPO_DIR:-}
STATE_DIR=${STATE_DIR:-/opt/blobe-vm}
OPT_DIR="$STATE_DIR/optimizer"
ENV_FILE="$STATE_DIR/.env"

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r k v; do
      [[ -z "$k" || "$k" =~ ^# ]] && continue
      v="${v%\'}"; v="${v#\'}"; v="${v%\"}"; v="${v#\"}"
      export "$k"="$v"
    done < "$ENV_FILE"
  fi
}

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    return 0
  fi
  echo "Node.js not found — installing Node.js 20.x via NodeSource"
  # Install prerequisites
  apt-get update -y
  apt-get install -y curl ca-certificates gnupg lsb-release
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
}

ensure_files() {
  mkdir -p "$STATE_DIR"
  if [[ ! -d "$OPT_DIR" || ! -f "$OPT_DIR/OptimizerService.js" ]]; then
    if [[ -n "$REPO_DIR" && -f "$REPO_DIR/optimizer/OptimizerService.js" ]]; then
      mkdir -p "$OPT_DIR"
      cp -a "$REPO_DIR/optimizer/"* "$OPT_DIR/"
    else
      echo "Optimizer files not present in $OPT_DIR and REPO_DIR not provided or missing." >&2
    fi
  fi
  chmod -R 755 "$OPT_DIR" || true
}

ensure_npm_deps() {
  if [[ -f "$OPT_DIR/package.json" ]]; then
    (cd "$OPT_DIR" && npm ci --no-audit --no-fund) || (cd "$OPT_DIR" && npm install --no-audit --no-fund)
  fi
}

ensure_logs() {
  mkdir -p /var/blobe/logs/optimizer
  chown root:root /var/blobe/logs/optimizer || true
  chmod 755 /var/blobe/logs/optimizer || true
}

main() {
  load_env
  ensure_node
  ensure_files
  ensure_npm_deps
  ensure_logs

  # Exec the optimizer under systemd control
  exec /usr/bin/env node "$OPT_DIR/OptimizerService.js" daemon
}

main "$@"
