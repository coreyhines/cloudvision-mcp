"""Process/env gates and preview tokens for Studios Phase 2 write tools.

This module is intentionally free of HTTP and MCP concerns. Registration of
write tools is a *separate* filter from :func:`cvp_mcp.tool_access.tool_enabled`
(which only reads ``CVP_MCP_DISABLED_TOOLS``). See ``docs/studios-phase2-spec.md``
for the canonical gate semantics.
"""

from __future__ import annotations

import hashlib
import json
import os

WRITES_ENV = "CLOUDVISION_MCP_ALLOW_WRITES"

# There is deliberately no submit gate. Workspace submit was retired
# 2026-09-02 (docs/studios-phase2-final-spec.md §A): the MCP stops at build and
# the human reviews and submits the workspace in the CVP UI.


def _env_is_one(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def writes_enabled() -> bool:
    """Return True only when ``CLOUDVISION_MCP_ALLOW_WRITES`` is exactly ``"1"``."""
    return _env_is_one(WRITES_ENV)


def preview_token(tool_name: str, args: dict) -> str:
    """Deterministic sha256 hex digest over ``tool_name`` and canonical args.

    Canonical JSON: sorted keys, compact separators, ``default=str`` so that
    arbitrary values hash stably regardless of insertion order.
    """
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    payload = f"{tool_name}|{canonical}".encode()
    return hashlib.sha256(payload).hexdigest()


def check_preview_token(tool_name: str, args: dict, token: str | None) -> str | None:
    """Return ``None`` when ``token`` matches, else the refusal code.

    A missing (``None``) or mismatched token yields ``"preview_required"``.
    """
    if token is None or token != preview_token(tool_name, args):
        return "preview_required"
    return None


def validate_workspace_id(workspace_id: str) -> str | None:
    """Validate a draft workspace id.

    Returns ``None`` when valid, otherwise the refusal code:
    ``"workspace_id_required"`` / ``"builtin_workspace_forbidden"`` /
    ``"invalid_workspace_id"``. Leading/trailing whitespace is stripped first.
    """
    ws = workspace_id.strip()
    if not ws:
        return "workspace_id_required"
    if ws.lower().startswith("builtin-"):
        return "builtin_workspace_forbidden"
    if not ws.startswith("ws-mcp-"):
        return "invalid_workspace_id"
    return None
