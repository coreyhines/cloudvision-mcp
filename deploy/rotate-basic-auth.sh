#!/usr/bin/env bash
# Rotate Caddy Basic Auth for CloudVision MCP (no container image rebuild).
#
#   sudo bash deploy/rotate-basic-auth.sh
# Non-interactive:
#   sudo CLOUDVISION_MCP_BASIC_AUTH_PASSWORD='new-secret' bash deploy/rotate-basic-auth.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

INSTALL_ROOT="${CLOUDVISION_MCP_INSTALL_ROOT:-/opt/containerdata/cloudvision-mcp}"
RUNTIME="${CLOUDVISION_MCP_RUNTIME:-podman}"
BASIC_AUTH_USER=""
CADDY_SVC="cloudvision-mcp-caddy.service"

usage() {
  echo "Usage: $0 [--user USER] [--help]" >&2
  echo "  Rotates Caddy Basic Auth password without rebuilding the MCP container image." >&2
  echo "  Env: CLOUDVISION_MCP_INSTALL_ROOT (default /opt/containerdata/cloudvision-mcp)" >&2
  echo "       CLOUDVISION_MCP_BASIC_AUTH_PASSWORD (non-interactive; not written to disk)" >&2
  echo "       CLOUDVISION_MCP_RUNTIME (default podman)" >&2
  echo "  Updates \$INSTALL_ROOT/environment and Caddyfile, then restarts ${CADDY_SVC}." >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      BASIC_AUTH_USER="${2:-}"
      [[ -n "${BASIC_AUTH_USER}" ]] || {
        echo "--user needs a value" >&2
        exit 1
      }
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script must run as root (sudo)." >&2
  exit 1
fi

ENV_HOST_PATH="${INSTALL_ROOT}/environment"
CADDYFILE_HOST="${INSTALL_ROOT}/Caddyfile"

if [[ ! -f "${ENV_HOST_PATH}" ]]; then
  echo "Missing ${ENV_HOST_PATH} — run deploy/install.sh first." >&2
  exit 1
fi

set -a
load_environment_file "${ENV_HOST_PATH}"
set +a

CLOUDVISION_MCP_AUTH_MODE=${CLOUDVISION_MCP_AUTH_MODE:-basic}
if [[ "${CLOUDVISION_MCP_AUTH_MODE}" != "basic" ]]; then
  echo "Auth mode is '${CLOUDVISION_MCP_AUTH_MODE}', not basic." >&2
  echo "This script only rotates Caddy basicauth credentials." >&2
  echo "For forward_auth or none, update ${ENV_HOST_PATH} and re-run deploy/install.sh --skip-image." >&2
  exit 1
fi

CLOUDVISION_MCP_BASIC_AUTH_USER=${BASIC_AUTH_USER:-${CLOUDVISION_MCP_BASIC_AUTH_USER:-mcp}}
CLOUDVISION_MCP_PUBLIC_HOST=${CLOUDVISION_MCP_PUBLIC_HOST:-cloudvision-mcp.example.com}

command -v "${RUNTIME}" >/dev/null 2>&1 || {
  echo "${RUNTIME} not found" >&2
  exit 1
}

read_new_password() {
  local tty_device=/dev/tty
  local pw confirm

  if [[ -n "${CLOUDVISION_MCP_BASIC_AUTH_PASSWORD:-}" ]]; then
    printf '%s' "${CLOUDVISION_MCP_BASIC_AUTH_PASSWORD}"
    return 0
  fi

  if ! is_interactive_shell; then
    echo "Set CLOUDVISION_MCP_BASIC_AUTH_PASSWORD or run interactively with a TTY." >&2
    exit 1
  fi

  read -r -s -p "New Basic auth password for user '${CLOUDVISION_MCP_BASIC_AUTH_USER}': " pw <"${tty_device}" || true
  echo >&2
  if [[ -z "${pw}" ]]; then
    echo "Password cannot be empty." >&2
    exit 1
  fi
  read -r -s -p "Confirm password: " confirm <"${tty_device}" || true
  echo >&2
  if [[ "${pw}" != "${confirm}" ]]; then
    echo "Passwords do not match." >&2
    exit 1
  fi
  printf '%s' "${pw}"
}

_new_pw="$(read_new_password)"
CLOUDVISION_MCP_BASIC_AUTH_HASH="$(hash_basic_auth_password "${_new_pw}")"
_client_header="$(format_basic_auth_header "${CLOUDVISION_MCP_BASIC_AUTH_USER}" "${_new_pw}")"
unset _new_pw

write_caddyfile \
  "${CADDYFILE_HOST}" \
  "${CLOUDVISION_MCP_PUBLIC_HOST}" \
  "basic" \
  "${CLOUDVISION_MCP_BASIC_AUTH_USER}" \
  "${CLOUDVISION_MCP_BASIC_AUTH_HASH}" \
  ""

update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_BASIC_AUTH_USER" "${CLOUDVISION_MCP_BASIC_AUTH_USER}"
update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_BASIC_AUTH_HASH" "${CLOUDVISION_MCP_BASIC_AUTH_HASH}"
chmod 600 "${ENV_HOST_PATH}"

echo "Updated ${ENV_HOST_PATH} and ${CADDYFILE_HOST}." >&2
systemctl restart "${CADDY_SVC}"
echo "Restarted ${CADDY_SVC}." >&2

echo >&2
echo "Update MCP clients (e.g. ~/.config/cloudvision-mcp/client.env for scripts/cursor-remote-mcp.sh):" >&2
echo "  CLOUDVISION_MCP_AUTH_HEADER=${_client_header}" >&2
echo "  MCP URL: https://${CLOUDVISION_MCP_PUBLIC_HOST}/mcp" >&2
