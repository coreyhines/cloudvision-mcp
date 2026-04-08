"""Features, EVPN/VxLAN hints, and system health via Connector (best-effort)."""

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


def _fetch(
    datadict: dict[str, Any], device_id: str, path: list, label: str
) -> tuple[str, dict[str, Any]]:
    token = normalize_api_token(datadict.get("cvtoken"))
    try:
        with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
            raw = get_device_path(client, device_id, path)
        data = serialize_cloudvision_data(raw)
        data = flatten_nested_device_map(data) if isinstance(data, dict) else {}
        return label, data
    except Exception as e:
        logging.debug("overlay fetch %s: %s", label, e)
        return label, {"_error": str(e)}


def grpc_get_features(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/feature",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )
    warnings: list[str] = []
    snapshots: dict[str, Any] = {}
    for label, path in (
        (
            "features_guess",
            [device_id, "Sysdb", "feature", Wildcard()],
        ),
        (
            "openconfig_platform",
            [device_id, "Sysdb", "arista-openconfig", Wildcard()],
        ),
    ):
        _, data = _fetch(datadict, device_id, path, label)
        if data and not (len(data) == 1 and "_error" in data):
            snapshots[label] = data
    if not snapshots:
        warnings.append("no_feature_paths_returned_data")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/feature",
        coverage="partial",
        obj={"device_id": device_id, "snapshots": snapshots},
        warnings=warnings,
    )


def grpc_get_evpn(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/evpn",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )
    warnings: list[str] = []
    _, data = _fetch(
        datadict,
        device_id,
        [device_id, "Sysdb", "evpn", Wildcard()],
        "evpn",
    )
    if not data or (isinstance(data, dict) and set(data.keys()) <= {"_error"}):
        warnings.append("no_evpn_data")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/evpn",
        coverage="partial",
        obj={"device_id": device_id, "evpn": data},
        warnings=warnings,
    )


def grpc_get_vxlan(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/vxlan",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )
    warnings: list[str] = []
    paths = (
        [device_id, "Sysdb", "vxlan", Wildcard()],
        [device_id, "Sysdb", "interface", "config", "vxlan", Wildcard()],
    )
    merged: dict[str, Any] = {}
    for path in paths:
        _, data = _fetch(datadict, device_id, list(path), "vxlan")
        key = "/".join(str(p) for p in path)
        if data and "_error" not in data:
            merged[key] = data
    if not merged:
        warnings.append("no_vxlan_data")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/vxlan",
        coverage="partial",
        obj={"device_id": device_id, "vxlan": merged},
        warnings=warnings,
    )


def grpc_get_system_health(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/sys+environment",
            coverage="none",
            obj={},
            warnings=["missing_device_id"],
        )
    warnings: list[str] = []
    parts: dict[str, Any] = {}
    for label, path in (
        ("version", [device_id, "Sysdb", "sys", "version"]),
        ("status", [device_id, "Sysdb", "sys", "status"]),
        ("environment", [device_id, "Sysdb", "environment", Wildcard()]),
        ("platform", [device_id, "Sysdb", "platform", Wildcard()]),
    ):
        _, data = _fetch(datadict, device_id, path, label)
        parts[label] = data
    if all(
        isinstance(v, dict) and v.get("_error") and len(v) == 1 for v in parts.values()
    ):
        warnings.append("no_system_health_data")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/sys+environment",
        coverage="partial",
        obj={"device_id": device_id, "system": parts},
        warnings=warnings,
    )
