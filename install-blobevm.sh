#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME="${0##*/}"
BRANCH="${BLOBEVM_BRANCH:-main}"
REPO_URL="${BLOBEVM_REPO_URL:-https://github.com/EpicRobot9/BlobeVM.git}"
INSTALL_ROOT="${BLOBEVM_ROOT:-/opt/blobe-vm}"
REPO_DIR="${INSTALL_ROOT}/repo"
INSTALLER_REL="server/install.sh"
LOGFILE="${BLOBEVM_BOOTSTRAP_LOG:-/var/log/blobe-vm-bootstrap.log}"
ASSUME_YES=0
SKIP_PULL=0
DRY_RUN=0
INSTALLER_ARGS=()

handle_error() {
  local exit_code=$?
  local line=$1
  local cmd=$2
  printf '\n[ERROR] %s failed at line %s: %s\n' "$SCRIPT_NAME" "$line" "$cmd" >&2
  printf 'See %s for full logs.\n' "$LOGFILE" >&2
  exit "$exit_code"
}
trap 'handle_error ${LINENO} "${BASH_COMMAND}"' ERR

supports_color() {
  [[ -t 1 && -z "${NO_COLOR:-}" && $(tput colors 2>/dev/null || echo 0) -ge 8 ]]
}
if supports_color; then
  C_RESET='\033[0m'
  C_BOLD='\033[1m'
  C_INFO='\033[1;34m'
  C_WARN='\033[1;33m'
  C_GOOD='\033[1;32m'
else
  C_RESET=''
  C_BOLD=''
  C_INFO=''
  C_WARN=''
  C_GOOD=''
fi

say() { printf '%b%s%b\n' "$C_INFO" "$1" "$C_RESET"; }
success() { printf '%b%s%b\n' "$C_GOOD" "$1" "$C_RESET"; }
warn() { printf '%b%s%b\n' "$C_WARN" "$1" "$C_RESET"; }
die() { printf '%s\n' "$1" >&2; exit 1; }

usage() {
  cat <<USAGE
${SCRIPT_NAME} [options] [-- installer-args]

Clone or update the BlobeVM repository and invoke server/install.sh.

Options:
  -b, --branch <name>     Git branch (default: ${BRANCH})
  -r, --repo <url>        Repository URL (default: ${REPO_URL})
  -d, --dest <path>       Installation root (default: ${INSTALL_ROOT})
  -y, --yes               Assume yes for prompts
      --skip-pull         Skip git fetch/reset if repo exists
      --dry-run           Show plan then exit
  -h, --help              Show this help

Any arguments after -- are passed to server/install.sh.
USAGE
}

parse_args() {
  local positional=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -b|--branch) [[ $# -lt 2 ]] && die "Missing value for $1"; BRANCH="$2"; shift 2 ;;
      -r|--repo) [[ $# -lt 2 ]] && die "Missing value for $1"; REPO_URL="$2"; shift 2 ;;
      -d|--dest) [[ $# -lt 2 ]] && die "Missing value for $1"; INSTALL_ROOT="$2"; REPO_DIR="${INSTALL_ROOT}/repo"; shift 2 ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      --skip-pull) SKIP_PULL=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage; exit 0 ;;
      --) shift; INSTALLER_ARGS=("$@"); return ;;
      *) positional+=("$1"); shift ;;
    esac
  done
  if [[ ${#positional[@]} -gt 0 ]]; then
    INSTALLER_ARGS=("${positional[@]}")
  fi
}

ensure_logfile() {
  mkdir -p "$(dirname "$LOGFILE")"
  touch "$LOGFILE"
  chmod 600 "$LOGFILE" 2>/dev/null || true
  exec > >(tee -a "$LOGFILE") 2>&1
  say "Logging to $LOGFILE"
}

reexec_as_root() {
  if [[ $EUID -eq 0 ]]; then
    return
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    die "This installer must run as root or with sudo access."
  fi
  say "Re-running with sudo..."
  exec sudo -E bash "$0" "$@"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_git_if_missing() {
  if need_cmd git; then
    return
  fi
  if need_cmd apt-get; then
    say "Installing git via apt-get..."
    apt-get update -y
    apt-get install -y git ca-certificates
  else
    die "git is required. Install git manually and rerun."
  fi
}

describe_plan() {
  cat <<PLAN
Configuration:
  Repo URL   : ${REPO_URL}
  Branch     : ${BRANCH}
  Install dir: ${INSTALL_ROOT}
  Log file   : ${LOGFILE}
PLAN
}

confirm_or_exit() {
  if [[ $ASSUME_YES -eq 1 ]]; then
    return
  fi
  read -rp "Proceed with these settings? [Y/n] " reply || true
  if [[ ${reply,,} =~ ^n|no$ ]]; then
    die "Aborted by user."
  fi
}

clone_or_update_repo() {
  mkdir -p "$INSTALL_ROOT"
  if [[ -d "$REPO_DIR/.git" ]]; then
    if [[ $SKIP_PULL -eq 1 ]]; then
      say "Using existing repository at $REPO_DIR (skip-pull enabled)."
      return
    fi
    say "Updating repository at $REPO_DIR..."
    (cd "$REPO_DIR" && git fetch --force origin "$BRANCH" && git reset --hard "origin/$BRANCH")
  else
    say "Cloning repository into $REPO_DIR..."
    rm -rf "$REPO_DIR"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"
  fi
}

ensure_installer_present() {
  INSTALLER_PATH="${REPO_DIR}/${INSTALLER_REL}"
  if [[ -f "$INSTALLER_PATH" ]]; then
    return
  fi
  warn "Installer script missing locally; attempting recovery."
  if git -C "$REPO_DIR" cat-file -e "origin/${BRANCH}:${INSTALLER_REL}" 2>/dev/null; then
    git -C "$REPO_DIR" show "origin/${BRANCH}:${INSTALLER_REL}" > "$INSTALLER_PATH"
  elif git -C "$REPO_DIR" cat-file -e "HEAD:${INSTALLER_REL}" 2>/dev/null; then
    git -C "$REPO_DIR" show "HEAD:${INSTALLER_REL}" > "$INSTALLER_PATH"
  else
    die "Unable to locate ${INSTALLER_REL} in repository."
  fi
  chmod +x "$INSTALLER_PATH"
}

run_main_installer() {
  say "Delegating to ${INSTALLER_REL}..."
  chmod +x "$INSTALLER_PATH" 2>/dev/null || true
  exec bash "$INSTALLER_PATH" "${INSTALLER_ARGS[@]}"
}


parse_args "$@"
reexec_as_root "$@"
ensure_logfile
describe_plan
confirm_or_exit

# Default: disable Traefik unless explicitly set
export BLOBEVM_NO_TRAEFIK="${BLOBEVM_NO_TRAEFIK:-1}"

install_git_if_missing
need_cmd curl || warn "curl not detected; downstream installer may install it."
need_cmd wget || warn "wget not detected; downstream installer may install it."
clone_or_update_repo
ensure_installer_present

# Detect code changes: compare repo HEAD to installed files
CHANGED=0
if [[ -d "$INSTALL_ROOT" && -d "$REPO_DIR/.git" ]]; then
  REPO_HASH=$(cd "$REPO_DIR" && git rev-parse HEAD)
  INST_HASH_FILE="$INSTALL_ROOT/.last_install_hash"
  LAST_HASH=""
  [[ -f "$INST_HASH_FILE" ]] && LAST_HASH=$(cat "$INST_HASH_FILE")
  if [[ "$REPO_HASH" != "$LAST_HASH" ]]; then
    CHANGED=1
    echo "$REPO_HASH" > "$INST_HASH_FILE"
  fi
fi

if [[ $DRY_RUN -eq 1 && $CHANGED -eq 0 ]]; then
  warn "Dry run: no changes were made."
  exit 0
fi

run_main_installer
