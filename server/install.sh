#!/usr/bin/env bash

set -euo pipefail

# BlobeVM Host/VPS Installer
# - Installs Docker and Traefik
# - Sets up a shared docker network "proxy"
# - Deploys Traefik (HTTP only by default, optional HTTPS via ACME)
# - Builds the BlobeVM image from this repository
# - Installs the blobe-vm-manager CLI
# - Optionally creates a first VM instance and prints its URL


require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "This installer must run as root. Re-running with sudo..." >&2
    exec sudo -E bash "$0" "$@"
  fi
}

detect_repo_root() {
  # Default to the directory two levels up from this script (repo root)
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
  REPO_DIR="$(cd "$script_dir/.." && pwd)"
}

prompt_config() {
  echo "--- BlobeVM Host Configuration ---"
  read -rp "Primary domain for VMs (e.g., example.com) [leave empty to use URL paths]: " BLOBEVM_DOMAIN || true
  read -rp "Email for Let's Encrypt (optional; required for HTTPS): " BLOBEVM_EMAIL || true
  read -rp "Enable KVM passthrough to containers if available? [y/N]: " enable_kvm || true
  if [[ -n "${BLOBEVM_EMAIL}" ]]; then
    read -rp "Force HTTP->HTTPS redirect on all routers? [Y/n]: " force_https || true
  fi
  read -rp "Protect Traefik dashboard with basic auth? [y/N]: " dash_auth || true
  read -rp "Enable HSTS headers on HTTPS routers? (adds preload, subdomains) [y/N]: " hsts_ans || true
  # Web dashboard now deployed by default (can disable by setting DISABLE_DASHBOARD=1 env before running installer)
  if [[ "${dash_auth,,}" =~ ^y(es)?$ ]]; then
    read -rp "Dashboard username [admin]: " dash_user || true
    dash_user=${dash_user:-admin}
    read -rsp "Dashboard password: " dash_pass; echo
    if ! command -v htpasswd >/dev/null 2>&1; then
      apt-get update -y >/dev/null 2>&1 || true
      apt-get install -y apache2-utils >/dev/null 2>&1 || true
    fi
    dash_hash=$(htpasswd -nbB "$dash_user" "$dash_pass" 2>/dev/null | sed 's/^.*://')
    [[ -z "$dash_hash" ]] && dash_hash=$(htpasswd -nb "$dash_user" "$dash_pass" 2>/dev/null | sed 's/^.*://')
    TRAEFIK_DASHBOARD_AUTH="$dash_user:$dash_hash"
  else
    TRAEFIK_DASHBOARD_AUTH=""
  fi
  ENABLE_KVM=0
  [[ "${enable_kvm,,}" == "y" || "${enable_kvm,,}" == "yes" ]] && ENABLE_KVM=1
  FORCE_HTTPS=0
  if [[ -n "${BLOBEVM_EMAIL}" ]]; then
    [[ "${force_https,,}" != "n" ]] && FORCE_HTTPS=1
  fi
  HSTS_ENABLED=0
  [[ "${hsts_ans,,}" =~ ^y(es)?$ ]] && HSTS_ENABLED=1
  if [[ "${DISABLE_DASHBOARD:-0}" -eq 1 ]]; then
    ENABLE_DASHBOARD=0
  else
    ENABLE_DASHBOARD=1
  fi

  echo
  echo "Summary:"
  echo "  Domain:    ${BLOBEVM_DOMAIN:-<none - path-based URLs>}"
  echo "  ACME email:${BLOBEVM_EMAIL:-<none - HTTP only>}"
  echo "  KVM:       $([[ "$ENABLE_KVM" -eq 1 ]] && echo enabled || echo disabled)"
  echo "  Force HTTPS: $([[ "$FORCE_HTTPS" -eq 1 ]] && echo yes || echo no)"
  echo "  HSTS:        $([[ "$HSTS_ENABLED" -eq 1 ]] && echo yes || echo no)"
  echo "  Web Dashboard: $([[ "$ENABLE_DASHBOARD" -eq 1 ]] && echo yes || echo no) (set DISABLE_DASHBOARD=1 to skip)"
  if [[ -n "$TRAEFIK_DASHBOARD_AUTH" ]]; then
    echo "  Dashboard Auth: enabled (user: ${dash_user:-admin})"
  else
    echo "  Dashboard Auth: disabled"
  fi
  echo
}

install_prereqs() {
  echo "Installing Docker engine and compose plugin..."
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg lsb-release jq
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  systemctl enable --now docker
}

ensure_network() {
  # Create a shared docker network for Traefik routing
  if ! docker network inspect proxy >/dev/null 2>&1; then
    docker network create proxy
  fi
}

setup_traefik() {
  mkdir -p /opt/blobe-vm/traefik/letsencrypt
  chmod 700 /opt/blobe-vm/traefik/letsencrypt

  local compose_file=/opt/blobe-vm/traefik/docker-compose.yml
  echo "Writing Traefik docker-compose.yml to $compose_file"

  if [[ -n "${BLOBEVM_EMAIL:-}" ]]; then
    cat > "$compose_file" <<YAML
services:
  traefik:
    image: traefik:v2.11
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --accesslog=true
      - --api.dashboard=true
      - --certificatesresolvers.myresolver.acme.email=${BLOBEVM_EMAIL}
      - --certificatesresolvers.myresolver.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.myresolver.acme.httpchallenge=true
      - --certificatesresolvers.myresolver.acme.httpchallenge.entrypoint=web
YAML
    if [[ "$FORCE_HTTPS" -eq 1 ]]; then
      cat >> "$compose_file" <<'YAML'
      - --entrypoints.web.http.redirections.entryPoint.to=websecure
      - --entrypoints.web.http.redirections.entryPoint.scheme=https
YAML
    fi
    cat >> "$compose_file" <<'YAML'
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    networks:
      - proxy
    labels:
      - traefik.enable=true
      # Router rules for the dashboard are only active when a domain is set
      # You can later attach a host rule via labels or set a CNAME like traefik.<domain>
      - traefik.http.services.traefik.loadbalancer.server.port=8080
      - traefik.http.routers.traefik.rule=PathPrefix(`/traefik`)
      - traefik.http.routers.traefik.entrypoints=web
YAML
    if [[ -n "$TRAEFIK_DASHBOARD_AUTH" ]]; then
      # Add auth middleware labels
      cat >> "$compose_file" <<YAML
      - traefik.http.middlewares.traefik-auth.basicauth.users=${TRAEFIK_DASHBOARD_AUTH}
      - traefik.http.routers.traefik.middlewares=traefik-auth
YAML
    fi
    cat >> "$compose_file" <<'YAML'
networks:
  proxy:
    external: true
YAML
  else
    cat > "$compose_file" <<YAML
services:
  traefik:
    image: traefik:v2.11
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --accesslog=true
      - --api.dashboard=true
    ports:
      - "80:80"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - proxy
networks:
  proxy:
    external: true
YAML
  fi

  echo "Starting Traefik..."
  (cd /opt/blobe-vm/traefik && docker compose up -d)

  if [[ "$ENABLE_DASHBOARD" -eq 1 ]]; then
    echo "Deploying BlobeVM web dashboard service..."
    # Append dashboard service to compose file
    cat >> /opt/blobe-vm/traefik/docker-compose.yml <<'DASH'
  dashboard:
    image: ghcr.io/library/python:3.11-slim
    command: bash -c "pip install --no-cache-dir flask && python /app/app.py"
    volumes:
      - /opt/blobe-vm:/opt/blobe-vm
      - /usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro
      - /var/run/docker.sock:/var/run/docker.sock
      - /opt/blobe-vm/dashboard/app.py:/app/app.py:ro
    environment:
      - BLOBEDASH_USER=${BLOBEDASH_USER:-}
      - BLOBEDASH_PASS=${BLOBEDASH_PASS:-}
    networks:
      - proxy
    labels:
      - traefik.enable=true
      - traefik.http.routers.blobe-dashboard.rule=PathPrefix(`/dashboard`)
      - traefik.http.routers.blobe-dashboard.entrypoints=web
      - traefik.http.services.blobe-dashboard.loadbalancer.server.port=5000
DASH
    if [[ -n "$BLOBEVM_DOMAIN" ]]; then
      cat >> /opt/blobe-vm/traefik/docker-compose.yml <<DASH
      - traefik.http.routers.blobe-dashboard-host.rule=Host(\`dashboard.${BLOBEVM_DOMAIN}\`)
      - traefik.http.routers.blobe-dashboard-host.entrypoints=web
      - traefik.http.services.blobe-dashboard-host.loadbalancer.server.port=5000
DASH
    fi
    (cd /opt/blobe-vm/traefik && docker compose up -d dashboard)
  fi
}

build_image() {
  echo "Building the BlobeVM image from $REPO_DIR ..."
  docker build -t blobevm:latest "$REPO_DIR"
}

install_manager() {
  echo "Installing blobe-vm-manager CLI..."
  install -Dm755 "$REPO_DIR/server/blobe-vm-manager" /usr/local/bin/blobe-vm-manager
  mkdir -p /opt/blobe-vm/instances
  cat > /opt/blobe-vm/.env <<EOF
BLOBEVM_DOMAIN=${BLOBEVM_DOMAIN:-}
BLOBEVM_EMAIL=${BLOBEVM_EMAIL:-}
ENABLE_TLS=$([[ -n "${BLOBEVM_EMAIL:-}" ]] && echo 1 || echo 0)
ENABLE_KVM=${ENABLE_KVM}
REPO_DIR=${REPO_DIR}
  BASE_PATH=/vm
  FORCE_HTTPS=${FORCE_HTTPS}
  TRAEFIK_DASHBOARD_AUTH=${TRAEFIK_DASHBOARD_AUTH}
HSTS_ENABLED=${HSTS_ENABLED}
ENABLE_DASHBOARD=${ENABLE_DASHBOARD}
EOF
}

maybe_create_first_vm() {
  echo
  read -rp "Create an initial VM instance now? [y/N]: " create_now || true
  if [[ "${create_now,,}" =~ ^y(es)?$ ]]; then
    local name
    read -rp "Instance name (subdomain or path name): " name
    if [[ -z "$name" ]]; then
      echo "No name provided, skipping initial VM creation."
      return 0
    fi
    blobe-vm-manager create "$name"
  fi
}

print_success() {
  echo
  echo "BlobeVM host setup complete."
  if [[ -n "${BLOBEVM_DOMAIN:-}" ]]; then
    echo "- Make sure your DNS points either wildcard *.${BLOBEVM_DOMAIN} and traefik.${BLOBEVM_DOMAIN} to this server's IP."
    echo "- Access Traefik dashboard at: https://traefik.${BLOBEVM_DOMAIN} (if HTTPS enabled) or http://traefik.${BLOBEVM_DOMAIN} (HTTP only)."
    echo "- Create VMs: blobe-vm-manager create myvm  -> URL: http(s)://myvm.${BLOBEVM_DOMAIN}/"
  else
    echo "- No domain configured. VMs will be available at path prefixes, e.g.: http://<SERVER_IP>/vm/<name>/"
  fi
  echo "- Manage VMs with: blobe-vm-manager [create|start|stop|delete|list] <name>"
}

main() {
  require_root "$@"
  detect_repo_root
  prompt_config
  install_prereqs
  ensure_network
  setup_traefik
  build_image
  install_manager
  maybe_create_first_vm
  print_success
}

main "$@"
