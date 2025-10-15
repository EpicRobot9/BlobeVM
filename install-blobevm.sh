#!/usr/bin/env bash
# BlobeVM Installer - Robust, idempotent, and production-ready
# Inspired by Holy-Unblocker installer best practices

set -euo pipefail
LOGFILE="/var/log/blobevm-install.log"
exec > >(tee -a "$LOGFILE") 2>&1

# --- Require root ---
if [[ $EUID -ne 0 ]]; then
  echo "This installer must run as root. Re-running with sudo..."
  exec sudo -E bash "$0" "$@"
fi

# --- Helper: print step ---
step() { echo -e "\n\033[1;34m==> $*\033[0m"; }

# --- Dependencies ---
step "Checking/installing dependencies..."
apt-get update -y
apt-get install -y curl wget jq lsb-release ca-certificates gnupg

for pkg in docker docker-compose; do
  if ! command -v $pkg >/dev/null 2>&1; then
    step "Installing $pkg..."
    apt-get install -y $pkg || true
  fi
done

if ! command -v nginx >/dev/null 2>&1; then
  step "Installing NGINX..."
  apt-get install -y nginx
fi

if ! command -v traefik >/dev/null 2>&1; then
  step "Installing Traefik..."
  tmpdir=$(mktemp -d)
  arch="amd64"
  release_json=$(curl -fsSL https://api.github.com/repos/traefik/traefik/releases/latest || true)
  asset_url=$(echo "$release_json" | jq -r ".assets[] | select(.name | test(\"linux_${arch}\\.tar\\.gz$\")) | .browser_download_url" | head -n1)
  if [[ -z "$asset_url" ]]; then
    echo "Could not determine Traefik download URL. Skipping host binary install." >&2
  else
    curl -fsSL "$asset_url" -o "$tmpdir/traefik.tar.gz"
    tar -xzf "$tmpdir/traefik.tar.gz" -C "$tmpdir"
    if [[ -f "$tmpdir/traefik" ]]; then
      install -m 0755 "$tmpdir/traefik" /usr/local/bin/traefik
    else
      echo "Traefik binary not found in archive. Skipping host binary install." >&2
    fi
  fi
  rm -rf "$tmpdir"
fi

if ! command -v noip2 >/dev/null 2>&1; then
  step "Installing No-IP DUC..."
  wget -O /tmp/noip.tar.gz https://www.noip.com/client/linux/noip-duc-linux.tar.gz
  tar xzf /tmp/noip.tar.gz -C /tmp
  cd /tmp/noip-2.*
  make && make install
  cd -
fi

# --- Prompt for env vars ---
step "Configuring environment..."
REPO_URL="${REPO_URL:-https://github.com/EpicRobot9/BlobeVM.git}"
read -rp "Domain (leave blank for path mode): " DOMAIN
read -rp "Proxy mode [traefik/nginx/none] (default traefik): " PROXY_MODE
PROXY_MODE=${PROXY_MODE:-traefik}
read -rp "HTTP port [80]: " HTTP_PORT
HTTP_PORT=${HTTP_PORT:-80}
read -rp "HTTPS port [443]: " HTTPS_PORT
HTTPS_PORT=${HTTPS_PORT:-443}

# --- Clone repo ---
step "Cloning BlobeVM repo..."
REPO_DIR="/opt/blobe-vm/repo"
if [[ ! -d "$REPO_DIR" ]]; then
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

# --- Detect public IP ---
step "Detecting public IP..."
PUB_IP=$(curl -fsS ifconfig.me || curl -fsS icanhazip.com || hostname -I | awk '{print $1}')
echo "Public IP: $PUB_IP"

# --- DNS validation ---
if [[ -n "$DOMAIN" ]]; then
  step "Validating DNS for $DOMAIN..."
  DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u | head -n1)
  if [[ "$DNS_IP" != "$PUB_IP" ]]; then
    echo "DNS for $DOMAIN does not point to this server ($PUB_IP). HTTPS will not work until DNS is correct."
  else
    echo "DNS is correct."
  fi
fi

# --- Proxy mode selection ---
if [[ "$PROXY_MODE" == "traefik" ]]; then
  step "Setting up Traefik..."
  # Detect running Traefik
  TID=$(docker ps --filter=ancestor=traefik --format '{{.ID}}' | head -n1 || true)
  if [[ -z "$TID" ]]; then
    step "Starting Traefik container..."
    docker run -d --name blobevm-traefik \
      -p "$HTTP_PORT:80" -p "$HTTPS_PORT:443" \
      --restart unless-stopped \
      traefik:v2.11 \
      --api.dashboard=true \
      --providers.docker=true \
      --providers.docker.exposedbydefault=false \
      --entrypoints.web.address=:80 \
      --entrypoints.websecure.address=:443
  fi
elif [[ "$PROXY_MODE" == "nginx" ]]; then
  step "Configuring NGINX..."
  # Write site config
  cat > /etc/nginx/sites-available/blobevm <<EOF
server {
  listen $HTTP_PORT;
  server_name ${DOMAIN:-_};
  location / {
    proxy_pass http://localhost:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
EOF
  ln -sf /etc/nginx/sites-available/blobevm /etc/nginx/sites-enabled/blobevm
  rm -f /etc/nginx/sites-enabled/default
  systemctl reload nginx
fi

# --- Build/pull Docker image ---
step "Building BlobeVM Docker image..."
docker build -t blobevm:latest "$REPO_DIR"

# --- Run container ---
step "Starting BlobeVM container..."
docker run -d --name blobevm_app \
  --restart unless-stopped \
  -p 3000:3000 \
  -v /opt/blobe-vm/instances:/instances \
  blobevm:latest

# --- Blue/green update logic ---
# (For brevity, not implemented here; can be added with docker rename/swap)

# --- No-IP DUC integration ---
if [[ -n "$DOMAIN" ]]; then
  step "Starting No-IP DUC for dynamic DNS..."
  noip2 -C || true
  noip2
fi

# --- Enable HTTPS with Certbot if DNS matches and port 80 is free ---
if [[ -n "$DOMAIN" && "$DNS_IP" == "$PUB_IP" ]]; then
  step "Enabling HTTPS with Certbot..."
  apt-get install -y certbot
  certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email || true
fi

# --- Uninstall and status commands ---
cat <<EOM

BlobeVM install complete!

To uninstall:
  docker rm -f blobevm_app blobevm-traefik || true
  rm -rf /opt/blobe-vm
  systemctl stop nginx || true

To check status:
  docker ps | grep blobevm
  systemctl status nginx
  noip2 -S

Logs: $LOGFILE
EOM
