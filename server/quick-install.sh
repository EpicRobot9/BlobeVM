#!/usr/bin/env bash
set -euo pipefail

# One-line installer for BlobeVM on a fresh server
# Usage:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/EpicRobot9/BlobeVM/main/server/quick-install.sh)"

if [[ $EUID -ne 0 ]]; then
  echo "Re-running as root..."
  exec sudo -E bash "$0" "$@"
fi

apt-get update -y >/dev/null 2>&1 || true
apt-get install -y git curl ca-certificates >/dev/null 2>&1 || true

TMP_DIR="/tmp/blobevm-install-$(date +%s)"
mkdir -p "$TMP_DIR"
cd "$TMP_DIR"

echo "Cloning BlobeVM repo..."
git clone --depth 1 https://github.com/EpicRobot9/BlobeVM.git repo
cd repo

echo "Running installer..."
bash server/install.sh

echo "Cleaning up temp directory..."
cd /
rm -rf "$TMP_DIR"

echo "Done. Use 'blobe-vm-manager create <name>' to make your first VM."
