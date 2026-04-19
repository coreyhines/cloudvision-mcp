"""Interface, VLAN, and L3 addressing via CloudVision Connector (device dataset)."""

from __future__ import annotations

import logging
import re
from typing import Any

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient

from cvp_mcp.env import normalize_api_token
from cvp_mcp.grpc.connector import get_device_path, serialize_cloudvision_data
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.sysdb_parse import (
    flatten_nested_device_map,
    merge_intfcfg_and_status,
    parse_switchport_vlan_rows,
    parse_vlan_database,
)


def _cvp_addr(datadict: dict[str, Any]) -> str:
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    return cvp


def _flatten_intf_map(raw: dict[str, Any]) -> dict[str, Any]:
    """Unwrap slice->interface->blob into interface->blob."""
    if not raw:
        return {}
    flat: dict[str, Any] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        if all(isinstance(x, dict) for x in v.values()):
            for intf_name, blob in v.items():
                if isinstance(blob, dict):
                    flat[str(intf_name)] = blob
        else:
            flat[str(k)] = v
    return flat


def _connector_device_config(
    datadict: dict[str, Any], device_id: str
) -> dict[str, Any]:
    token = normalize_api_token(datadict.get("cvtoken"))
    path_base = [
        device_id,
        "Sysdb",
        "interface",
        "config",
        "eth",
        "phy",
        "slice",
        Wildcard(),
        "intfConfig",
        Wildcard(),
    ]
    path_status = [
        device_id,
        "Sysdb",
        "interface",
        "status",
        "eth",
        "phy",
        "slice",
        Wildcard(),
        "intfStatus",
        Wildcard(),
    ]
    with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
        cfg_raw = serialize_cloudvision_data(
            get_device_path(client, device_id, path_base)
        )
        st_raw = serialize_cloudvision_data(
            get_device_path(client, device_id, path_status)
        )
    cfg_raw = flatten_nested_device_map(cfg_raw)
    st_raw = flatten_nested_device_map(st_raw)
    cfg_map = _flatten_intf_map(cfg_raw)
    st_map = _flatten_intf_map(st_raw)
    return {"intfConfig": cfg_map, "intfStatus": st_map}


def grpc_get_interfaces(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/interface",
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    try:
        blob = _connector_device_config(datadict, device_id)
        items = merge_intfcfg_and_status(blob["intfConfig"], blob["intfStatus"])
        items.sort(key=lambda r: r["interface_name"])
        cov = "full" if items else "partial"
        if not items:
            warnings.append("no_interface_data_returned")
        return tool_envelope(
            device_id=device_id,
            data_source="connector:device:Sysdb/interface",
            coverage=cov,
            items=items,
            warnings=warnings,
        )
    except Exception as e:
        logging.error("get_interfaces: %s", e)
        return tool_envelope(
            device_id=device_id,
            data_source="connector:device:Sysdb/interface",
            coverage="none",
            items=[],
            warnings=[str(e)],
        )


def grpc_get_vlans(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    """VLANs + switchport membership from Sysdb bridging paths."""
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/bridging",
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    token = normalize_api_token(datadict.get("cvtoken"))
    rows: list[dict[str, Any]] = []
    try:
        with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
            sw_path = [
                device_id,
                "Sysdb",
                "bridging",
                "switchIntfConfig",
                "switchIntfConfig",
                Wildcard(),
            ]
            sw_raw = flatten_nested_device_map(
                serialize_cloudvision_data(get_device_path(client, device_id, sw_path))
            )
            if not isinstance(sw_raw, dict):
                sw_raw = {}
            member_rows = parse_switchport_vlan_rows(sw_raw)
            for m in member_rows:
                m["device_id"] = device_id
            rows.extend(member_rows)

            for vlan_path in (
                [device_id, "Sysdb", "bridging", "status", "vlan", Wildcard()],
                [device_id, "Sysdb", "bridging", "vlan", Wildcard()],
            ):
                try:
                    vraw = flatten_nested_device_map(
                        serialize_cloudvision_data(
                            get_device_path(client, device_id, vlan_path)
                        )
                    )
                    if isinstance(vraw, dict) and vraw:
                        vrows = parse_vlan_database(vraw)
                        for v in vrows:
                            v["device_id"] = device_id
                            v["_path_hint"] = "/".join(str(p) for p in vlan_path)
                        rows.extend(vrows)
                        break
                except Exception as e:
                    logging.debug("vlan path %s: %s", vlan_path, e)
    except Exception as e:
        logging.error("get_vlans: %s", e)
        warnings.append(str(e))

    cov = "partial" if warnings or not rows else "full"
    if not rows:
        warnings.append("no_vlan_data_returned")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/bridging",
        coverage=cov,
        items=rows,
        warnings=warnings,
    )


def _parse_ip_addrs_from_map(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort extract interface -> addresses from nested Sysdb ip maps."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return out

    def walk(prefix: str, node: Any, depth: int) -> None:
        if depth > 12:
            return
        if not isinstance(node, dict):
            return
        if "address" in node or "prefix" in node or "ipAddr" in str(node.keys()):
            iface = prefix or "unknown"
            addr = node.get("address") or node.get("prefix") or node.get("ipAddr")
            primary = node.get("primary")
            origin = node.get("originType") or node.get("addrType")
            out.append(
                {
                    "interface": iface,
                    "address_family": "unknown",
                    "address": str(addr) if addr is not None else "",
                    "primary": bool(primary) if primary is not None else False,
                    "origin": str(origin) if origin else "",
                    "raw": {k: node[k] for k in list(node.keys())[:15]},
                }
            )
            return
        for k, v in node.items():
            if k in ("Name", "name", "value"):
                continue
            next_prefix = f"{prefix}/{k}" if prefix else str(k)
            walk(next_prefix, v, depth + 1)

    walk("", raw, 0)
    return out


def grpc_get_ip_interfaces(datadict: dict[str, Any], device_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source="connector:device:Sysdb/ip",
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    token = normalize_api_token(datadict.get("cvtoken"))
    items: list[dict[str, Any]] = []
    try:
        with GRPCClient(grpcAddr=_cvp_addr(datadict), tokenValue=token) as client:
            for ip_path in (
                [
                    device_id,
                    "Sysdb",
                    "ip",
                    "config",
                    "addr",
                    Wildcard(),
                ],
                [
                    device_id,
                    "Sysdb",
                    "ip",
                    "config",
                    "ifAddr",
                    Wildcard(),
                ],
            ):
                try:
                    raw = flatten_nested_device_map(
                        serialize_cloudvision_data(
                            get_device_path(client, device_id, ip_path)
                        )
                    )
                    if isinstance(raw, dict) and raw:
                        found = _parse_ip_addrs_from_map(raw)
                        for f in found:
                            f["device_id"] = device_id
                            f["_path_hint"] = "/".join(str(p) for p in ip_path)
                        items.extend(found)
                        break
                except Exception as e:
                    logging.debug("ip path %s: %s", ip_path, e)
    except Exception as e:
        logging.error("get_ip_interfaces: %s", e)
        warnings.append(str(e))

    cov = "partial" if warnings or not items else "full"
    if not items:
        warnings.append("no_ip_interface_data_returned")
    return tool_envelope(
        device_id=device_id,
        data_source="connector:device:Sysdb/ip",
        coverage=cov,
        items=items,
        warnings=warnings,
    )


def _is_physical_switch_port_name(name: str) -> bool:
    n = (name or "").strip()
    return n.startswith("Ethernet") or n.startswith("Management")


def _interface_row_reports_oper_up(row: dict[str, Any]) -> bool:
    """True when Sysdb oper status indicates the interface has link / is forwarding."""
    oper = (row.get("oper_status") or "").strip()
    if not oper:
        return False
    low = oper.lower()
    if any(
        x in low
        for x in (
            "intfoperdown",
            "lowerlayerdown",
            "notpresent",
        )
    ):
        return False
    if "operup" in low:
        return True
    if low in ("linkup", "connected", "up"):
        return True
    return False


def _natural_port_name_key(name: str) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", name)
    key: list[Any] = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.lower())
    return tuple(key)


def grpc_list_oper_up_physical_ports_for_lldp(
    datadict: dict[str, Any], device_id: str
) -> tuple[list[str], list[str]]:
    """
    Short names of physical ports (Ethernet*, Management*) with oper-up status.

    Used to avoid probing every ``Ethernet1..N`` for LLDP when Connector returns
    interface Sysdb. Returns ``([], warnings)`` on failure or when no rows qualify.
    """
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return [], ["missing_device_id"]
    try:
        blob = _connector_device_config(datadict, device_id)
        items = merge_intfcfg_and_status(blob["intfConfig"], blob["intfStatus"])
        out: list[str] = []
        for row in items:
            name = str(row.get("interface_name") or "")
            if not _is_physical_switch_port_name(name):
                continue
            if not _interface_row_reports_oper_up(row):
                continue
            out.append(name)
        out.sort(key=_natural_port_name_key)
        if not out:
            warnings.append("no_oper_up_physical_ports_from_interface_sysdb")
        return out, warnings
    except Exception as e:
        logging.warning("oper_up_ports_for_lldp %s: %s", device_id, e)
        return [], [f"interface_port_list_failed:{e}"]
