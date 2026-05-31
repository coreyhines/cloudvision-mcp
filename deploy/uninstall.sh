#!/usr/bin/env bash
# Stop CloudVision MCP quadlet services and remove generated unit files (does not delete data).
set -euo pipefail

QUADLET_SUBDIR="${CLOUDVISION_MCP_QUADLET_SUBDIR:-cloudvision-mcp}"
QUADLET_DIR="/etc/containers/systemd/${QUADLET_SUBDIR}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

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
