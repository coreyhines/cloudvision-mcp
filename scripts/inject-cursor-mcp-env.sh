#!/usr/bin/env bash
# Inject CloudVision MCP auth into the macOS user session so Cursor (GUI)
# can resolve ${env:CLOUDVISION_MCP_AUTH_HEADER} for streamable_http headers.
set -euo pipefail

CLIENT_ENV="${HOME}/.config/cloudvision-mcp/client.env"
HOME_ENV="${HOME}/.env"

load_env_file() {
  local file=$1
  [[ -f "$file" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
}

if [[ -f "$CLIENT_ENV" ]]; then
  load_env_file "$CLIENT_ENV"
elif [[ -f "$HOME_ENV" ]]; then
  load_env_file "$HOME_ENV"
fi

if [[ -z "${CLOUDVISION_MCP_AUTH_HEADER:-}" ]]; then
  echo "inject-cursor-mcp-env: CLOUDVISION_MCP_AUTH_HEADER not set in ${CLIENT_ENV} or ${HOME_ENV}" >&2
  exit 1
fi

launchctl setenv CLOUDVISION_MCP_AUTH_HEADER "$CLOUDVISION_MCP_AUTH_HEADER"
