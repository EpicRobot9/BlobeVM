#!/usr/bin/env bash

set -euo pipefail

LOGFILE="/var/log/blobe-vm-bootstrap.log"
mkdir -p "$(dirname "$LOGFILE")"
touch "$LOGFILE"
chmod 600 "$LOGFILE"
exec > >(tee -a "$LOGFILE") 2>&1

if [[ $EUID -ne 0 ]]; then
  echo "This installer must run as root. Re-running with sudo..."
  exec sudo -E bash "$0" "$@"
fi

trap 'echo "[ERROR] line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

info() { printf '\n==> %s\n' "$*"; }
need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    return 1
  fi
}

REPO_URL="${BLOBEVM_REPO_URL:-https://github.com/EpicRobot9/BlobeVM.git}"
REPO_BRANCH="${BLOBEVM_BRANCH:-main}"
INSTALL_ROOT="${BLOBEVM_ROOT:-/opt/blobe-vm}"
REPO_DIR="${INSTALL_ROOT}/repo"
INSTALLER_REL="server/install.sh"

info "Ensuring minimal prerequisites are present"
if ! need_cmd git; then
  if need_cmd apt-get; then
    apt-get update -y
    apt-get install -y git ca-certificates
  else
    echo "git is required but package manager is unsupported. Install git and rerun." >&2
    exit 1
  fi
fi

mkdir -p "$INSTALL_ROOT"

if [[ -d "$REPO_DIR/.git" ]]; then
  info "Updating existing BlobeVM clone"
  (
    cd "$REPO_DIR"
    git fetch --force origin "$REPO_BRANCH"
    git reset --hard "origin/$REPO_BRANCH"
  )
else
  info "Cloning BlobeVM repository"
  rm -rf "$REPO_DIR"
  git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"
fi

INSTALLER_PATH="${REPO_DIR}/${INSTALLER_REL}"
if [[ ! -f "$INSTALLER_PATH" ]]; then
  echo "Installer script $INSTALLER_REL not found in repository." >&2
  exit 1
fi

info "Launching main installer"
cd "$REPO_DIR"
chmod +x "$INSTALLER_PATH" 2>/dev/null || true
exec bash "$INSTALLER_PATH" "$@"
