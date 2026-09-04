"""Raw Sysdb path probe used to diagnose which device paths return data.

Diagnostic only. It runs one unary Get against an arbitrary device path and
reports the shape of what came back, so a caller can tell an empty path from a
wrong-depth path without guessing.
"""

from __future__ import annotations

import logging
from typing import Any

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient

from cvp_mcp.env import normalize_api_token
from cvp_mcp.grpc.connector import get_device_path, serialize_cloudvision_data
from cvp_mcp.grpc.envelope import tool_envelope

DATA_SOURCE = "connector:device:probe_path"
MAX_PREVIEW_KEYS = 50


def parse_probe_path(path: str) -> list:
    """Turn ``"Sysdb/environment/*"`` into connector path elements.

    ``*`` becomes a ``Wildcard``; empty segments are dropped so leading,
    trailing, and repeated slashes are all tolerated.
    """
    segments = [seg for seg in (path or "").split("/") if seg.strip()]
    if not segments:
        raise ValueError("path must contain at least one element")
    return [Wildcard() if seg.strip() == "*" else seg.strip() for seg in segments]


def _cvp_addr(datadict: dict[str, Any]) -> str:
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def probe_device_path(datadict: dict[str, Any], device_id: str, path: str) -> dict:
    """Run one Get at ``path`` for ``device_id`` and describe what returned."""
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source=DATA_SOURCE,
            coverage="none",
            warnings=["missing_device_id"],
            obj={},
        )
    try:
        path_elts = parse_probe_path(path)
    except ValueError:
        return tool_envelope(
            device_id=device_id,
            data_source=DATA_SOURCE,
            coverage="none",
            warnings=["invalid_path"],
            obj={},
        )

    # Echo the requested path back as plain strings so the caller can see
    # exactly which segments were treated as wildcards.
    display_elements = ["*" if isinstance(e, Wildcard) else e for e in path_elts]
    token = normalize_api_token(datadict.get("cvtoken"))
    try:
        with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
            raw = serialize_cloudvision_data(
                get_device_path(client, device_id, path_elts)
            )
    except Exception as exc:
        logging.error("probe_device_path: %s", exc)
        return tool_envelope(
            device_id=device_id,
            data_source=DATA_SOURCE,
            coverage="none",
            warnings=[f"probe_failed:{exc}"],
            obj={"path_elements": display_elements},
        )

    raw = raw if isinstance(raw, dict) else {}
    keys = [str(k) for k in raw.keys()]
    obj = {
        "path_elements": display_elements,
        "key_count": len(keys),
        "keys": keys[:MAX_PREVIEW_KEYS],
        "child_types": {
            str(k): type(v).__name__ for k, v in list(raw.items())[:MAX_PREVIEW_KEYS]
        },
    }
    if len(keys) > MAX_PREVIEW_KEYS:
        obj["keys_truncated"] = True
    return tool_envelope(
        device_id=device_id,
        data_source=DATA_SOURCE,
        coverage="full" if keys else "none",
        warnings=[] if keys else ["no_data_at_path"],
        obj=obj,
    )
