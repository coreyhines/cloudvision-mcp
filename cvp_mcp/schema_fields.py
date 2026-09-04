"""Shared JSON-Schema property snippets reused across grouped MCP tools."""

from __future__ import annotations

from typing import Any

# Only fields whose meaning is identical everywhere they appear belong here.
# `query` is omitted: inventory search vs events search vs studio tag query differ.
SHARED_FIELDS: dict[str, dict[str, Any]] = {
    "device_id": {
        "type": "string",
        "description": (
            "CloudVision device identifier: serial number (canonical), hostname, "
            "FQDN, or system MAC; resolved to serial before querying."
        ),
    },
    "workspace_id": {
        "type": "string",
        "description": "CloudVision workspace id (draft workspaces use ws-mcp-*).",
    },
    "studio_id": {
        "type": "string",
        "description": "Studio id within a workspace.",
    },
    "confirm": {
        "type": "boolean",
        "description": (
            "Apply the change. Writes default to dry-run unless confirm=True "
            "and preview_token matches the prior dry-run."
        ),
    },
    "preview_token": {
        "type": "string",
        "description": "Token from the previous dry-run call; required when confirm=True.",
    },
}


def is_shared(name: str) -> bool:
    """Return True when ``name`` has a canonical shared schema definition."""
    return name in SHARED_FIELDS
