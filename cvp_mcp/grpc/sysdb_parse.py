"""Helpers for EOS Sysdb/Smash structures returned via CloudVision Connector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def eos_name(obj: Any) -> str:
    """
    Extract display string from common EOS protobuf-style dicts: ``{"Name": "foo"}``.
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, Mapping):
        n = obj.get("Name") or obj.get("name")
        if isinstance(n, str):
            return n
        if isinstance(n, Mapping):
            v = n.get("value")
            if isinstance(v, str):
                return v
    return str(obj)


def merge_intfcfg_and_status(
    intf_cfg: dict[str, Any], intf_status: dict[str, Any]
) -> list[dict[str, Any]]:
    """Join interface config + status maps keyed by interface short name."""
    names = sorted(set(intf_cfg.keys()) | set(intf_status.keys()))
    rows: list[dict[str, Any]] = []
    for name in names:
        cfg = intf_cfg.get(name) if isinstance(intf_cfg.get(name), dict) else {}
        st = intf_status.get(name) if isinstance(intf_status.get(name), dict) else {}
        if not isinstance(cfg, dict):
            cfg = {}
        if not isinstance(st, dict):
            st = {}
        admin_raw = eos_name(cfg.get("adminEnabledStateLocal") or cfg.get("adminState"))
        enabled = admin_raw in (
            "",
            "enabled",
            "unknownEnabledState",
            "INTF_ADMIN_ENABLED",
        )
        oper = eos_name(st.get("operStatus") or st.get("operStatus"))
        speed = eos_name(st.get("speedEnum") or st.get("speed"))
        desc = ""
        if isinstance(cfg.get("description"), str):
            desc = cfg["description"]
        elif isinstance(cfg.get("description"), Mapping):
            desc = str(cfg["description"].get("value", ""))
        mtu = cfg.get("mtu")
        if isinstance(mtu, Mapping) and "value" in mtu:
            mtu = mtu.get("value")
        rows.append(
            {
                "interface_name": name,
                "enabled": enabled,
                "oper_status": oper or "",
                "speed": speed or "",
                "mtu": mtu if mtu is not None else None,
                "description": desc or "",
                "counters": _extract_counters(st),
            }
        )
    return rows


def _extract_counters(status: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "inOctets",
        "outOctets",
        "inErrors",
        "outErrors",
        "inDiscards",
        "outDiscards",
    ):
        if key in status:
            out[key] = status[key]
    return out


def parse_switchport_vlan_rows(switch_intf_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``Sysdb/bridging/switchIntfConfig/switchIntfConfig/*`` style map."""
    rows: list[dict[str, Any]] = []
    for intf_name, raw in switch_intf_cfg.items():
        if not isinstance(raw, dict):
            continue
        mode = eos_name(raw.get("switchportMode"))
        access = raw.get("accessVlan")
        if isinstance(access, Mapping) and "value" in access:
            access = access.get("value")
        trunk = raw.get("trunkAllowedVlans")
        rows.append(
            {
                "interface": str(intf_name),
                "switchport_mode": mode or "",
                "access_vlan": access,
                "trunk_allowed_vlans": trunk,
            }
        )
    return rows


def parse_vlan_database(vlan_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort parse of VLAN status/config maps keyed by VLAN id string."""
    rows: list[dict[str, Any]] = []
    for vid, raw in vlan_map.items():
        if not isinstance(raw, dict):
            continue
        name = ""
        if isinstance(raw.get("name"), str):
            name = raw["name"]
        status = eos_name(raw.get("status") or raw.get("state"))
        rows.append(
            {
                "vlan_id": vid,
                "name": name,
                "status": status or "",
                "raw_keys": sorted(raw.keys())[:40],
            }
        )
    rows.sort(key=lambda r: str(r["vlan_id"]))
    return rows


def flatten_nested_device_map(
    raw: dict[str, Any], max_depth: int = 6
) -> dict[str, Any]:
    """
    Connector sometimes returns one top-level key (wildcard expansion). Unwrap shallowly.
    """
    cur: Any = raw
    depth = 0
    while isinstance(cur, dict) and len(cur) == 1 and depth < max_depth:
        only = next(iter(cur.values()))
        if isinstance(only, dict) and not any(
            k in only for k in ("Name", "name", "value", "operStatus")
        ):
            cur = only
            depth += 1
            continue
        break
    return cur if isinstance(cur, dict) else {}
