"""LLDP neighbor data via CloudVision Connector (device dataset, best-effort paths)."""

from __future__ import annotations

import logging
from typing import Any

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient

from cvp_mcp.env import normalize_api_token
from cvp_mcp.grpc.connector import get_device_path, serialize_cloudvision_data
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.sysdb_parse import eos_name, flatten_nested_device_map


def _cvp_addr(datadict: dict[str, Any]) -> str:
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def _get_path(
    datadict: dict[str, Any], device_id: str, path: list[Any]
) -> dict[str, Any]:
    token = normalize_api_token(datadict.get("cvtoken"))
    with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
        raw = get_device_path(client, device_id, path)
    raw = serialize_cloudvision_data(raw)
    return flatten_nested_device_map(raw) if isinstance(raw, dict) else {}


def _unwrap_lldp_container(flat: dict[str, Any]) -> dict[str, Any]:
    """If the snapshot is wrapped (neighborStatus / neighborTable / …), return inner map."""
    for key in ("neighborStatus", "neighborTable", "neighbors", "status"):
        inner = flat.get(key)
        if isinstance(inner, dict) and inner:
            return inner
    return flat


def _neighbor_row(
    local_interface: str, neighbor_key: str, nb: dict[str, Any]
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "local_interface": local_interface,
        "neighbor_key": neighbor_key,
    }
    if "systemName" in nb:
        row["system_name"] = eos_name(nb.get("systemName"))
    if "systemNameStr" in nb:
        row["system_name_str"] = eos_name(nb.get("systemNameStr"))
    if "portId" in nb:
        row["port_id"] = eos_name(nb.get("portId"))
    if "portIdStr" in nb:
        row["port_id_str"] = eos_name(nb.get("portIdStr"))
    if "chassisId" in nb:
        row["chassis_id"] = eos_name(nb.get("chassisId"))
    if "chassisIdStr" in nb:
        row["chassis_id_str"] = eos_name(nb.get("chassisIdStr"))
    if "managementAddress" in nb:
        row["management_address"] = nb["managementAddress"]
    if "mgmtAddr" in nb:
        row["mgmt_addr"] = nb["mgmtAddr"]
    return row


def parse_lldp_flat_to_items(flat: dict[str, Any]) -> list[dict[str, Any]]:
    """Map flattened LLDP Sysdb snapshot to stable rows for MCP clients."""
    items: list[dict[str, Any]] = []
    if not flat:
        return items
    work = _unwrap_lldp_container(flat)
    for local_intf, node in work.items():
        if not isinstance(node, dict):
            continue
        for nb_key, nb in node.items():
            if not isinstance(nb, dict):
                continue
            items.append(_neighbor_row(str(local_intf), str(nb_key), nb))
    items.sort(
        key=lambda r: (r.get("local_interface") or "", r.get("neighbor_key") or "")
    )
    return items


def grpc_get_lldp_neighbors(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/lldp",
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    candidate_paths: tuple[list[Any], ...] = (
        [device_id, "Sysdb", "lldp", "status", "neighborStatus", Wildcard()],
        [device_id, "Sysdb", "lldp", "status", "neighborTable", Wildcard()],
        [device_id, "Sysdb", "lldp", "status", Wildcard()],
        [device_id, "Sysdb", "lldp", Wildcard()],
        [device_id, "Sysdb", "lldp", "config", Wildcard()],
        [device_id, "Smash", "lldp", Wildcard()],
    )
    flat: dict[str, Any] = {}
    path_used = ""
    for p in candidate_paths:
        try:
            snap = _get_path(datadict, device_id, list(p))
            if snap:
                flat = snap
                path_used = "/".join(str(x) for x in p)
                break
        except Exception as e:
            logging.debug("lldp path %s: %s", p, e)
    items = parse_lldp_flat_to_items(flat)
    if flat and not items:
        warnings.append("lldp_data_unparsed")
    if not items and not flat:
        warnings.append("no_lldp_data_at_known_paths")
    cov = "full" if items else ("partial" if flat else "none")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/lldp",
        coverage=cov,
        items=items,
        obj={"path_hint": path_used, "raw_key_count": len(flat)},
        warnings=warnings,
    )
