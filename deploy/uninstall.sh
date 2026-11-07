#!/usr/bin/env bash
# Stop CloudVision MCP quadlet services and remove generated unit files (does not delete data).
#
#   sudo bash deploy/uninstall.sh [--force]
#
# Refuses when $INSTALL_ROOT/Caddyfile was written by another deployer, because
# then that deployer owns the services too. --force (or CLOUDVISION_MCP_FORCE=1)
# removes them anyway.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

QUADLET_SUBDIR="${CLOUDVISION_MCP_QUADLET_SUBDIR:-cloudvision-mcp}"
QUADLET_DIR="/etc/containers/systemd/${QUADLET_SUBDIR}"
INSTALL_ROOT="${CLOUDVISION_MCP_INSTALL_ROOT:-/opt/containerdata/cloudvision-mcp}"
FORCE="${CLOUDVISION_MCP_FORCE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    *)
      echo "Usage: $0 [--force]" >&2
      exit 1
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

require_own_install "${INSTALL_ROOT}/Caddyfile" "uninstall.sh" "${FORCE}"

for svc in cloudvision-mcp-caddy.service cloudvision-mcp-app.service cloudvision-mcp-pod.service; do
  systemctl stop "${svc}" 2>/dev/null || true
  systemctl disable "${svc}" 2>/dev/null || true
done

rm -f \
  "${QUADLET_DIR}/cloudvision-mcp.pod" \
  "${QUADLET_DIR}/cloudvision-mcp-app.container" \
  "${QUADLET_DIR}/cloudvision-mcp-caddy.container"

systemctl daemon-reload
echo "Removed quadlets from ${QUADLET_DIR}. Install root under /opt/containerdata was not deleted." >&2
