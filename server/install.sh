#!/usr/bin/env bash

set -euo pipefail
set -o errtrace

# BlobeVM Host/VPS Installer
# - Installs Docker and Traefik (default) OR runs in direct mode without a proxy
# - Sets up a shared docker network "proxy" (Traefik mode only)
# - Deploys Traefik (HTTP only by default, optional HTTPS via ACME) unless disabled
# - Builds the BlobeVM image from this repository
# - Installs the blobe-vm-manager CLI
# - Optionally creates a first VM instance and prints its URL
#
# Environment overrides (optional, useful for automation):
#   BLOBEVM_DOMAIN, BLOBEVM_EMAIL, BLOBEVM_HTTP_PORT, BLOBEVM_HTTPS_PORT
#   BLOBEVM_FORCE_HTTPS, BLOBEVM_ENABLE_DASHBOARD, BLOBEVM_ENABLE_KVM
#   BLOBEVM_HSTS, BLOBEVM_TRAEFIK_NETWORK, BLOBEVM_REUSE_SETTINGS
#   BLOBEVM_AUTO_CREATE_VM, BLOBEVM_INITIAL_VM_NAME, BLOBEVM_ENABLE_TLS
#   BLOBEVM_ASSUME_DEFAULTS (accept safe defaults during prompts)
#   DISABLE_DASHBOARD (legacy flag to skip dashboard deployment)
#   BLOBEVM_NO_TRAEFIK (1 to run without Traefik; VMs get unique high ports)
#   BLOBEVM_DIRECT_PORT_START (first port to try in direct/no-Traefik mode; default 20000)

trap 'echo "[ERROR] ${BASH_SOURCE[0]}: line ${LINENO} failed: ${BASH_COMMAND}" >&2' ERR


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

# Load existing settings if present (update-safe)
load_existing_env() {
  local env_file=/opt/blobe-vm/.env
  [[ -f "$env_file" ]] || return 0
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^# ]] && continue
    v="${v%\'}"; v="${v#\'}"; v="${v%\"}"; v="${v#\"}"
    export "$k"="$v"
  done < "$env_file"
}

normalize_bool() {
  local value="${1:-}"
  case "${value,,}" in
    1|true|yes|y|on|enable|enabled) echo 1 ;;
    0|false|no|n|off|disable|disabled) echo 0 ;;
    *) echo "${value}" ;;
  esac
}

apply_env_overrides() {
  # Default: disable Traefik unless explicitly set
  if [[ -z "${BLOBEVM_NO_TRAEFIK:-}" ]]; then
    NO_TRAEFIK=1
  fi
  BLOBEVM_DOMAIN="${BLOBEVM_DOMAIN:-${BLOBEVM_INSTALL_DOMAIN:-}}"
  BLOBEVM_EMAIL="${BLOBEVM_EMAIL:-${BLOBEVM_INSTALL_EMAIL:-}}"

  if [[ -n "${BLOBEVM_FORCE_HTTPS:-}" ]]; then
    local forced
    forced="$(normalize_bool "${BLOBEVM_FORCE_HTTPS}")"
    [[ "${forced}" == "1" ]] && FORCE_HTTPS=1
    [[ "${forced}" == "0" ]] && FORCE_HTTPS=0
  fi

  if [[ -n "${BLOBEVM_ENABLE_KVM:-}" ]]; then
    local kvm
    kvm="$(normalize_bool "${BLOBEVM_ENABLE_KVM}")"
    [[ "${kvm}" == "1" ]] && ENABLE_KVM=1
    [[ "${kvm}" == "0" ]] && ENABLE_KVM=0
  fi

  if [[ -n "${BLOBEVM_ENABLE_DASHBOARD:-}" ]]; then
    local dash
    dash="$(normalize_bool "${BLOBEVM_ENABLE_DASHBOARD}")"
    if [[ "${dash}" == "1" ]]; then
      ENABLE_DASHBOARD=1
      unset DISABLE_DASHBOARD
    elif [[ "${dash}" == "0" ]]; then
      ENABLE_DASHBOARD=0
      DISABLE_DASHBOARD=1
    fi
  fi

  if [[ -n "${DISABLE_DASHBOARD:-}" ]]; then
    local disable
    disable="$(normalize_bool "${DISABLE_DASHBOARD}")"
    [[ "${disable}" == "1" ]] && ENABLE_DASHBOARD=0
  fi

  if [[ -n "${BLOBEVM_HSTS:-}" ]]; then
    local hsts
    hsts="$(normalize_bool "${BLOBEVM_HSTS}")"
    [[ "${hsts}" == "1" ]] && HSTS_ENABLED=1
    [[ "${hsts}" == "0" ]] && HSTS_ENABLED=0
  fi

  if [[ -n "${BLOBEVM_ENABLE_TLS:-}" ]]; then
    local tls
    tls="$(normalize_bool "${BLOBEVM_ENABLE_TLS}")"
    [[ "${tls}" == "1" ]] && TLS_ENABLED=1
    [[ "${tls}" == "0" ]] && TLS_ENABLED=0
  fi

  if [[ -n "${BLOBEVM_HTTP_PORT:-}" ]]; then
    HTTP_PORT="${BLOBEVM_HTTP_PORT}"
  fi
  if [[ -n "${BLOBEVM_HTTPS_PORT:-}" ]]; then
    HTTPS_PORT="${BLOBEVM_HTTPS_PORT}"
  fi

  if [[ -n "${BLOBEVM_TRAEFIK_NETWORK:-}" ]]; then
    TRAEFIK_NETWORK="${BLOBEVM_TRAEFIK_NETWORK}"
  fi

  if [[ -n "${BLOBEVM_BASE_PATH:-}" ]]; then
    BASE_PATH="${BLOBEVM_BASE_PATH}"
  fi

  # Opt-in: No Traefik mode (direct port publishing)
  if [[ -n "${BLOBEVM_NO_TRAEFIK:-}" ]]; then
    local nt
    nt="$(normalize_bool "${BLOBEVM_NO_TRAEFIK}")"
    [[ "${nt}" == "1" ]] && NO_TRAEFIK=1 || NO_TRAEFIK=0
  fi

  if [[ -n "${BLOBEVM_SKIP_TRAEFIK:-}" ]]; then
    local skip
    skip="$(normalize_bool "${BLOBEVM_SKIP_TRAEFIK}")"
    [[ "${skip}" == "1" ]] && SKIP_TRAEFIK=1
  fi

  if [[ -n "${BLOBEVM_REUSE_SETTINGS:-}" ]]; then
    BLOBEVM_REUSE_SETTINGS="$(normalize_bool "${BLOBEVM_REUSE_SETTINGS}")"
  fi

  if [[ -n "${BLOBEDASH_USER:-}" ]]; then
    DASH_AUTH_USER="${BLOBEDASH_USER}"
  fi

  if [[ -n "${BLOBEVM_ASSUME_DEFAULTS:-}" ]]; then
    ASSUME_DEFAULTS="$(normalize_bool "${BLOBEVM_ASSUME_DEFAULTS}")"
  fi
}

prompt_config() {
  echo "--- BlobeVM Host Configuration ---"

  if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
    echo "Mode: Direct (no Traefik). Each VM will be published on a unique high port."
  fi

  if [[ -n "${BLOBEVM_DOMAIN:-}" ]]; then
    echo "Domain supplied via environment: ${BLOBEVM_DOMAIN}"
  else
    read -rp "Primary domain for VMs (e.g., example.com) [leave empty to use URL paths]: " BLOBEVM_DOMAIN || true
    BLOBEVM_DOMAIN="${BLOBEVM_DOMAIN//[[:space:]]/}"
  fi

  if [[ -n "${BLOBEVM_EMAIL:-}" && "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    echo "Using Let's Encrypt email from environment."
  else
    if [[ "${NO_TRAEFIK:-0}" -ne 1 ]]; then
      read -rp "Email for Let's Encrypt (optional; required for HTTPS): " BLOBEVM_EMAIL || true
    fi
    BLOBEVM_EMAIL="${BLOBEVM_EMAIL//[[:space:]]/}"
  fi

  local enable_kvm_response=""
  if [[ -n "${ENABLE_KVM:-}" ]]; then
    ENABLE_KVM=$([[ "${ENABLE_KVM}" == "1" ]] && echo 1 || echo 0)
  elif [[ "${ASSUME_DEFAULTS:-0}" == "1" ]]; then
    ENABLE_KVM=0
  else
    read -rp "Enable KVM passthrough to containers if available? [y/N]: " enable_kvm_response || true
    ENABLE_KVM=0
    [[ "${enable_kvm_response,,}" == y* ]] && ENABLE_KVM=1
  fi

  if [[ -n "${BLOBEVM_EMAIL:-}" && "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    if [[ -n "${FORCE_HTTPS:-}" ]]; then
      FORCE_HTTPS=$([[ "${FORCE_HTTPS}" == "1" ]] && echo 1 || echo 0)
    elif [[ "${ASSUME_DEFAULTS:-0}" == "1" ]]; then
      FORCE_HTTPS=1
    else
      local force_https_response=""
      read -rp "Force HTTP->HTTPS redirect on all routers? [Y/n]: " force_https_response || true
      FORCE_HTTPS=1
      [[ "${force_https_response,,}" == n* ]] && FORCE_HTTPS=0
    fi
  else
    FORCE_HTTPS=0
  fi

  local dash_auth_response=""
  if [[ -n "${TRAEFIK_DASHBOARD_AUTH:-}" && "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    [[ -z "${DASH_AUTH_USER:-}" ]] && DASH_AUTH_USER="${TRAEFIK_DASHBOARD_AUTH%%:*}"
    echo "Dashboard basic auth supplied via environment."
  elif [[ "${ASSUME_DEFAULTS:-0}" == "1" ]]; then
    TRAEFIK_DASHBOARD_AUTH=""
  else
    if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
      TRAEFIK_DASHBOARD_AUTH=""
    else
      read -rp "Protect Traefik dashboard with basic auth? [y/N]: " dash_auth_response || true
      if [[ "${dash_auth_response,,}" =~ ^y(es)?$ ]]; then
        local dash_user dash_pass dash_hash
        dash_user="${DASH_AUTH_USER:-admin}"
        read -rp "Dashboard username [${dash_user}]: " dash_user_input || true
        [[ -n "${dash_user_input}" ]] && dash_user="${dash_user_input}"
        read -rsp "Dashboard password: " dash_pass; echo
        if ! command -v htpasswd >/dev/null 2>&1; then
          apt-get update -y >/dev/null 2>&1 || true
          apt-get install -y apache2-utils >/dev/null 2>&1 || true
        fi
        dash_hash=$(htpasswd -nbB "${dash_user}" "${dash_pass}" 2>/dev/null | sed 's/^.*://')
        [[ -z "${dash_hash}" ]] && dash_hash=$(htpasswd -nb "${dash_user}" "${dash_pass}" 2>/dev/null | sed 's/^.*://')
        TRAEFIK_DASHBOARD_AUTH="${dash_user}:${dash_hash}"
        DASH_AUTH_USER="${dash_user}"
      else
        TRAEFIK_DASHBOARD_AUTH=""
      fi
    fi
  fi

  if [[ -n "${BLOBEVM_HSTS:-}" && "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    HSTS_ENABLED=$([[ "${HSTS_ENABLED:-0}" == "1" ]] && echo 1 || echo 0)
  elif [[ "${ASSUME_DEFAULTS:-0}" == "1" ]]; then
    HSTS_ENABLED=0
  else
    if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
      HSTS_ENABLED=0
    else
      local hsts_ans
      read -rp "Enable HSTS headers on HTTPS routers? (adds preload, subdomains) [y/N]: " hsts_ans || true
      HSTS_ENABLED=0
      [[ "${hsts_ans,,}" =~ ^y(es)?$ ]] && HSTS_ENABLED=1
    fi
  fi

  if [[ "${DISABLE_DASHBOARD:-0}" -eq 1 ]]; then
    ENABLE_DASHBOARD=0
  elif [[ -n "${ENABLE_DASHBOARD:-}" ]]; then
    ENABLE_DASHBOARD=$([[ "${ENABLE_DASHBOARD}" == "1" ]] && echo 1 || echo 0)
  else
    # In direct mode, default to disabled (enable with BLOBEVM_ENABLE_DASHBOARD=1)
    if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
      ENABLE_DASHBOARD=0
    else
      ENABLE_DASHBOARD=1
    fi
  fi

  TRAEFIK_NETWORK="${TRAEFIK_NETWORK:-proxy}"

  echo
  echo "Summary:"
  echo "  Domain:    ${BLOBEVM_DOMAIN:-<none - path-based URLs>}"
  echo "  ACME email:${BLOBEVM_EMAIL:-<none - HTTP only>}"
  echo "  KVM:       $([[ "${ENABLE_KVM}" -eq 1 ]] && echo enabled || echo disabled)"
  if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
    echo "  Proxy:      disabled (direct mode)"
  else
    echo "  Force HTTPS: $([[ "${FORCE_HTTPS}" -eq 1 ]] && echo yes || echo no)"
    echo "  HSTS:        $([[ "${HSTS_ENABLED}" -eq 1 ]] && echo yes || echo no)"
  fi
  echo "  Web Dashboard: $([[ "${ENABLE_DASHBOARD}" -eq 1 ]] && echo yes || echo no) (set DISABLE_DASHBOARD=1 to skip)"
  if [[ -n "${TRAEFIK_DASHBOARD_AUTH}" ]]; then
    local summary_user="${DASH_AUTH_USER:-${TRAEFIK_DASHBOARD_AUTH%%:*}}"
    echo "  Dashboard Auth: enabled (user: ${summary_user:-admin})"
  else
    echo "  Dashboard Auth: disabled"
  fi
  echo
}

install_prereqs() {
  echo "Ensuring prerequisite packages are installed..."
  export DEBIAN_FRONTEND=noninteractive
  # Clean up duplicate apt source lines that cause warnings on some hosts
  if [[ -f /etc/apt/sources.list.d/ubuntu-mirrors.list ]]; then
    tmpf="$(mktemp)"
    awk '!seen[$0]++' /etc/apt/sources.list.d/ubuntu-mirrors.list > "$tmpf" || true
    mv "$tmpf" /etc/apt/sources.list.d/ubuntu-mirrors.list || true
  fi
  apt-get update -y
  apt-get install -y ca-certificates curl wget gnupg lsb-release jq >/dev/null

  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
    cat <<EOF >/etc/apt/sources.list.d/docker.list
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") $(. /etc/os-release && echo "$VERSION_CODENAME") stable
EOF
  fi

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null

  systemctl enable --now docker >/dev/null 2>&1 || systemctl start docker >/dev/null 2>&1

  # Quick sanity checks
  command -v docker >/dev/null 2>&1 || {
    echo "Docker did not install correctly." >&2
    exit 1
  }
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is unavailable. Please ensure docker-compose-plugin is installed." >&2
    exit 1
  fi
  local detected_docker
  detected_docker="$(command -v docker)"
  if [[ -z "${detected_docker}" ]]; then
    echo "Unable to determine docker CLI path." >&2
    exit 1
  fi
  if [[ -z "${HOST_DOCKER_BIN:-}" || ! -e "${HOST_DOCKER_BIN}" ]]; then
    HOST_DOCKER_BIN="${detected_docker}"
  fi
  if [[ ! -e "${HOST_DOCKER_BIN}" ]]; then
    echo "Docker CLI not found at ${HOST_DOCKER_BIN}." >&2
    exit 1
  fi
  export HOST_DOCKER_BIN
  command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
  command -v wget >/dev/null 2>&1 || { echo "wget is required." >&2; exit 1; }
}

ensure_network() {
  # Create a shared docker network for Traefik routing
  if [[ "${SKIP_TRAEFIK:-0}" -eq 1 || "${NO_TRAEFIK:-0}" -eq 1 ]]; then
    # We are reusing an external Traefik; assume its network exists
    return 0
  fi
  local net_name
  net_name="${TRAEFIK_NETWORK:-proxy}"
  if ! docker network inspect "${net_name}" >/dev/null 2>&1; then
    docker network create "${net_name}"
  fi
}

# If configured to skip Traefik because of a previous external instance, but it's gone now,
# clear SKIP_TRAEFIK so we deploy ours.
validate_skip_traefik() {
  [[ "${NO_TRAEFIK:-0}" -eq 1 ]] && return 0
  if [[ "${SKIP_TRAEFIK:-0}" -eq 1 ]]; then
    local net_name="${TRAEFIK_NETWORK:-proxy}"
    local has_tr=0 has_net=0
    if docker ps -a --format '{{.Names}}' | grep -Eq '^(traefik|traefik-traefik-1)$'; then has_tr=1; fi
    if docker network inspect "${net_name}" >/dev/null 2>&1; then has_net=1; fi
    if [[ "$has_tr" -ne 1 || "$has_net" -ne 1 ]]; then
      echo "Configured to reuse external Traefik, but it's not present (container/network missing)."
      echo "Re-enabling Traefik deployment."
      SKIP_TRAEFIK=0
    fi
  fi
}

# --- Port helpers and interactive resolution ---
# Return 0 if port is in use
port_in_use() {
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | awk '{print $4}' | grep -E "(^|:)${p}$" >/dev/null 2>&1
  else
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -E "(^|:)${p}$" >/dev/null 2>&1
  fi
}

# Print processes listening on a port (best-effort)
print_port_owners() {
  local p="$1"
  echo "Processes on port ${p}:"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp | awk -v P=":${p}$" '$4 ~ P {print $0}' | sed 's/^/  /'
  else
    netstat -ltnp 2>/dev/null | awk -v P=":${p} " '$4 ~ P {print $0}' | sed 's/^/  /'
  fi
}

# Extract PIDs bound to a port (best-effort)
port_pids() {
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp | awk -v P=":${p}$" '$4 ~ P {print $0}' |
      sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u
  else
    netstat -ltnp 2>/dev/null | awk -v P=":${p} " '$4 ~ P {print $7}' |
      sed -n 's|/.*||p' | sort -u
  fi
}

# Attempt to gracefully stop known services, else kill PIDs
free_port_by_killing() {
  local p="$1"
  local pids; pids=$(port_pids "$p")
  [[ -n "$pids" ]] || return 0
  echo "Attempting to free port ${p}..."
  local pid
  for pid in $pids; do
    local comm cmd svc
    comm=$(tr -d '\0' < "/proc/${pid}/comm" 2>/dev/null || true)
    cmd=$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)
    svc=""
    case "${comm:-$cmd}" in
      *nginx*) svc=nginx ;;
      *apache2*|*httpd*) svc=apache2 ;;
      *caddy*) svc=caddy ;;
      *traefik*) svc=traefik ;;
      *haproxy*) svc=haproxy ;;
      *dockerd*|*docker-proxy*) svc="" ;;
    esac
    if command -v systemctl >/dev/null 2>&1 && [[ -n "$svc" ]]; then
      if systemctl list-unit-files | grep -q "^${svc}\.service"; then
        echo "Stopping service ${svc} (pid ${pid})..."
        systemctl stop "$svc" || true
      fi
    fi
    if kill -0 "$pid" 2>/dev/null; then
      echo "Sending SIGTERM to pid ${pid}..."
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
      echo "Sending SIGKILL to pid ${pid}..."
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  # wait until freed (up to 10 tries)
  local i=0
  while (( i < 10 )); do
    if ! port_in_use "$p"; then return 0; fi
    sleep 0.5; i=$((i+1))
  done
  return 1
}

# Find first free TCP port from a starting point, up to N attempts
find_free_port() {
  local start="$1"; local attempts="${2:-200}"; local p="$start"; local i=0
  while (( i < attempts )); do
    if ! port_in_use "$p"; then echo "$p"; return 0; fi
    p=$((p+1)); i=$((i+1))
  done
  return 1
}

# When a desired port is busy, ask user to kill or switch
resolve_busy_port_interactive() {
  local label="$1" desired="$2" fallback_start="$3"
  echo "=== ${label} port ${desired} is currently in use ==="
  print_port_owners "$desired"
  echo
  echo "Options:"
  echo "  1) Kill the process(es) using port ${desired} and use ${desired}"
  echo "  2) Switch to another port automatically (start from ${fallback_start})"
  echo "  3) Enter a custom port"
  local choice
  while true; do
    read -rp "Choose [1/2/3]: " choice || true
    case "$choice" in
      1)
        if free_port_by_killing "$desired"; then
          echo "$desired"
          return 0
        else
          echo "Failed to free port ${desired}." >&2
        fi
        ;;
      2)
        local alt
        alt=$(find_free_port "$fallback_start" 200 || true)
        if [[ -n "$alt" ]]; then
          echo "$alt"
          return 0
        else
          echo "No free alternative port found starting at ${fallback_start}." >&2
        fi
        ;;
      3)
        local custom
        read -rp "Enter port number: " custom || true
        if [[ "$custom" =~ ^[0-9]+$ && "$custom" -ge 1 && "$custom" -le 65535 ]]; then
          if port_in_use "$custom"; then
            echo "Port ${custom} is also in use. Try again." >&2
          else
            echo "$custom"
            return 0
          fi
        else
          echo "Invalid port. Try again." >&2
        fi
        ;;
      *) echo "Please enter 1, 2, or 3." ;;
    esac
  done
}

detect_ports() {
  # Choose host ports for Traefik, prompting to kill or switch when needed
  [[ "${NO_TRAEFIK:-0}" -eq 1 ]] && return 0
  local desired_http="${HTTP_PORT:-${BLOBEVM_HTTP_PORT:-80}}"
  local desired_https="${HTTPS_PORT:-${BLOBEVM_HTTPS_PORT:-443}}"
  HTTP_PORT="${desired_http}"
  HTTPS_PORT="${desired_https}"

  if port_in_use "$HTTP_PORT"; then
    if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
      local fallback_http
      if [[ "$HTTP_PORT" -eq 80 ]]; then
        fallback_http=8080
      else
        fallback_http=$((HTTP_PORT + 1))
      fi
      HTTP_PORT=$(find_free_port "$fallback_http" 200 || true)
      if [[ -z "$HTTP_PORT" ]]; then
        echo "Unable to find a free HTTP port automatically. Exiting." >&2
        exit 1
      fi
      echo "Port ${desired_http} is busy; automatically using HTTP port ${HTTP_PORT}." >&2
    else
      local fallback_http
      if [[ "$desired_http" -eq 80 ]]; then
        fallback_http=8080
      else
        fallback_http=$((desired_http + 1))
      fi
      HTTP_PORT=$(resolve_busy_port_interactive "HTTP" "$desired_http" "$fallback_http")
    fi
  fi

  if port_in_use "$HTTPS_PORT"; then
    if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
      local fallback_https
      if [[ "$HTTPS_PORT" -eq 443 ]]; then
        fallback_https=8443
      else
        fallback_https=$((HTTPS_PORT + 1))
      fi
      HTTPS_PORT=$(find_free_port "$fallback_https" 200 || true)
      if [[ -z "$HTTPS_PORT" ]]; then
        echo "Unable to find a free HTTPS port automatically. Exiting." >&2
        exit 1
      fi
      echo "Port ${desired_https} is busy; automatically using HTTPS port ${HTTPS_PORT}." >&2
    else
      local fallback_https
      if [[ "$desired_https" -eq 443 ]]; then
        fallback_https=8443
      else
        fallback_https=$((desired_https + 1))
      fi
      HTTPS_PORT=$(resolve_busy_port_interactive "HTTPS" "$desired_https" "$fallback_https")
    fi
  fi
}

# When ACME email is set but port 80 is busy, prompt the user to either free it or continue without TLS for now.
handle_tls_port_conflict() {
  [[ "${NO_TRAEFIK:-0}" -eq 1 ]] && { TLS_ENABLED=0; return 0; }
  TLS_ENABLED=0
  if [[ -n "${BLOBEVM_EMAIL:-}" ]]; then
    # Default to TLS if port 80 is available
    if [[ "${HTTP_PORT:-80}" == "80" ]]; then
      TLS_ENABLED=1
      return 0
    fi
    if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
      TLS_ENABLED=0
      echo "HTTPS requires port 80; continuing without TLS (non-interactive mode)." >&2
      return 0
    fi
    echo
    echo "=== HTTPS/ACME requires inbound port 80 ==="
    echo "You provided an email for Let's Encrypt, but port 80 is currently in use."
    echo "Options:"
    echo "  1) Free port 80 now and retry detection (recommended for HTTPS)."
    echo "  2) Continue WITHOUT TLS for now (you can enable it later after freeing port 80)."
    echo
    while true; do
      read -rp "Choose [1/2]: " choice || true
      case "${choice}" in
        1)
          echo "Press Enter after you have freed port 80 (e.g., stop nginx/apache/caddy)..."
          read -r _ || true
          detect_ports
          if [[ "${HTTP_PORT}" == "80" ]]; then
            TLS_ENABLED=1
            echo "Port 80 is free. Proceeding with HTTPS enabled."
            return 0
          else
            echo "Port 80 still busy. You can choose 1 again to retry or 2 to continue without TLS."
          fi
          ;;
        2)
          TLS_ENABLED=0
          echo "Proceeding without TLS. You can re-run the installer later to enable HTTPS."
          return 0
          ;;
        *)
          echo "Please enter 1 or 2."
          ;;
      esac
    done
  fi
}

detect_external_traefik() {
  # Look for a running Traefik container and offer to reuse it
  local tid
  tid=$(docker ps --filter=ancestor=traefik --format '{{.ID}}' | head -n1 || true)
  if [[ -z "$tid" ]]; then
    # Try by name contains 'traefik'
    tid=$(docker ps --format '{{.ID}} {{.Image}} {{.Names}}' | awk '/traefik/{print $1; exit}')
  fi
  [[ -z "$tid" ]] && return 0

  echo
  echo "Detected an existing Traefik container on this host."
  local reuse
  read -rp "Reuse the existing Traefik instead of deploying a new one? [Y/n]: " reuse || true
  if [[ -z "$reuse" || "${reuse,,}" == y* ]]; then
    # Pick its first attached user-defined network
    local net
    net=$(docker inspect "$tid" -f '{{ range $k, $v := .NetworkSettings.Networks }}{{$k}} {{ end }}' | awk '{for(i=1;i<=NF;i++) if($i!~/(bridge|host|none)/){print $i; exit}}')
    if [[ -z "$net" ]]; then
      echo "Could not determine a suitable user-defined network from the existing Traefik."
      echo "Falling back to deploying our own Traefik."
      return 0
    fi
    TRAEFIK_NETWORK="$net"
    SKIP_TRAEFIK=1
    echo "Reusing Traefik on network '$TRAEFIK_NETWORK'."
  fi
}

# Warn if reusing external Traefik and it forces HTTP->HTTPS while TLS is disabled
warn_if_external_redirect() {
  [[ "${SKIP_TRAEFIK:-0}" -ne 1 ]] && return 0
  [[ "${TLS_ENABLED:-0}" -ne 0 ]] && return 0
  local tid
  tid=$(docker ps --format '{{.ID}} {{.Image}} {{.Names}}' | awk '/traefik/{print $1; exit}')
  [[ -z "$tid" ]] && return 0
  local args
  args=$(docker inspect "$tid" -f '{{range .Args}}{{.}}\n{{end}}' 2>/dev/null || true)
  if echo "$args" | grep -q -- '--entrypoints.web.http.redirections.entryPoint.to=websecure'; then
    echo
    echo "NOTE: External Traefik appears to have HTTP->HTTPS redirection enabled, but TLS is disabled in this setup."
    echo "That will cause browsers/curl to be redirected to HTTPS and likely see 404s if no websecure routers exist."
    echo "To fix: remove the redirection flag from the external Traefik and restart it, or allow this installer to manage Traefik."
    echo "Flag to remove: --entrypoints.web.http.redirections.entryPoint.to=websecure (and related scheme settings)."
  fi
}

# Check DNS for provided domain and print exact A records needed
check_domain_dns() {
  [[ -n "${BLOBEVM_DOMAIN:-}" ]] || return 0
  echo
  echo "Validating DNS for domain: ${BLOBEVM_DOMAIN}"
  local pub4 dns4 base_ok=0 traefik_ok=0
  pub4=$(curl -4 -fsS ifconfig.me || curl -4 -fsS icanhazip.com || true)
  if [[ -z "$pub4" ]]; then
    echo "Could not determine this server's public IPv4 address. Skipping DNS validation." >&2
    return 0
  fi
  # Resolve A records using getent; fallback to host if available
  dns4=$(getent ahostsv4 "$BLOBEVM_DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
  if [[ -z "$dns4" && $(command -v host) ]]; then
    dns4=$(host -4 "$BLOBEVM_DOMAIN" 2>/dev/null | awk '/has address/{print $4}' | sort -u | tr '\n' ' ')
  fi
  if [[ "$dns4" == *"$pub4"* ]]; then base_ok=1; fi
  # Check traefik subdomain specifically
  local traefik_host="traefik.${BLOBEVM_DOMAIN}"
  local dns4_t
  dns4_t=$(getent ahostsv4 "$traefik_host" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
  if [[ -z "$dns4_t" && $(command -v host) ]]; then
    dns4_t=$(host -4 "$traefik_host" 2>/dev/null | awk '/has address/{print $4}' | sort -u | tr '\n' ' ')
  fi
  if [[ "$dns4_t" == *"$pub4"* ]]; then traefik_ok=1; fi

  if [[ $base_ok -eq 1 && $traefik_ok -eq 1 ]]; then
    echo "DNS looks good: ${BLOBEVM_DOMAIN} and traefik.${BLOBEVM_DOMAIN} resolve to ${pub4}."
    return 0
  fi

  echo "DNS for ${BLOBEVM_DOMAIN} is not pointing to this server yet."
  [[ -n "$dns4" ]] && echo "  Current A for ${BLOBEVM_DOMAIN}: ${dns4}"
  [[ -n "$dns4_t" ]] && echo "  Current A for traefik.${BLOBEVM_DOMAIN}: ${dns4_t}"
  echo
  echo "Add the following DNS A records at your DNS provider:"
  echo "  A  *.${BLOBEVM_DOMAIN}     -> ${pub4}"
  echo "  A  traefik.${BLOBEVM_DOMAIN} -> ${pub4}"
  echo
  echo "After updating DNS, allow time for propagation. HTTPS (Let's Encrypt) will only work after ${BLOBEVM_DOMAIN} resolves to ${pub4} and port 80 is reachable."
  return 1
}

# Return 0 when both apex and traefik.<domain> resolve to our public IP
domain_ready() {
  [[ -n "${BLOBEVM_DOMAIN:-}" ]] || return 1
  local pub4 base_ok=0 traefik_ok=0
  pub4=$(curl -4 -fsS ifconfig.me || curl -4 -fsS icanhazip.com || true)
  [[ -z "$pub4" ]] && return 1
  local dns4
  dns4=$(getent ahostsv4 "$BLOBEVM_DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
  if [[ -z "$dns4" && $(command -v host) ]]; then
    dns4=$(host -4 "$BLOBEVM_DOMAIN" 2>/dev/null | awk '/has address/{print $4}' | sort -u | tr '\n' ' ')
  fi
  [[ "$dns4" == *"$pub4"* ]] && base_ok=1
  local traefik_host="traefik.${BLOBEVM_DOMAIN}" dns4_t
  dns4_t=$(getent ahostsv4 "$traefik_host" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
  if [[ -z "$dns4_t" && $(command -v host) ]]; then
    dns4_t=$(host -4 "$traefik_host" 2>/dev/null | awk '/has address/{print $4}' | sort -u | tr '\n' ' ')
  fi
  [[ "$dns4_t" == *"$pub4"* ]] && traefik_ok=1
  [[ $base_ok -eq 1 && $traefik_ok -eq 1 ]]
}

# Optional: wait for DNS to become ready before enabling TLS
wait_for_dns_propagation() {
  # Only relevant when TLS is enabled and HTTP_PORT is 80 (ACME requires 80)
  [[ "${NO_TRAEFIK:-0}" -eq 1 ]] && return 0
  [[ "${TLS_ENABLED:-0}" -eq 1 ]] || return 0
  [[ "${HTTP_PORT:-80}" == "80" ]] || return 0
  domain_ready && return 0
  echo
  echo "DNS for ${BLOBEVM_DOMAIN} does not point to this server yet."
  local ans
  read -rp "Wait for DNS to propagate before continuing? [y/N]: " ans || true
  [[ "${ans,,}" == y* ]] || return 0

  local attempts=40 delay=15 count=0
  echo "Waiting up to $((attempts*delay/60)) minutes. Checking every ${delay}s..."
  while (( count < attempts )); do
    if domain_ready; then
      echo "DNS is now pointing correctly."
      return 0
    fi
    count=$((count+1))
    sleep "$delay"
    # Every 4 attempts (~1 min), ask if we should keep waiting
    if (( count % 4 == 0 && count < attempts )); then
      local cont
      read -t 10 -rp "Still waiting on DNS... keep waiting? [Y/n]: " cont || cont=""
      if [[ -n "$cont" && "${cont,,}" == n* ]]; then
        echo "Skipping further DNS wait."
        return 0
      fi
    fi
  done
  echo "Timed out waiting for DNS. You can continue; HTTPS will start working once DNS is correct."
}

setup_traefik() {
  if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
    echo "Traefik disabled: skipping proxy deployment."
    return 0
  fi
  if [[ "${SKIP_TRAEFIK:-0}" -eq 1 ]]; then
    echo "Skipping Traefik deployment (reusing existing)."
    return 0
  fi
  mkdir -p /opt/blobe-vm/traefik/letsencrypt
  chmod 700 /opt/blobe-vm/traefik/letsencrypt

  local compose_file=/opt/blobe-vm/traefik/docker-compose.yml
  local net_name="${TRAEFIK_NETWORK:-proxy}"
  echo "Writing Traefik docker-compose.yml to $compose_file"

  if [[ "${TLS_ENABLED:-0}" -eq 1 ]]; then
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
    # Write ports with bash expansion to avoid docker compose env interpolation
    {
      echo "    ports:";
      echo "      - \"${HTTP_PORT}:80\"";
      echo "      - \"${HTTPS_PORT}:443\"";
    } >> "$compose_file"
    cat >> "$compose_file" <<YAML
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    networks:
      - ${net_name}
    labels:
      - traefik.enable=true
      # Router rules for the dashboard are only active when a domain is set
      # You can later attach a host rule via labels or set a CNAME like traefik.<domain>
      - traefik.http.services.traefik.loadbalancer.server.port=8080
      - traefik.http.routers.traefik.rule=PathPrefix(`/traefik`)
      - traefik.http.routers.traefik.entrypoints=web
YAML
    if [[ -n "$TRAEFIK_DASHBOARD_AUTH" ]]; then
      # Escape $ to $$ to prevent docker compose from treating bcrypt parts as env variables
      local SAFE_AUTH
      SAFE_AUTH="${TRAEFIK_DASHBOARD_AUTH//$/\$\$}"
      # Add auth middleware labels
      cat >> "$compose_file" <<YAML
      - traefik.http.middlewares.traefik-auth.basicauth.users=${SAFE_AUTH}
      - traefik.http.routers.traefik.middlewares=traefik-auth
YAML
    fi
    cat >> "$compose_file" <<YAML
networks:
  ${net_name}:
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
YAML
    {
      echo "    ports:";
      echo "      - \"${HTTP_PORT}:80\"";
    } >> "$compose_file"
    cat >> "$compose_file" <<YAML
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - ${net_name}
networks:
  ${net_name}:
    external: true
YAML
  fi

  echo "Starting Traefik..."
  (cd /opt/blobe-vm/traefik && docker compose up -d)

  if [[ "$ENABLE_DASHBOARD" -eq 1 ]]; then
    echo "Deploying BlobeVM web dashboard service..."
    # Always remove and repull dashboard container/image to ensure freshness
    echo "[dashboard] Removing old dashboard container/image (if any)..."
    (cd /opt/blobe-vm/traefik && docker compose rm -sf dashboard || true)
    docker image rm -f ghcr.io/library/python:3.11-slim 2>/dev/null || true
    echo "[dashboard] Pulling latest dashboard base image..."
    docker pull ghcr.io/library/python:3.11-slim
    # Append dashboard service to compose file
    cat >> /opt/blobe-vm/traefik/docker-compose.yml <<DASH
  dashboard:
    image: ghcr.io/library/python:3.11-slim
    command: bash -c "apt-get update && apt-get install -y curl jq && pip install --no-cache-dir flask && python /app/app.py"
    volumes:
      - /opt/blobe-vm:/opt/blobe-vm
      - /usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro
      - ${HOST_DOCKER_BIN:-/usr/bin/docker}:/usr/bin/docker:ro
      - /var/run/docker.sock:/var/run/docker.sock
      - /opt/blobe-vm/dashboard/app.py:/app/app.py:ro
    environment:
      - BLOBEDASH_USER=${BLOBEDASH_USER:-}
      - BLOBEDASH_PASS=${BLOBEDASH_PASS:-}
      - HOST_DOCKER_BIN=${HOST_DOCKER_BIN:-/usr/bin/docker}
    networks:
      - ${net_name}
    labels:
      - traefik.enable=true
      - traefik.http.routers.blobe-dashboard.rule=PathPrefix(`/dashboard`)
      - traefik.http.routers.blobe-dashboard.entrypoints=web
      - traefik.http.services.blobe-dashboard.loadbalancer.server.port=5000
DASH
    if [[ "$TLS_ENABLED" -eq 1 ]]; then
      cat >> /opt/blobe-vm/traefik/docker-compose.yml <<'DASH'
      - traefik.http.routers.blobe-dashboard-secure.rule=PathPrefix(`/dashboard`)
      - traefik.http.routers.blobe-dashboard-secure.entrypoints=websecure
      - traefik.http.routers.blobe-dashboard-secure.tls=true
      - traefik.http.routers.blobe-dashboard-secure.tls.certresolver=myresolver
DASH
    fi
    if [[ -n "$BLOBEVM_DOMAIN" ]]; then
      if [[ "$TLS_ENABLED" -eq 1 ]]; then
        cat >> /opt/blobe-vm/traefik/docker-compose.yml <<DASH
      - traefik.http.routers.blobe-dashboard-host.rule=Host(`dashboard.${BLOBEVM_DOMAIN}`)
      - traefik.http.routers.blobe-dashboard-host.entrypoints=websecure
      - traefik.http.routers.blobe-dashboard-host.tls=true
      - traefik.http.routers.blobe-dashboard-host.tls.certresolver=myresolver
      - traefik.http.services.blobe-dashboard-host.loadbalancer.server.port=5000
DASH
      else
        cat >> /opt/blobe-vm/traefik/docker-compose.yml <<DASH
      - traefik.http.routers.blobe-dashboard-host.rule=Host(`dashboard.${BLOBEVM_DOMAIN}`)
      - traefik.http.routers.blobe-dashboard-host.entrypoints=web
      - traefik.http.services.blobe-dashboard-host.loadbalancer.server.port=5000
DASH
      fi
    fi
    (cd /opt/blobe-vm/traefik && docker compose up -d dashboard)
  fi
}

# --- Direct mode dashboard deployment (no Traefik) ---
deploy_dashboard_direct() {
  [[ "${ENABLE_DASHBOARD:-0}" -eq 1 ]] || return 0
  echo "Deploying dashboard in direct mode (no proxy)..."
  local start_port="${BLOBEVM_DIRECT_PORT_START:-20000}"
  local port
  port=$(find_free_port "$start_port" 200 || true)
  if [[ -z "$port" ]]; then
    echo "Could not find a free port for the dashboard. Skipping dashboard deployment." >&2
    return 0
  fi
  DASHBOARD_PORT="$port"
  local docker_bin="${HOST_DOCKER_BIN:-}"
  if [[ -z "$docker_bin" || ! -e "$docker_bin" ]]; then
    docker_bin="$(command -v docker || true)"
  fi
  if [[ -z "$docker_bin" || ! -e "$docker_bin" ]]; then
    echo "Unable to determine docker CLI path for dashboard deployment." >&2
    return 1
  fi
  # Always remove and repull dashboard container/image to ensure freshness
  if docker ps -a --format '{{.Names}}' | grep -qx "blobedash"; then
    echo "[dashboard] Removing old dashboard container..."
    docker rm -f blobedash >/dev/null 2>&1 || true
  fi
  echo "[dashboard] Removing old dashboard image (if any)..."
  docker image rm -f ghcr.io/library/python:3.11-slim 2>/dev/null || true
  echo "[dashboard] Pulling latest dashboard base image..."
  docker pull ghcr.io/library/python:3.11-slim
  docker run -d --name blobedash --restart unless-stopped \
    -p "${DASHBOARD_PORT}:5000" \
    -v /opt/blobe-vm:/opt/blobe-vm \
    -v /usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro \
    -v "${docker_bin}:/usr/bin/docker:ro" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /opt/blobe-vm/dashboard/app.py:/app/app.py:ro \
    -e BLOBEDASH_USER="${BLOBEDASH_USER:-}" \
    -e BLOBEDASH_PASS="${BLOBEDASH_PASS:-}" \
    -e HOST_DOCKER_BIN="${docker_bin}" \
  ghcr.io/library/python:3.11-slim \
  bash -c "apt-get update && apt-get install -y curl jq && pip install --no-cache-dir flask && python /app/app.py" \
    >/dev/null
}

build_image() {
  # Fallback if REPO_DIR is missing or was loaded stale from .env
  if [[ -z "${REPO_DIR:-}" || ! -d "$REPO_DIR" ]]; then
    detect_repo_root
  fi
  local image="blobevm:latest"
  local force="${BLOBEVM_FORCE_REBUILD:-0}"
  # Compute a content hash of the Dockerfile and the VM root/ folder
  local cur_hash prev_hash hash_file
  hash_file="/opt/blobe-vm/.image.hash"
  cur_hash=$( \
    cd "$REPO_DIR" && { \
      { sha256sum Dockerfile 2>/dev/null || true; } \
      && { find root -type f -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null || true; } \
    } | sha256sum | awk '{print $1}'
  )
  [[ -f "$hash_file" ]] && prev_hash="$(cat "$hash_file" 2>/dev/null || true)" || prev_hash=""

  # Check if image exists
  local img_id
  img_id=$(docker images -q "$image" 2>/dev/null || true)

  if [[ "$force" == "1" || -z "$img_id" || "$cur_hash" != "$prev_hash" ]]; then
    echo "Building the BlobeVM image from $REPO_DIR ..."
    docker build -t "$image" "$REPO_DIR"
    echo "$cur_hash" > "$hash_file" || true
    echo "Build complete."
  else
    echo "Image '$image' is up-to-date. Skipping rebuild."
  fi
}

install_manager() {
  echo "Installing blobe-vm-manager CLI..."
  # Only replace if changed to preserve running processes and avoid unnecessary writes
  local src="$REPO_DIR/server/blobe-vm-manager" dst="/usr/local/bin/blobe-vm-manager"
  if [[ -f "$dst" ]]; then
    if ! cmp -s "$src" "$dst"; then
      install -Dm755 "$src" "$dst"
    else
      # Ensure permissions are correct even if unchanged
      chmod 755 "$dst"
    fi
  else
    install -Dm755 "$src" "$dst"
  fi
  mkdir -p /opt/blobe-vm/instances
  # Ensure dashboard app is available under /opt for both modes
  mkdir -p /opt/blobe-vm/dashboard
  if [[ -f "$REPO_DIR/dashboard/app.py" ]]; then
    cp -f "$REPO_DIR/dashboard/app.py" /opt/blobe-vm/dashboard/app.py
  fi
  # Install dashboard service assets
  mkdir -p /opt/blobe-vm/server
  if [[ -f "$REPO_DIR/server/blobedash-ensure.sh" ]]; then
    install -Dm755 "$REPO_DIR/server/blobedash-ensure.sh" /opt/blobe-vm/server/blobedash-ensure.sh
  fi
  if [[ -f "$REPO_DIR/server/blobedash.service" ]]; then
    install -Dm644 "$REPO_DIR/server/blobedash.service" /etc/systemd/system/blobedash.service
  fi
  local base_path="${BASE_PATH:-/vm}"
  # Helper to single-quote values safely for shell
  sh_q() { printf "'%s'" "$(printf %s "$1" | sed "s/'/'\''/g")"; }
  {
    echo "BLOBEVM_DOMAIN=$(sh_q "${BLOBEVM_DOMAIN:-}")";
    echo "BLOBEVM_EMAIL=$(sh_q "${BLOBEVM_EMAIL:-}")";
    echo "ENABLE_TLS=$(sh_q "${TLS_ENABLED}")";
    echo "ENABLE_KVM=$(sh_q "${ENABLE_KVM}")";
    echo "REPO_DIR=$(sh_q "${REPO_DIR}")";
    echo "BASE_PATH=$(sh_q "${base_path}")";
    echo "FORCE_HTTPS=$(sh_q "${FORCE_HTTPS}")";
    echo "TRAEFIK_DASHBOARD_AUTH=$(sh_q "${TRAEFIK_DASHBOARD_AUTH}")";
    echo "HSTS_ENABLED=$(sh_q "${HSTS_ENABLED}")";
    echo "ENABLE_DASHBOARD=$(sh_q "${ENABLE_DASHBOARD}")";
    echo "HTTP_PORT=$(sh_q "${HTTP_PORT}")";
    echo "HTTPS_PORT=$(sh_q "${HTTPS_PORT}")";
    echo "TRAEFIK_NETWORK=$(sh_q "${TRAEFIK_NETWORK}")";
    echo "SKIP_TRAEFIK=$(sh_q "${SKIP_TRAEFIK:-0}")";
    echo "NO_TRAEFIK=$(sh_q "${NO_TRAEFIK:-0}")";
    echo "DASHBOARD_PORT=$(sh_q "${DASHBOARD_PORT:-}")";
    echo "DIRECT_PORT_START=$(sh_q "${BLOBEVM_DIRECT_PORT_START:-20000}")";
    echo "HOST_DOCKER_BIN=$(sh_q "${HOST_DOCKER_BIN}")";
  } > /opt/blobe-vm/.env
}

# Verify that the dashboard will be able to show VM status by ensuring
# docker CLI and socket are reachable from a tiny probe container and that
# the manager script is on the host.
preflight_dashboard_runtime() {
  echo "Running dashboard preflight checks..."
  # 1) Host docker binary path
  local docker_bin="${HOST_DOCKER_BIN:-}"
  if [[ -z "$docker_bin" || ! -e "$docker_bin" ]]; then
    docker_bin="$(command -v docker || true)"
  fi
  if [[ -z "$docker_bin" || ! -e "$docker_bin" ]]; then
    echo "[preflight] Could not locate docker CLI on host." >&2
    return 1
  fi
  # 2) Docker socket
  if [[ ! -S /var/run/docker.sock ]]; then
    echo "[preflight] /var/run/docker.sock not found. Is Docker running?" >&2
    return 1
  fi
  # 3) Manager binary
  if [[ ! -x /usr/local/bin/blobe-vm-manager ]]; then
    echo "[preflight] blobe-vm-manager missing at /usr/local/bin/blobe-vm-manager" >&2
    return 1
  fi
  # 4) Instances dir exists
  mkdir -p /opt/blobe-vm/instances

  # 5) In-container probe: ensure docker ps works when mounting CLI and socket
  local probe="blobedash-preflight-$$"
  docker rm -f "$probe" >/dev/null 2>&1 || true
  if ! docker run --rm --name "$probe" \
      -v "/opt/blobe-vm:/opt/blobe-vm" \
      -v "/usr/local/bin/blobe-vm-manager:/usr/local/bin/blobe-vm-manager:ro" \
      -v "$docker_bin:/usr/bin/docker:ro" \
      -v "/var/run/docker.sock:/var/run/docker.sock" \
      ghcr.io/library/python:3.11-slim bash -lc "docker ps >/dev/null 2>&1"; then
    echo "[preflight] docker ps failed inside probe container. Check docker socket permissions." >&2
    return 1
  fi
  echo "Dashboard preflight checks passed."
}

maybe_create_first_vm() {
  echo
  local auto_create="$(normalize_bool "${BLOBEVM_AUTO_CREATE_VM:-0}")"
  if [[ "$auto_create" == "1" ]]; then
    local name="${BLOBEVM_INITIAL_VM_NAME:-alpha}"
    echo "Auto-creating initial VM '${name}'."
    blobe-vm-manager create "$name"
    return 0
  fi

  if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
    echo "Skipping initial VM creation (non-interactive mode)."
    return 0
  fi

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
  local base_path="${BASE_PATH:-/vm}"
  local http_suffix=""
  local https_suffix=""
  [[ "${HTTP_PORT}" != "80" ]] && http_suffix=":${HTTP_PORT}"
  [[ "${HTTPS_PORT}" != "443" ]] && https_suffix=":${HTTPS_PORT}"

  if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
    echo "- Mode: Direct (no reverse proxy)."
    if [[ "${ENABLE_DASHBOARD:-0}" -eq 1 && -n "${DASHBOARD_PORT:-}" ]]; then
      echo "- Dashboard: http://<SERVER_IP>:${DASHBOARD_PORT}/dashboard"
    else
      echo "- Dashboard: disabled (enable by setting BLOBEVM_ENABLE_DASHBOARD=1 and re-running install)."
    fi
    echo "- VM URLs:  http://<SERVER_IP>:<port>/  (each VM chooses a free high port; the CLI prints it)"
  elif [[ -n "${BLOBEVM_DOMAIN:-}" ]]; then
    if [[ "${TLS_ENABLED:-0}" -eq 1 ]]; then
      echo "- Dashboard: https://dashboard.${BLOBEVM_DOMAIN}${https_suffix}/"
      echo "- Traefik:  https://traefik.${BLOBEVM_DOMAIN}${https_suffix}/"
      echo "- VM URLs:  https://<name>.${BLOBEVM_DOMAIN}${https_suffix}/ (path fallback ${base_path}/<name>/)"
    else
      echo "- Dashboard: http://dashboard.${BLOBEVM_DOMAIN}${http_suffix}/"
      echo "- Traefik:  http://traefik.${BLOBEVM_DOMAIN}${http_suffix}/"
      echo "- VM URLs:  http://<name>.${BLOBEVM_DOMAIN}${http_suffix}/ (path fallback ${base_path}/<name>/)"
    fi
    echo "- Ensure DNS: *.${BLOBEVM_DOMAIN} and traefik.${BLOBEVM_DOMAIN} → this server's IP."
  else
    echo "- Dashboard: http://<SERVER_IP>${http_suffix}/dashboard"
    if [[ "${TLS_ENABLED:-0}" -eq 1 ]]; then
      echo "- Dashboard (HTTPS): https://<SERVER_IP>${https_suffix}/dashboard"
    fi
    echo "- VM URLs: http://<SERVER_IP>${http_suffix}${base_path}/<name>/"
    [[ "${TLS_ENABLED:-0}" -eq 1 ]] && echo "- VM URLs (HTTPS): https://<SERVER_IP>${https_suffix}${base_path}/<name>/"
  fi
  echo "- Manage VMs: blobe-vm-manager [list|create|start|stop|delete|rename] <name>"
  echo "- Uninstall everything: blobe-vm-manager nuke"
}

main() {
  require_root "$@"
  detect_repo_root
  # Detect existing install and load settings
  UPDATE_MODE=0
  if [[ -d /opt/blobe-vm || -f /usr/local/bin/blobe-vm-manager ]]; then
    UPDATE_MODE=1
    load_existing_env || true
  fi

  apply_env_overrides
  ASSUME_DEFAULTS=${ASSUME_DEFAULTS:-0}

  if [[ "$UPDATE_MODE" -eq 1 ]]; then
    echo "Detected existing BlobeVM installation."
    local reuse_cfg
    if [[ "${BLOBEVM_REUSE_SETTINGS:-}" == "1" ]]; then
      reuse_cfg="y"
    elif [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
      reuse_cfg="y"
    else
      read -rp "Use existing settings and update components? [Y/n]: " reuse_cfg || true
    fi
    if [[ -z "$reuse_cfg" || "${reuse_cfg,,}" == y* ]]; then
      # Keep existing settings from .env; ensure defaults for missing
      BLOBEVM_DOMAIN="${BLOBEVM_DOMAIN:-}"
      BLOBEVM_EMAIL="${BLOBEVM_EMAIL:-}"
      ENABLE_KVM=${ENABLE_KVM:-0}
      FORCE_HTTPS=${FORCE_HTTPS:-0}
      HSTS_ENABLED=${HSTS_ENABLED:-0}
      ENABLE_DASHBOARD=${ENABLE_DASHBOARD:-1}
      TRAEFIK_NETWORK="${TRAEFIK_NETWORK:-proxy}"
      SKIP_TRAEFIK=${SKIP_TRAEFIK:-0}
      HTTP_PORT=${HTTP_PORT:-80}
      HTTPS_PORT=${HTTPS_PORT:-443}
      TLS_ENABLED=${ENABLE_TLS:-0}
      # If TLS is disabled, ensure FORCE_HTTPS is not set to avoid unintended redirects
      if [[ "${TLS_ENABLED:-0}" -eq 0 ]]; then
        FORCE_HTTPS=0
      fi
      BASE_PATH=${BASE_PATH:-/vm}
      # Ensure REPO_DIR points to a real directory (avoid stale temp paths)
      if [[ -z "${REPO_DIR:-}" || ! -d "$REPO_DIR" ]]; then
        detect_repo_root
      fi
    else
      prompt_config
      # User chose to reconfigure: clear any previously loaded derived values so we re-detect ports/TLS
      unset HTTP_PORT HTTPS_PORT TLS_ENABLED
    fi
  else
    prompt_config
  fi
  BASE_PATH=${BASE_PATH:-/vm}
  install_prereqs
  # External Traefik only if we're not already configured to skip and not in direct mode
  if [[ "${SKIP_TRAEFIK:-0}" -ne 1 && "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    detect_external_traefik || true
  fi
  # If previous config said to reuse external Traefik but it's gone, auto-enable our deployment
  validate_skip_traefik || true
  ensure_network
  if [[ "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    check_domain_dns || true
  fi
  # If reusing an external Traefik, skip our port detection and TLS prompts entirely
  if [[ "${SKIP_TRAEFIK:-0}" -ne 1 && "${NO_TRAEFIK:-0}" -ne 1 ]]; then
    # If updating, prefer existing port settings; otherwise detect
    if [[ -z "${HTTP_PORT:-}" || -z "${HTTPS_PORT:-}" ]]; then
      detect_ports
    fi
    if [[ -z "${TLS_ENABLED:-}" ]]; then
      handle_tls_port_conflict
    fi
    if [[ "${TLS_ENABLED:-0}" -eq 0 ]]; then
      FORCE_HTTPS=0
    fi
    # If TLS is enabled, optionally wait for DNS to point before launching Traefik
    wait_for_dns_propagation || true
  fi
  setup_traefik
  build_image
  install_manager
  # Check dashboard runtime dependencies before deployment
  preflight_dashboard_runtime || true
  # Direct mode dashboard
  if [[ "${NO_TRAEFIK:-0}" -eq 1 ]]; then
    # Ensure systemd unit is installed and enabled
    if [[ -f /etc/systemd/system/blobedash.service ]]; then
      systemctl daemon-reload || true
      systemctl enable blobedash.service || true
      systemctl start blobedash.service || true
        echo "Restarting dashboard service..."
        sudo systemctl restart blobedash
        sudo systemctl status blobedash --no-pager -l
    else
      # Fallback to one-shot docker run if systemd missing for any reason
      deploy_dashboard_direct || true
    fi
    # Load DASHBOARD_PORT from .env if assigned by ensure script
    if [[ -f /opt/blobe-vm/.env ]]; then
      # shellcheck disable=SC1091
      set +u
      while IFS='=' read -r k v; do
        [[ -z "$k" || "$k" =~ ^# ]] && continue
        v="${v%\'}"; v="${v#\'}"; v="${v%\"}"; v="${v#\"}"
        if [[ "$k" == "DASHBOARD_PORT" ]]; then export DASHBOARD_PORT="$v"; fi
      done < /opt/blobe-vm/.env
      set -u
    fi
    # Persist current env back to .env (now including DASHBOARD_PORT when present)
    install_manager

    # Migrate existing instances to direct mode: assign ports automatically
    echo "Migrating existing VMs to direct mode (assigning high ports)..."
    shopt -s nullglob
    for d in /opt/blobe-vm/instances/*; do
      [[ -d "$d" ]] || continue
      n="$(basename "$d")"
      cname="blobevm_${n}"
      # Remove old container if present to trigger port-publish recreation
      if docker ps -a --format '{{.Names}}' | grep -qx "$cname"; then
        docker rm -f "$cname" >/dev/null 2>&1 || true
      fi
      blobe-vm-manager start "$n" || true
    done
  fi
  # If reusing external Traefik while TLS is disabled, warn about possible redirect
  warn_if_external_redirect || true
  if [[ "$UPDATE_MODE" -eq 1 ]]; then
    echo "Update complete. Existing VMs were not modified."
  else
    maybe_create_first_vm
  fi
  print_success
  echo
  echo "Current VMs:"
  if command -v blobe-vm-manager >/dev/null 2>&1; then
    blobe-vm-manager list || true
  else
    echo "  (manager not found in PATH)"
  fi
}

main "$@"
