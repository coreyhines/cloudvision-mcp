#!/usr/bin/env bash
# CloudVision MCP centralized install (Linux, Podman quadlets + Caddy auth/TLS front).
# Idempotent: safe to re-run (refresh quadlets, optional image rebuild, systemd reload).
#
#   sudo bash deploy/install.sh
# Non-interactive (recommended under sudo — no TTY prompts):
#   sudo CLOUDVISION_MCP_INSTALL_ROOT=/opt/containerdata/cloudvision-mcp \
#        CLOUDVISION_MCP_IMAGE_REPO=hub.example.com/cloudvision_mcp \
#        bash deploy/install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_REPO_URL="${CLOUDVISION_MCP_REPO_URL:-}"
GIT_REF="${CLOUDVISION_MCP_GIT_REF:-main}"
INSTALL_ROOT="${CLOUDVISION_MCP_INSTALL_ROOT:-/opt/containerdata/cloudvision-mcp}"
IMAGE_REPO="${CLOUDVISION_MCP_IMAGE_REPO:-}"
IMAGE_TAG="${CLOUDVISION_MCP_IMAGE_TAG:-latest}"
QUADLET_SUBDIR="${CLOUDVISION_MCP_QUADLET_SUBDIR:-cloudvision-mcp}"
RUNTIME="${CLOUDVISION_MCP_RUNTIME:-podman}"
SKIP_IMAGE="${CLOUDVISION_MCP_SKIP_IMAGE:-0}"
SRC_DIR=""
DEPLOY_DIR=""

usage() {
  echo "Usage: $0 [--runtime podman|docker] [--skip-image]" >&2
  echo "  Builds the container image and pushes to CLOUDVISION_MCP_IMAGE_REPO:TAG." >&2
  echo "  Env: CLOUDVISION_MCP_INSTALL_ROOT (default /opt/containerdata/cloudvision-mcp)" >&2
  echo "       CLOUDVISION_MCP_IMAGE_REPO (required non-interactive; prompted on TTY)" >&2
  echo "       CLOUDVISION_MCP_IMAGE_TAG (default latest)" >&2
  echo "       CLOUDVISION_MCP_SKIP_IMAGE=1 or --skip-image to skip build/push" >&2
  echo "       CLOUDVISION_MCP_QUADLET_SUBDIR (under /etc/containers/systemd/)" >&2
  echo "  Run 'podman login <registry>' before install if the registry requires auth." >&2
  echo "  Under sudo, prefer env vars for non-interactive install (see script header)." >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      RUNTIME="${2:-}"
      [[ -n "${RUNTIME}" ]] || {
        echo "--runtime needs a value" >&2
        exit 1
      }
      shift 2
      ;;
    --skip-image)
      SKIP_IMAGE=1
      shift
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

if [[ "${RUNTIME}" != "podman" && "${RUNTIME}" != "docker" ]]; then
  echo "Invalid --runtime (use podman or docker)" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This installer must run as root (sudo)." >&2
  exit 1
fi

is_interactive_shell() {
  local tty_device=/dev/tty
  if [[ -e "${tty_device}" && -r "${tty_device}" ]]; then
    return 0
  fi
  if [[ -t 0 ]]; then
    return 0
  fi
  return 1
}

prompt_install_root() {
  local tty_device=/dev/tty
  if is_interactive_shell && [[ -z "${CLOUDVISION_MCP_INSTALL_ROOT:-}" ]]; then
    read -r -p "Install root (env + Caddyfile) [/opt/containerdata/cloudvision-mcp]: " _root <"${tty_device}" || true
    if [[ -n "${_root}" ]]; then
      INSTALL_ROOT="${_root}"
    fi
  fi
}

prompt_install_root
CADDYFILE_HOST="${INSTALL_ROOT}/Caddyfile"
ENV_HOST_PATH="${INSTALL_ROOT}/environment"
SRC_DIR="${INSTALL_ROOT}/src"

echo "cloudvision-mcp install: INSTALL_ROOT=${INSTALL_ROOT} RUNTIME=${RUNTIME}" >&2

mkdir -p "${INSTALL_ROOT}" "/etc/containers/systemd/${QUADLET_SUBDIR}"

resolve_src_dir() {
  if [[ -n "${DEFAULT_REPO_URL}" ]]; then
    if [[ ! -d "${SRC_DIR}/.git" ]]; then
      git clone --depth 1 --branch "${GIT_REF}" "${DEFAULT_REPO_URL}" "${SRC_DIR}"
    else
      git -C "${SRC_DIR}" fetch origin "${GIT_REF}" --depth 1
      git -C "${SRC_DIR}" reset --hard "origin/${GIT_REF}"
    fi
    DEPLOY_DIR="${SRC_DIR}/deploy"
  elif [[ -f "${REPO_ROOT}/deploy/install.sh" ]]; then
    DEPLOY_DIR="${REPO_ROOT}/deploy"
    SRC_DIR="${REPO_ROOT}"
  else
    echo "Cannot find deploy/ (run from a clone or set CLOUDVISION_MCP_REPO_URL)." >&2
    exit 1
  fi
}

resolve_src_dir

verify_deploy_tree() {
  local missing=0
  local f
  for f in \
    "${DEPLOY_DIR}/environment.example" \
    "${DEPLOY_DIR}/Containerfile" \
    "${DEPLOY_DIR}/cloudvision-mcp.pod.example" \
    "${DEPLOY_DIR}/cloudvision-mcp-app.container.example" \
    "${DEPLOY_DIR}/cloudvision-mcp-caddy.container.example"; do
    if [[ ! -f "${f}" ]]; then
      echo "Missing required file: ${f}" >&2
      missing=1
    fi
  done
  if [[ "${missing}" -ne 0 ]]; then
    exit 1
  fi
}

verify_deploy_tree

normalize_image_repo() {
  IMAGE_REPO="${IMAGE_REPO#/}"
  IMAGE_REPO="${IMAGE_REPO%/}"
}

collect_image_settings() {
  local tty_device=/dev/tty
  local interactive=0
  if is_interactive_shell; then
    interactive=1
  fi

  if [[ "${interactive}" -eq 1 ]]; then
    local _default="${IMAGE_REPO:-${CLOUDVISION_MCP_IMAGE_REPO:-}}"
    read -r -p "Container registry/image (e.g. hub.example.com/cloudvision_mcp) [${_default}]: " _repo <"${tty_device}" || true
    if [[ -n "${_repo}" ]]; then
      IMAGE_REPO="${_repo}"
    elif [[ -n "${_default}" ]]; then
      IMAGE_REPO="${_default}"
    fi
    if [[ -z "${CLOUDVISION_MCP_IMAGE_TAG:-}" ]]; then
      read -r -p "Container image tag [latest]: " CLOUDVISION_MCP_IMAGE_TAG <"${tty_device}" || true
    fi
  fi

  IMAGE_REPO="${IMAGE_REPO:-${CLOUDVISION_MCP_IMAGE_REPO:-}}"
  IMAGE_TAG="${CLOUDVISION_MCP_IMAGE_TAG:-${IMAGE_TAG:-latest}}"
  normalize_image_repo

  if [[ "${SKIP_IMAGE}" -eq 0 && -z "${IMAGE_REPO}" ]]; then
    echo "Container registry/image is required (set CLOUDVISION_MCP_IMAGE_REPO or run interactively)." >&2
    exit 1
  fi
}

build_and_push_image() {
  local full="${IMAGE_REPO}:${IMAGE_TAG}"
  echo "Building ${full} from ${SRC_DIR}..." >&2
  "${RUNTIME}" build -f "${DEPLOY_DIR}/Containerfile" -t "${full}" "${SRC_DIR}"
  echo "Pushing ${full}..." >&2
  "${RUNTIME}" push "${full}"
  echo "Image published: ${full}" >&2
}

collect_settings() {
  local tty_device=/dev/tty
  local interactive=0
  if is_interactive_shell; then
    interactive=1
  fi

  if [[ "${interactive}" -eq 1 ]]; then
    if [[ -z "${CLOUDVISION_MCP_PUBLIC_HOST:-}" ]]; then
      read -r -p "Public HTTPS hostname [cloudvision-mcp.example.com]: " CLOUDVISION_MCP_PUBLIC_HOST <"${tty_device}" || true
    fi
    if [[ -z "${CLOUDVISION_MCP_AUTH_MODE:-}" ]]; then
      read -r -p "Auth mode (none|basic|forward_auth) [basic]: " CLOUDVISION_MCP_AUTH_MODE <"${tty_device}" || true
    fi
    if [[ -z "${CLOUDVISION_MCP_TLS_CERTS:-}" ]]; then
      read -r -p "Host TLS cert directory [/opt/containerdata/certs/wild]: " CLOUDVISION_MCP_TLS_CERTS <"${tty_device}" || true
    fi
    if [[ -z "${CLOUDVISION_MCP_NETWORK:-}" ]]; then
      read -r -p "Pod Network= (empty to omit; e.g. net-10): " CLOUDVISION_MCP_NETWORK <"${tty_device}" || true
    fi
    if [[ -z "${CLOUDVISION_MCP_IP:-}" ]]; then
      read -r -p "Pod static IP= (empty to omit): " CLOUDVISION_MCP_IP <"${tty_device}" || true
    fi
    if [[ -z "${CLOUDVISION_MCP_IP6:-}" ]]; then
      read -r -p "Pod static IP6= (empty to omit): " CLOUDVISION_MCP_IP6 <"${tty_device}" || true
    fi
    if [[ -z "${CLOUDVISION_MCP_DNS:-}" ]]; then
      read -r -p "Pod DNS= (space-separated, empty to omit): " CLOUDVISION_MCP_DNS <"${tty_device}" || true
    fi
    if [[ "${CLOUDVISION_MCP_AUTH_MODE:-basic}" == "basic" ]]; then
      if [[ -z "${CLOUDVISION_MCP_BASIC_AUTH_USER:-}" ]]; then
        read -r -p "Basic auth username [mcp]: " CLOUDVISION_MCP_BASIC_AUTH_USER <"${tty_device}" || true
      fi
      if [[ -z "${CLOUDVISION_MCP_BASIC_AUTH_HASH:-}" ]]; then
        read -r -s -p "Basic auth password (input hidden): " _pw <"${tty_device}" || true
        echo >&2
        if [[ -n "${_pw}" ]]; then
          CLOUDVISION_MCP_BASIC_AUTH_HASH="$("${RUNTIME}" run --rm docker.io/library/caddy:2-alpine \
            caddy hash-password --plaintext "${_pw}")"
        fi
      fi
    fi
    if [[ "${CLOUDVISION_MCP_AUTH_MODE:-}" == "forward_auth" && -z "${CLOUDVISION_MCP_FORWARD_AUTH_URL:-}" ]]; then
      read -r -p "forward_auth upstream URL (e.g. http://127.0.0.1:4180): " CLOUDVISION_MCP_FORWARD_AUTH_URL <"${tty_device}" || true
    fi
  fi

  CLOUDVISION_MCP_PUBLIC_HOST=${CLOUDVISION_MCP_PUBLIC_HOST:-cloudvision-mcp.example.com}
  CLOUDVISION_MCP_AUTH_MODE=${CLOUDVISION_MCP_AUTH_MODE:-basic}
  CLOUDVISION_MCP_BASIC_AUTH_USER=${CLOUDVISION_MCP_BASIC_AUTH_USER:-mcp}
  CLOUDVISION_MCP_POD_NAME=${CLOUDVISION_MCP_POD_NAME:-cloudvision-mcp-pod}
  CLOUDVISION_MCP_CONTAINER_NAME=${CLOUDVISION_MCP_CONTAINER_NAME:-cloudvision-mcp-app}
  CLOUDVISION_MCP_CADDY_CONTAINER_NAME=${CLOUDVISION_MCP_CADDY_CONTAINER_NAME:-cloudvision-mcp-caddy}
  CLOUDVISION_MCP_TLS_CERTS=${CLOUDVISION_MCP_TLS_CERTS:-/opt/containerdata/certs/wild}
  CLOUDVISION_MCP_NETWORK=${CLOUDVISION_MCP_NETWORK:-}
  CLOUDVISION_MCP_IP=${CLOUDVISION_MCP_IP:-}
  CLOUDVISION_MCP_IP6=${CLOUDVISION_MCP_IP6:-}
  CLOUDVISION_MCP_DNS=${CLOUDVISION_MCP_DNS:-}
  CLOUDVISION_MCP_FORWARD_AUTH_URL=${CLOUDVISION_MCP_FORWARD_AUTH_URL:-}
}

write_caddyfile() {
  local out=$1
  local host=$2
  local auth_mode=$3
  local basic_user=$4
  local basic_hash=$5
  local forward_url=$6
  {
    printf '%s\n' '# Generated by deploy/install.sh — edit auth via environment + re-run install.'
    printf '%s\n' '{'
    printf '\tadmin off\n'
    printf '%s\n' '}'
    printf '\n'
    printf '%s\n' "${host} {"
    printf '\ttls /opt/certs/wild/fullchain.pem /opt/certs/wild/privkey.pem\n'
    printf '\n'
    printf '\tencode zstd gzip\n'
    printf '\n'
    case "${auth_mode}" in
      basic)
        if [[ -z "${basic_hash}" ]]; then
          echo "basic auth selected but CLOUDVISION_MCP_BASIC_AUTH_HASH is empty" >&2
          exit 1
        fi
        printf '\tbasicauth {\n'
        printf '\t\t%s %s\n' "${basic_user}" "${basic_hash}"
        printf '\t}\n'
        printf '\n'
        ;;
      forward_auth)
        if [[ -z "${forward_url}" ]]; then
          echo "forward_auth selected but CLOUDVISION_MCP_FORWARD_AUTH_URL is empty" >&2
          exit 1
        fi
        printf '\tforward_auth %s {\n' "${forward_url}"
        printf '\t\turi /verify\n'
        printf '\t\tcopy_headers Remote-User Remote-Email X-Forwarded-User\n'
        printf '\t}\n'
        printf '\n'
        ;;
      none)
        echo "warning: CLOUDVISION_MCP_AUTH_MODE=none — MCP is TLS-only, no client auth." >&2
        ;;
      *)
        echo "Invalid CLOUDVISION_MCP_AUTH_MODE: ${auth_mode}" >&2
        exit 1
        ;;
    esac
    printf '\treverse_proxy http://127.0.0.1:8000 {\n'
    printf '\t\tflush_interval -1\n'
    printf '\t}\n'
    printf '%s\n' '}'
    printf '\n'
    printf '%s\n' ':80 {'
    printf '\tredir https://%s{uri} permanent\n' "${host}"
    printf '%s\n' '}'
  } >"${out}"
  chmod 644 "${out}"
}

write_pod_quadlet() {
  local out=$1
  local pod_name=$2
  {
    printf '%s\n' '[Unit]'
    printf '%s\n' 'Description=CloudVision MCP pod (shared network for Caddy + MCP)'
    printf '%s\n' 'Wants=network-online.target'
    printf '%s\n' 'After=network-online.target'
    printf '%s\n' ''
    printf '%s\n' '[Pod]'
    printf '%s\n' "PodName=${pod_name}"
    if [[ -n "${CLOUDVISION_MCP_NETWORK}" ]]; then
      printf '%s\n' "Network=${CLOUDVISION_MCP_NETWORK}"
    fi
    if [[ -n "${CLOUDVISION_MCP_IP}" ]]; then
      printf '%s\n' "IP=${CLOUDVISION_MCP_IP}"
    fi
    if [[ -n "${CLOUDVISION_MCP_IP6}" ]]; then
      printf '%s\n' "IP6=${CLOUDVISION_MCP_IP6}"
    fi
    local dns
    for dns in ${CLOUDVISION_MCP_DNS}; do
      if [[ -n "${dns}" ]]; then
        printf '%s\n' "DNS=${dns}"
      fi
    done
    printf '%s\n' ''
    printf '%s\n' '[Service]'
    printf '%s\n' 'Restart=on-failure'
    printf '%s\n' ''
    printf '%s\n' '[Install]'
    printf '%s\n' 'WantedBy=multi-user.target'
  } >"${out}"
  chmod 644 "${out}"
}

write_app_quadlet() {
  local out=$1
  local pod_svc=$2
  local pod_file=$3
  {
    printf '%s\n' '[Unit]'
    printf '%s\n' 'Description=CloudVision MCP (Podman quadlet, Streamable HTTP)'
    printf '%s\n' "After=network-online.target ${pod_svc}"
    printf '%s\n' 'Wants=network-online.target'
    printf '%s\n' "Requires=${pod_svc}"
    printf '%s\n' ''
    printf '%s\n' '[Container]'
    printf '%s\n' "Pod=${pod_file}"
    printf '%s\n' "Image=${IMAGE_REPO}:${IMAGE_TAG}"
    printf '%s\n' "ContainerName=${CLOUDVISION_MCP_CONTAINER_NAME}"
    printf '%s\n' "Environment=CLOUDVISION_MCP_INSTALL_ROOT=${INSTALL_ROOT}"
    printf '%s\n' "EnvironmentFile=${INSTALL_ROOT}/environment"
    printf '%s\n' "Volume=${INSTALL_ROOT}:${INSTALL_ROOT}:ro,Z"
    printf '%s\n' "Volume=${CLOUDVISION_MCP_TLS_CERTS}:/opt/certs/wild:ro,Z"
    printf '%s\n' 'HealthCmd=["sh", "-c", "curl -sS --max-time 8 http://127.0.0.1:8000/ -o /dev/null"]'
    printf '%s\n' 'HealthInterval=30s'
    printf '%s\n' 'HealthTimeout=10s'
    printf '%s\n' 'HealthRetries=3'
    printf '%s\n' 'HealthStartPeriod=60s'
    printf '%s\n' ''
    printf '%s\n' '[Service]'
    printf '%s\n' 'Restart=on-failure'
    printf '%s\n' 'RestartSec=10'
    printf '%s\n' 'TimeoutStartSec=120'
    printf '%s\n' 'TimeoutStopSec=40'
    printf '%s\n' ''
    printf '%s\n' '[Install]'
    printf '%s\n' 'WantedBy=multi-user.target'
  } >"${out}"
  chmod 644 "${out}"
}

write_caddy_quadlet() {
  local out=$1
  local pod_svc=$2
  local app_svc=$3
  local pod_file=$4
  {
    printf '%s\n' '[Unit]'
    printf '%s\n' 'Description=TLS and auth front for CloudVision MCP (Caddy)'
    printf '%s\n' 'Documentation=https://caddyserver.com/docs/caddyfile'
    printf '%s\n' "After=network-online.target ${pod_svc} ${app_svc}"
    printf '%s\n' 'Wants=network-online.target'
    printf '%s\n' "Requires=${pod_svc}"
    printf '%s\n' "Requires=${app_svc}"
    printf '%s\n' ''
    printf '%s\n' '[Container]'
    printf '%s\n' "Pod=${pod_file}"
    printf '%s\n' 'Image=docker.io/library/caddy:2-alpine'
    printf '%s\n' "ContainerName=${CLOUDVISION_MCP_CADDY_CONTAINER_NAME}"
    printf '%s\n' "Volume=${CLOUDVISION_MCP_TLS_CERTS}:/opt/certs/wild:ro,Z"
    printf '%s\n' "Volume=${INSTALL_ROOT}/Caddyfile:/etc/caddy/Caddyfile:ro,Z"
    printf '%s\n' 'HealthCmd=["sh", "-c", "curl -fsS --max-time 10 http://127.0.0.1:80/ -o /dev/null"]'
    printf '%s\n' 'HealthInterval=30s'
    printf '%s\n' 'HealthTimeout=10s'
    printf '%s\n' 'HealthRetries=3'
    printf '%s\n' 'HealthStartPeriod=30s'
    printf '%s\n' ''
    printf '%s\n' '[Service]'
    printf '%s\n' 'Restart=on-failure'
    printf '%s\n' 'RestartSec=5'
    printf '%s\n' 'TimeoutStartSec=60'
    printf '%s\n' ''
    printf '%s\n' '[Install]'
    printf '%s\n' 'WantedBy=multi-user.target'
  } >"${out}"
  chmod 644 "${out}"
}

ENV_HOST_PATH="${INSTALL_ROOT}/environment"
if [[ ! -f "${ENV_HOST_PATH}" ]]; then
  sed "s|/opt/containerdata/cloudvision-mcp|${INSTALL_ROOT}|g" \
    "${DEPLOY_DIR}/environment.example" >"${ENV_HOST_PATH}"
  chmod 600 "${ENV_HOST_PATH}"
  echo "Created ${ENV_HOST_PATH} — set CVP and CVPTOKEN before relying on the service." >&2
fi

if [[ -f "${ENV_HOST_PATH}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_HOST_PATH}"
  set +a
fi

collect_image_settings
collect_settings

update_env_key() {
  local file=$1 key=$2 value=$3
  local tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "${file}" >"${tmp}" || true
  printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
  mv "${tmp}" "${file}"
}

write_caddyfile \
  "${CADDYFILE_HOST}" \
  "${CLOUDVISION_MCP_PUBLIC_HOST}" \
  "${CLOUDVISION_MCP_AUTH_MODE}" \
  "${CLOUDVISION_MCP_BASIC_AUTH_USER}" \
  "${CLOUDVISION_MCP_BASIC_AUTH_HASH:-}" \
  "${CLOUDVISION_MCP_FORWARD_AUTH_URL}"

update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_PUBLIC_HOST" "${CLOUDVISION_MCP_PUBLIC_HOST}"
update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_AUTH_MODE" "${CLOUDVISION_MCP_AUTH_MODE}"
update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_BASIC_AUTH_USER" "${CLOUDVISION_MCP_BASIC_AUTH_USER}"
if [[ -n "${CLOUDVISION_MCP_BASIC_AUTH_HASH:-}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_BASIC_AUTH_HASH" "${CLOUDVISION_MCP_BASIC_AUTH_HASH}"
fi
if [[ -n "${CLOUDVISION_MCP_FORWARD_AUTH_URL:-}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_FORWARD_AUTH_URL" "${CLOUDVISION_MCP_FORWARD_AUTH_URL}"
fi
update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_POD_NAME" "${CLOUDVISION_MCP_POD_NAME}"
update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_TLS_CERTS" "${CLOUDVISION_MCP_TLS_CERTS}"
if [[ -n "${CLOUDVISION_MCP_NETWORK}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_NETWORK" "${CLOUDVISION_MCP_NETWORK}"
fi
if [[ -n "${CLOUDVISION_MCP_IP}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_IP" "${CLOUDVISION_MCP_IP}"
fi
if [[ -n "${CLOUDVISION_MCP_IP6}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_IP6" "${CLOUDVISION_MCP_IP6}"
fi
if [[ -n "${CLOUDVISION_MCP_DNS}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_DNS" "${CLOUDVISION_MCP_DNS}"
fi
if [[ -n "${IMAGE_REPO}" ]]; then
  update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_IMAGE_REPO" "${IMAGE_REPO}"
fi
update_env_key "${ENV_HOST_PATH}" "CLOUDVISION_MCP_IMAGE_TAG" "${IMAGE_TAG}"
chmod 600 "${ENV_HOST_PATH}"

if [[ "${RUNTIME}" == "podman" ]]; then
  command -v podman >/dev/null 2>&1 || {
    echo "podman not found" >&2
    exit 1
  }
  QUADLET_DIR="/etc/containers/systemd/${QUADLET_SUBDIR}"
  mkdir -p "${QUADLET_DIR}"
  POD_FILE="cloudvision-mcp.pod"
  POD_SVC="cloudvision-mcp-pod.service"
  APP_BASENAME="cloudvision-mcp-app"
  CADDY_BASENAME="cloudvision-mcp-caddy"
  APP_SVC="${APP_BASENAME}.service"
  CADDY_SVC="${CADDY_BASENAME}.service"

  if [[ "${SKIP_IMAGE}" -eq 0 ]]; then
    build_and_push_image
  else
    echo "Skipping image build/push (--skip-image)." >&2
  fi

  write_pod_quadlet "${QUADLET_DIR}/${POD_FILE}" "${CLOUDVISION_MCP_POD_NAME}"
  write_app_quadlet "${QUADLET_DIR}/${APP_BASENAME}.container" "${POD_SVC}" "${POD_FILE}"
  write_caddy_quadlet "${QUADLET_DIR}/${CADDY_BASENAME}.container" "${POD_SVC}" "${APP_SVC}" "${POD_FILE}"

  systemctl daemon-reload
  enable_or_start() {
    local unit_file=$1
    local service=$2
    if systemctl enable "${unit_file}" 2>/dev/null; then
      systemctl restart "${service}" || systemctl start "${service}" || true
    else
      systemctl restart "${service}" || systemctl start "${service}" || true
    fi
  }
  enable_or_start "${QUADLET_DIR}/${POD_FILE}" "${POD_SVC}"
  enable_or_start "${QUADLET_DIR}/${APP_BASENAME}.container" "${APP_SVC}"
  enable_or_start "${QUADLET_DIR}/${CADDY_BASENAME}.container" "${CADDY_SVC}"
else
  echo "docker path not implemented; use podman quadlets." >&2
  exit 1
fi

echo "Install finished." >&2
echo "  Env:       ${ENV_HOST_PATH}" >&2
echo "  Caddyfile: ${CADDYFILE_HOST}" >&2
echo "  Quadlets:  /etc/containers/systemd/${QUADLET_SUBDIR}/" >&2
echo "  Image:     ${IMAGE_REPO}:${IMAGE_TAG}" >&2
echo "  MCP URL:   https://${CLOUDVISION_MCP_PUBLIC_HOST}/mcp" >&2
echo "  Auth:      ${CLOUDVISION_MCP_AUTH_MODE}" >&2
