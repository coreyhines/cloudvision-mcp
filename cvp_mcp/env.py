"""Shared environment and credential normalization for CloudVision MCP."""

from __future__ import annotations

import os


def normalize_api_token(token: str | None) -> str:
    """Strip whitespace and optional Bearer prefix from CVPTOKEN (env-file safe)."""
    if not token:
        return ""
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def normalize_cvp_endpoint(cvp: str | None) -> str:
    """Strip URL scheme/path; ensure host:443 for gRPC (matches Arista CVP examples)."""
    if not cvp:
        return ""
    cvp = cvp.strip()
    for prefix in ("https://", "http://"):
        if cvp.startswith(prefix):
            cvp = cvp[len(prefix) :]
    cvp = cvp.split("/")[0]
    if ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def resolve_cert_path(certfile: str | None) -> str | None:
    if not certfile:
        return None
    certfile = certfile.strip()
    if not certfile:
        return None
    if os.path.isabs(certfile):
        return certfile
    root = os.environ.get("CLOUDVISION_MCP_INSTALL_ROOT", "").strip()
    if root:
        return os.path.join(root, certfile)
    return certfile


def env_datadict_from_os() -> dict:
    """Build the same ``datadict`` shape as ``cloudvision_mcp.get_env_vars``."""
    return {
        "cvtoken": normalize_api_token(os.environ.get("CVPTOKEN")),
        "cvp": normalize_cvp_endpoint(os.environ.get("CVP")),
        "cert": resolve_cert_path(os.environ.get("CERT")),
    }
