"""BGP and RIB-style data via Connector device dataset (best-effort paths)."""

from __future__ import annotations

import logging
from typing import Any

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient

from cvp_mcp.env import normalize_api_token
from cvp_mcp.grpc.connector import get_device_path, serialize_cloudvision_data
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.sysdb_parse import flatten_nested_device_map


def _cvp_addr(datadict: dict[str, Any]) -> str:
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def _get_path(datadict: dict[str, Any], device_id: str, path: list) -> dict[str, Any]:
    token = normalize_api_token(datadict.get("cvtoken"))
    with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
        raw = get_device_path(client, device_id, path)
    raw = serialize_cloudvision_data(raw)
    return flatten_nested_device_map(raw) if isinstance(raw, dict) else {}


def grpc_get_bgp_status(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/routing/bgp",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )
    obj: dict[str, Any] = {"device_id": device_id, "bgp_snapshot": {}}
    candidate_paths = (
        [device_id, "Sysdb", "routing", "bgp", "status", Wildcard()],
        [device_id, "Sysdb", "routing", "bgp", "config", Wildcard()],
        [device_id, "Smash", "routing", "bgp", "status", Wildcard()],
    )
    for p in candidate_paths:
        try:
            snap = _get_path(datadict, device_id, list(p))
            if snap:
                obj["bgp_snapshot"] = snap
                obj["_path_used"] = "/".join(str(x) for x in p)
                break
        except Exception as e:
            logging.debug("bgp path %s: %s", p, e)
    if not obj["bgp_snapshot"]:
        warnings.append("no_bgp_data_at_known_paths")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/routing/bgp",
        coverage=(
            "partial" if warnings else ("full" if obj["bgp_snapshot"] else "partial")
        ),
        obj=obj,
        warnings=warnings,
    )


def grpc_get_routes(
    datadict: dict[str, Any], device_id: str, vrf: str = "default"
) -> dict[str, Any]:
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    vrf = (vrf or "default").strip() or "default"
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/routing",
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    items: list[dict[str, Any]] = []
    paths = (
        [
            device_id,
            "Sysdb",
            "routing",
            "status",
            "rib",
            vrf,
            Wildcard(),
        ],
        [
            device_id,
            "Sysdb",
            "routing",
            "status",
            Wildcard(),
            "rib",
            Wildcard(),
        ],
    )
    used_path = ""
    for p in paths:
        try:
            snap = _get_path(datadict, device_id, list(p))
            if not snap:
                continue
            used_path = "/".join(str(x) for x in p)
            for prefix, rec in snap.items():
                if not isinstance(rec, dict):
                    continue
                items.append(
                    {
                        "vrf": vrf,
                        "destination": str(prefix),
                        "detail": rec if isinstance(rec, dict) else str(rec),
                    }
                )
            if items:
                break
        except Exception as e:
            logging.debug("routes path %s: %s", p, e)

    items.sort(key=lambda x: (x.get("destination") or "", x.get("vrf") or ""))
    if not items:
        warnings.append("no_route_data_at_known_paths")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/routing",
        coverage="partial" if warnings else "full",
        items=items,
        obj={"vrf": vrf, "path_hint": used_path},
        warnings=warnings,
    )
