"""Unified response envelope for hybrid Resource API + Connector tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def tool_envelope(
    *,
    device_id: str | None = None,
    data_source: str,
    coverage: str = "full",
    items: list[Any] | None = None,
    obj: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a normalized MCP tool payload."""
    out: dict[str, Any] = {
        "device_id": device_id,
        "collected_at": datetime.now(UTC).isoformat(),
        "data_source": data_source,
        "coverage": coverage,
        "warnings": list(warnings or []),
    }
    if items is not None:
        out["items"] = items
    if obj is not None:
        out["object"] = obj
    return out
