"""LLDP-based network topology discovery and multi-format export."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import grpc
import yaml

from cvp_mcp.env import cvp_credentials_missing_reasons
from cvp_mcp.grpc.interfaces import grpc_list_oper_up_physical_ports_for_lldp
from cvp_mcp.grpc.inventory import grpc_all_inventory
from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors
from cvp_mcp.grpc.utils import createConnection

_VALID_OUTPUT = frozenset({"json", "mermaid", "table", "containerlab"})

NETWORK_MAP_DATA_SOURCE = "inventory+connector:lldp_topology_scan"

# Placeholder images — edit before ``containerlab deploy`` if versions matter.
_DEFAULT_CEOS_IMAGE = "ceos:4.32.0F"
_DEFAULT_LINUX_IMAGE = "alpine:latest"
_EXTRA_REMOTE_INDEX_SCAN_LIMIT = 8


def infer_ethernet_port_count(model: str) -> int:
    """
    Best-effort port count from CVP ``model`` string for LLDP port sweep.

    Unknown models default to 48 to avoid missing ports on common switches.
    """
    m = (model or "").upper()
    if re.search(r"720XP-48|48TXH|CCS-720XP-48", m):
        return 48
    if re.search(r"720XP-24|-24ZY4|CCS-720XP-24", m):
        return 24
    if re.search(r"710P|710-P|16P", m):
        return 16
    if re.search(r"AWE-5310|5310", m):
        return 32
    if "7050" in m or "7160" in m or "7280" in m:
        return 52
    if "O-235" in m or "OUTDOOR" in m:
        return 8
    return 48


def _norm_mac(mac: str) -> str:
    s = (mac or "").strip().lower().replace("-", ":")
    if len(s) == 12 and ":" not in s:
        return ":".join(s[i : i + 2] for i in range(0, 12, 2))
    return s


def _collect_inventory(
    datadict: dict[str, Any], *, include_inactive: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    devices: list[dict[str, Any]] = []
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
        active, inactive = grpc_all_inventory(channel, exclude_access_points=True)
    devices.extend(active)
    if include_inactive:
        devices.extend(inactive)
    if not devices:
        warnings.append("no_inventory_devices_after_filters")
    return devices, warnings


def _device_port_cap(
    dev: dict[str, Any],
    max_ethernet_ports: int | None,
) -> int:
    n = infer_ethernet_port_count(str(dev.get("model") or ""))
    if max_ethernet_ports is not None:
        n = min(n, max(1, max_ethernet_ports))
    return n


def _filter_simple_ethernet_ports_by_cap(ports: list[str], cap: int) -> list[str]:
    """Drop ``Ethernet<n>`` when *n* exceeds the model cap; keep other names (e.g. Management)."""
    out: list[str] = []
    for p in ports:
        m = re.match(r"^Ethernet(\d+)$", p.strip(), re.I)
        if m and int(m.group(1)) > cap:
            continue
        out.append(p)
    return out


def _remote_row_signature(row: dict[str, Any]) -> str:
    """Stable key to dedupe multiple LLDP indices on the same port."""
    mac = _norm_mac(row.get("eth_addr") or row.get("remote_chassis_id") or "")
    if mac:
        return f"mac:{mac}"
    name = (row.get("system_name") or "").strip().lower()
    rport = (row.get("remote_port_id") or "").strip()
    mgmt = (row.get("management_address") or "").strip().lower()
    if mgmt:
        return f"name:{name}|rp:{rport}|mgmt:{mgmt}"
    return f"name:{name}|rp:{rport}|k:{row.get('neighbor_key') or ''}"


def _remote_device_id(edge: dict[str, Any]) -> str:
    """Stable identifier for the remote end of a directed edge."""
    mac = _norm_mac(edge.get("remote_eth_addr") or "")
    if mac:
        return f"mac:{mac}"
    ch = _norm_mac(edge.get("remote_chassis_id") or "")
    if ch:
        return f"mac:{ch}"
    name = (edge.get("remote_system_name") or "").strip()
    if name:
        return f"name:{name.lower()}"
    return "remote:unknown"


def _canonical_link_key(
    edge: dict[str, Any], serial_to_mac: dict[str, str]
) -> tuple[frozenset[str], tuple[str, str]]:
    """
    Stable key for a physical link pair.

    Two directed edges (A→B and B→A) on the same ports produce the same key
    regardless of which direction is examined first.
    """
    local_serial = (edge.get("local_serial") or "").strip()
    # Normalize local device ID to MAC namespace if we have it
    local_mac = serial_to_mac.get(local_serial, "")
    local_id = f"mac:{local_mac}" if local_mac else f"serial:{local_serial}"
    remote_id = _remote_device_id(edge)
    serials = frozenset({local_id, remote_id})
    ports = tuple(
        sorted([edge.get("local_port") or "", edge.get("remote_port_id") or ""])
    )
    return (serials, ports)


_SIDE_KEYS = (
    "serial",
    "hostname",
    "model",
    "port",
    "remote_system_name",
    "remote_chassis_id",
    "remote_eth_addr",
    "remote_port_id",
    "remote_management_address",
    "remote_management_addresses",
    "remote_system_description",
    "remote_system_capabilities",
    "remote_enabled_system_capabilities",
    "remote_pvid",
    "remote_vlans",
    "remote_lldp_med",
    "neighbor_source",
)


def _directed_edge_to_side(edge: dict[str, Any]) -> dict[str, Any]:
    """Extract local-side fields from a directed edge into a side dict."""
    return {
        "serial": edge.get("local_serial") or "",
        "hostname": edge.get("local_hostname") or "",
        "model": edge.get("local_model") or "",
        "port": edge.get("local_port") or "",
        "remote_system_name": edge.get("remote_system_name") or "",
        "remote_chassis_id": edge.get("remote_chassis_id") or "",
        "remote_eth_addr": edge.get("remote_eth_addr") or "",
        "remote_port_id": edge.get("remote_port_id") or "",
        "remote_management_address": edge.get("remote_management_address") or "",
        "remote_management_addresses": edge.get("remote_management_addresses") or [],
        "remote_system_description": edge.get("remote_system_description") or "",
        "remote_system_capabilities": edge.get("remote_system_capabilities") or [],
        "remote_enabled_system_capabilities": edge.get(
            "remote_enabled_system_capabilities"
        )
        or [],
        "remote_pvid": edge.get("remote_pvid") or "",
        "remote_vlans": edge.get("remote_vlans") or [],
        "remote_lldp_med": edge.get("remote_lldp_med") or {},
        "neighbor_source": edge.get("neighbor_source") or "",
    }


def _check_edge_mismatches(side_a: dict[str, Any], side_b: dict[str, Any]) -> list[str]:
    """Detect PVID, VLAN, and port cross-reference mismatches between two sides."""
    mismatches: list[str] = []
    a_pvid = (side_a.get("remote_pvid") or "").strip()
    b_pvid = (side_b.get("remote_pvid") or "").strip()
    if a_pvid and b_pvid and a_pvid != b_pvid:
        mismatches.append(f"pvid_mismatch: side_a={a_pvid} side_b={b_pvid}")
    a_vlans = set(side_a.get("remote_vlans") or [])
    b_vlans = set(side_b.get("remote_vlans") or [])
    if a_vlans and b_vlans and a_vlans != b_vlans:
        only_a = a_vlans - b_vlans
        only_b = b_vlans - a_vlans
        mismatches.append(
            f"vlan_mismatch: only_a={sorted(only_a)} only_b={sorted(only_b)}"
        )
    a_port_b = (side_a.get("remote_port_id") or "").strip()
    b_port = (side_b.get("port") or "").strip()
    b_port_a = (side_b.get("remote_port_id") or "").strip()
    a_port = (side_a.get("port") or "").strip()
    if a_port_b and b_port and a_port_b != b_port:
        mismatches.append(
            f"port_crossref_mismatch: side_a_sees_remote={a_port_b} side_b_local={b_port}"
        )
    if b_port_a and a_port and b_port_a != a_port:
        mismatches.append(
            f"port_crossref_mismatch: side_b_sees_remote={b_port_a} side_a_local={a_port}"
        )
    return mismatches


def _merge_bidirectional_edges(
    edges: list[dict[str, Any]], serial_to_mac: dict[str, str]
) -> list[dict[str, Any]]:
    """Merge directed edges into undirected links, grouping by canonical key."""
    groups: dict[tuple[frozenset[str], tuple[str, str]], list[dict[str, Any]]] = {}
    for e in edges:
        key = _canonical_link_key(e, serial_to_mac)
        groups.setdefault(key, []).append(e)
    merged: list[dict[str, Any]] = []
    for _key, group in groups.items():
        if len(group) == 1:
            side_a = _directed_edge_to_side(group[0])
            merged.append(
                {
                    "side_a": side_a,
                    "side_b": None,
                    "mismatches": ["unidirectional_lldp"],
                    "link_verified": False,
                    "link_direction": "undirected",
                }
            )
            continue
        # Two directions: assign side_a to the lower serial for stability.
        s0 = _directed_edge_to_side(group[0])
        s1 = _directed_edge_to_side(group[1])
        if (s0.get("serial") or "") <= (s1.get("serial") or ""):
            side_a, side_b = s0, s1
        else:
            side_a, side_b = s1, s0
        mismatches = _check_edge_mismatches(side_a, side_b)
        merged.append(
            {
                "side_a": side_a,
                "side_b": side_b,
                "mismatches": mismatches,
                "link_verified": True,
                "link_direction": "undirected",
            }
        )
    merged.sort(
        key=lambda e: (e["side_a"].get("serial") or "", e["side_a"].get("port") or "")
    )
    return merged


def _lldp_row_to_edges(
    local: dict[str, Any],
    port_name: str,
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    """One LLDP row -> one topology edge dict."""
    sysname = (row.get("system_name") or "").strip()
    chassis = (row.get("remote_chassis_id") or "").strip()
    eth = (row.get("eth_addr") or "").strip()
    rport = (row.get("remote_port_id") or "").strip()
    rmgmt = (row.get("management_address") or "").strip()
    if not sysname and not chassis and not eth:
        return []
    return [
        {
            "local_serial": local.get("serial_number") or "",
            "local_hostname": local.get("hostname") or "",
            "local_model": local.get("model") or "",
            "local_port": port_name,
            "remote_system_name": sysname,
            "remote_chassis_id": chassis,
            "remote_eth_addr": eth,
            "remote_port_id": rport,
            "remote_management_address": rmgmt,
            "remote_management_addresses": row.get("management_addresses") or [],
            "remote_system_description": row.get("system_description") or "",
            "remote_system_capabilities": row.get("system_capabilities") or [],
            "remote_enabled_system_capabilities": row.get("enabled_system_capabilities")
            or [],
            "remote_pvid": row.get("pvid") or "",
            "remote_vlans": row.get("vlans") or [],
            "remote_lldp_med": row.get("lldp_med") or {},
            "neighbor_source": row.get("neighbor_source") or "",
        }
    ]


def _append_rows_from_lldp_response(
    local: dict[str, Any],
    port_name: str,
    out: dict[str, Any],
    *,
    edges: list[dict[str, Any]],
    seen_remote: set[str],
    stats: dict[str, Any],
) -> None:
    """Parse ``items`` from ``grpc_get_lldp_neighbors`` and append new edges."""
    items = out.get("items") if isinstance(out, dict) else None
    if not items:
        return
    for row in items:
        if not isinstance(row, dict):
            continue
        sig = _remote_row_signature(row)
        if sig in seen_remote:
            continue
        for e in _lldp_row_to_edges(local, port_name, row):
            seen_remote.add(sig)
            edges.append(e)
            stats["edges_found"] += 1


def scan_lldp_topology_edges(
    datadict: dict[str, Any],
    *,
    include_inactive_devices: bool = False,
    max_ethernet_ports: int | None = None,
    device_serial_allowlist: list[str] | None = None,
    lldp_port_source: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Walk inventory devices and probe LLDP on candidate ports per device.

    ``lldp_port_source``:
    - ``auto``: use Sysdb interface oper-up physical ports (Ethernet*, Management*)
      when Connector returns data; fallback to ``Ethernet1..N`` otherwise.
    - ``oper_up_only``: use only oper-up physical ports; skip device when list is empty.
    - ``full_range``: always use model-based ``Ethernet1..N`` sweep (legacy behavior).

    Returns ``(edges, stats)`` where each edge is a flat dict suitable for export.
    """
    mode = (lldp_port_source or "auto").strip().lower()
    if mode not in ("auto", "oper_up_only", "full_range"):
        mode = "auto"

    cred_miss = cvp_credentials_missing_reasons(datadict)
    if cred_miss:
        return [], {
            "devices_scanned": 0,
            "port_probes": 0,
            "edges_found": 0,
            "extra_neighbor_index_probes": 0,
            "devices_port_source_oper_up": 0,
            "devices_port_source_ethernet_range": 0,
            "devices_skipped_no_oper_up_ports": 0,
            "unidirectional_links": 0,
            "mismatched_links": 0,
            "lldp_port_source": mode,
            "inventory_warnings": cred_miss,
            "credential_error": True,
        }

    inventory, inv_warnings = _collect_inventory(
        datadict, include_inactive=include_inactive_devices
    )
    allow = None
    if device_serial_allowlist:
        allow = {x.strip() for x in device_serial_allowlist if x.strip()}

    edges: list[dict[str, Any]] = []
    stats = {
        "devices_scanned": 0,
        "port_probes": 0,
        "edges_found": 0,
        "extra_neighbor_index_probes": 0,
        "devices_port_source_oper_up": 0,
        "devices_port_source_ethernet_range": 0,
        "devices_skipped_no_oper_up_ports": 0,
        "unidirectional_links": 0,
        "mismatched_links": 0,
        "lldp_port_source": mode,
        "inventory_warnings": inv_warnings,
    }

    for dev in inventory:
        serial = (dev.get("serial_number") or "").strip()
        if not serial:
            continue
        if allow is not None and serial not in allow:
            continue
        # Third-party / non-EOS may have no LLDP via Connector; still try.
        cap = _device_port_cap(dev, max_ethernet_ports)
        stats["devices_scanned"] += 1

        if mode == "full_range":
            port_iter: list[str] = [f"Ethernet{i}" for i in range(1, cap + 1)]
            stats["devices_port_source_ethernet_range"] += 1
            logging.info(
                "topology LLDP scan: %s (%s) full_range Ethernet1..%s",
                serial,
                dev.get("hostname") or "",
                cap,
            )
        else:
            raw_ports, _pw = grpc_list_oper_up_physical_ports_for_lldp(datadict, serial)
            filtered = _filter_simple_ethernet_ports_by_cap(raw_ports, cap)
            if filtered:
                port_iter = filtered
                stats["devices_port_source_oper_up"] += 1
                logging.info(
                    "topology LLDP scan: %s (%s) oper_up ports (%s): %s",
                    serial,
                    dev.get("hostname") or "",
                    len(port_iter),
                    ", ".join(port_iter[:12]) + ("…" if len(port_iter) > 12 else ""),
                )
            else:
                if mode == "oper_up_only":
                    stats["devices_skipped_no_oper_up_ports"] += 1
                    logging.info(
                        "topology LLDP scan: %s (%s) skipped (oper_up_only with no oper-up list)",
                        serial,
                        dev.get("hostname") or "",
                    )
                    continue
                port_iter = [f"Ethernet{i}" for i in range(1, cap + 1)]
                stats["devices_port_source_ethernet_range"] += 1
                logging.info(
                    "topology LLDP scan: %s (%s) fallback Ethernet1..%s (no oper-up list)",
                    serial,
                    dev.get("hostname") or "",
                    cap,
                )

        for port_name in port_iter:
            stats["port_probes"] += 1
            seen_remote: set[str] = set()
            try:
                out = grpc_get_lldp_neighbors(
                    datadict,
                    serial,
                    port_name=port_name,
                    remote_neighbor_key="",
                )
            except Exception as e:
                logging.warning(
                    "topology scan LLDP failed %s %s: %s", serial, port_name, e
                )
                continue
            _append_rows_from_lldp_response(
                dev, port_name, out, edges=edges, seen_remote=seen_remote, stats=stats
            )
            # Additional ``remoteSystem/<n>`` indices for ports with LLDP data.
            if isinstance(out, dict) and out.get("items"):
                for idx in range(2, _EXTRA_REMOTE_INDEX_SCAN_LIMIT + 1):
                    stats["extra_neighbor_index_probes"] += 1
                    try:
                        out_n = grpc_get_lldp_neighbors(
                            datadict,
                            serial,
                            port_name=port_name,
                            remote_neighbor_key=str(idx),
                        )
                    except Exception as e:
                        logging.debug(
                            "topology scan LLDP idx %s %s %s: %s",
                            serial,
                            port_name,
                            idx,
                            e,
                        )
                        continue
                    _append_rows_from_lldp_response(
                        dev,
                        port_name,
                        out_n,
                        edges=edges,
                        seen_remote=seen_remote,
                        stats=stats,
                    )

    serial_to_mac: dict[str, str] = {}
    for dev in inventory:
        sn = (dev.get("serial_number") or "").strip()
        sm = _norm_mac(str(dev.get("system_mac") or ""))
        if sn and sm:
            serial_to_mac[sn] = sm

    merged = _merge_bidirectional_edges(edges, serial_to_mac)
    stats["edges_found"] = len(merged)
    stats["unidirectional_links"] = sum(1 for e in merged if not e.get("link_verified"))
    stats["mismatched_links"] = sum(
        1
        for e in merged
        if e.get("mismatches")
        and any(m != "unidirectional_lldp" for m in e["mismatches"])
    )
    return merged, stats


def _node_key_for_local(dev: dict[str, Any]) -> str:
    return (dev.get("serial_number") or dev.get("hostname") or "unknown").strip()


def _node_key_for_remote(side: dict[str, Any]) -> str:
    """Derive a node key from a side dict's remote fields."""
    mac = _norm_mac(side.get("remote_eth_addr") or "")
    if mac:
        return f"mac:{mac}"
    ch = _norm_mac(side.get("remote_chassis_id") or "")
    if ch:
        return f"chassis:{ch}"
    name = (side.get("remote_system_name") or "").strip()
    if name:
        return f"name:{name.lower()}"
    return "remote:unknown"


def _side_remote_to_edge_remote(side: dict[str, Any]) -> dict[str, Any]:
    """Convert a side dict's remote fields to the flat edge format for _match_remote_to_inventory."""
    return {
        "remote_eth_addr": side.get("remote_eth_addr") or "",
        "remote_chassis_id": side.get("remote_chassis_id") or "",
    }


def _match_remote_to_inventory(
    side: dict[str, Any], by_mac: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Try to match a side dict's remote fields to inventory by MAC."""
    for key in ("remote_eth_addr", "remote_chassis_id"):
        mac = _norm_mac(side.get(key) or "")
        if mac and mac in by_mac:
            return by_mac[mac]
    return None


def build_topology_nodes_and_links(
    edges: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    *,
    node_scope: str = "full_inventory",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Build abstract ``nodes`` and ``links`` for JSON / Containerlab / Mermaid.

    Expects merged edges with ``side_a`` / ``side_b`` structure.

    ``node_scope``: ``full_inventory`` preloads every CVP inventory row (orphan switches
    appear with no links). ``connected`` keeps only devices that participate in at least
    one edge — clearer diagrams when the scan is partial or filtered.
    """
    by_mac: dict[str, dict[str, Any]] = {}
    for d in inventory:
        sm = _norm_mac(str(d.get("system_mac") or ""))
        if sm:
            by_mac[sm] = d

    node_map: dict[str, dict[str, Any]] = {}
    if node_scope == "full_inventory":
        for d in inventory:
            k = _node_key_for_local(d)
            node_map[k] = {
                "id": k,
                "label": d.get("hostname") or k,
                "kind": "inventory",
                "device_type": d.get("device_type") or "",
                "model": d.get("model") or "",
                "serial_number": d.get("serial_number") or "",
                "system_mac": d.get("system_mac") or "",
            }

    links: list[dict[str, Any]] = []

    for e in edges:
        side_a = e.get("side_a") or {}
        side_b = e.get("side_b")

        # Ensure side_a's device is in the node map
        sa_serial = (side_a.get("serial") or "").strip()
        if sa_serial and sa_serial not in node_map:
            node_map[sa_serial] = {
                "id": sa_serial,
                "label": side_a.get("hostname") or sa_serial,
                "kind": "local_only",
                "device_type": "",
                "model": side_a.get("model") or "",
                "serial_number": sa_serial,
                "system_mac": "",
            }

        # Resolve the target (side_b side or remote from side_a)
        if side_b:
            # Bidirectional: try matching side_b's remote data to inventory,
            # also try side_a's remote data
            matched = _match_remote_to_inventory(side_b, by_mac)
            if not matched:
                matched = _match_remote_to_inventory(side_a, by_mac)
            if matched:
                target_id = _node_key_for_local(matched)
                if target_id not in node_map:
                    node_map[target_id] = {
                        "id": target_id,
                        "label": matched.get("hostname") or target_id,
                        "kind": "inventory",
                        "device_type": matched.get("device_type") or "",
                        "model": matched.get("model") or "",
                        "serial_number": matched.get("serial_number") or "",
                        "system_mac": matched.get("system_mac") or "",
                    }
            else:
                target_id = _node_key_for_remote(side_a)
                if target_id not in node_map:
                    label = (
                        side_b.get("hostname") or side_a.get("remote_system_name") or ""
                    ).strip() or target_id
                    node_map[target_id] = {
                        "id": target_id,
                        "label": label,
                        "kind": "lldp_external",
                        "device_type": "",
                        "model": side_b.get("model") or "",
                        "serial_number": side_b.get("serial") or "",
                        "system_mac": _norm_mac(side_a.get("remote_eth_addr") or ""),
                    }

            remote_port_id = side_b.get("port") or side_a.get("remote_port_id") or ""
        else:
            # Unidirectional: resolve remote from side_a's remote fields
            matched = _match_remote_to_inventory(side_a, by_mac)
            if matched:
                target_id = _node_key_for_local(matched)
                if target_id not in node_map:
                    node_map[target_id] = {
                        "id": target_id,
                        "label": matched.get("hostname") or target_id,
                        "kind": "inventory",
                        "device_type": matched.get("device_type") or "",
                        "model": matched.get("model") or "",
                        "serial_number": matched.get("serial_number") or "",
                        "system_mac": matched.get("system_mac") or "",
                    }
            else:
                target_id = _node_key_for_remote(side_a)
                if target_id not in node_map:
                    label = (
                        side_a.get("remote_system_name") or ""
                    ).strip() or target_id
                    node_map[target_id] = {
                        "id": target_id,
                        "label": label,
                        "kind": "lldp_external",
                        "device_type": "",
                        "model": "",
                        "serial_number": "",
                        "system_mac": _norm_mac(side_a.get("remote_eth_addr") or ""),
                    }

            remote_port_id = side_a.get("remote_port_id") or ""

        links.append(
            {
                "source_id": sa_serial,
                "target_id": target_id,
                "local_port": side_a.get("port") or "",
                "remote_port_id": remote_port_id,
                "remote_management_address": side_a.get("remote_management_address")
                or "",
                "remote_management_addresses": side_a.get("remote_management_addresses")
                or [],
                "remote_system_description": side_a.get("remote_system_description")
                or "",
                "remote_system_capabilities": side_a.get("remote_system_capabilities")
                or [],
                "remote_enabled_system_capabilities": side_a.get(
                    "remote_enabled_system_capabilities"
                )
                or [],
                "remote_pvid": side_a.get("remote_pvid") or "",
                "remote_vlans": side_a.get("remote_vlans") or [],
                "remote_lldp_med": side_a.get("remote_lldp_med") or {},
                "remote_system_name": side_a.get("remote_system_name") or "",
                "matched_inventory": matched is not None,
                "link_verified": e.get("link_verified", False),
                "mismatches": e.get("mismatches") or [],
            }
        )

    deduped: list[dict[str, Any]] = []
    seen_lk: set[tuple[str, str, str]] = set()
    for lk in links:
        key = (
            str(lk.get("source_id")),
            str(lk.get("target_id")),
            str(lk.get("local_port") or ""),
        )
        if key in seen_lk:
            continue
        seen_lk.add(key)
        deduped.append(lk)

    nodes = sorted(node_map.values(), key=lambda x: str(x.get("label") or x["id"]))
    return nodes, deduped


def format_topology_mermaid(
    nodes: list[dict[str, Any]], links: list[dict[str, Any]]
) -> str:
    """Undirected edges for verified bidirectional links, dotted for unidirectional."""
    lines = ["flowchart LR"]
    nid: dict[str, str] = {}
    for i, n in enumerate(nodes):
        slug = f"n{i}"
        nid[str(n["id"])] = slug
        lab = str(n.get("label") or n["id"]).replace('"', "'")[:80]
        lines.append(f'  {slug}["{lab}"]')

    for j, lk in enumerate(links):
        a = nid.get(str(lk.get("source_id")))
        b = nid.get(str(lk.get("target_id")))
        if not a or not b:
            continue
        local_port = str(lk.get("local_port") or "").replace('"', "'")
        remote_port = str(lk.get("remote_port_id") or "").replace('"', "'")
        verified = lk.get("link_verified", False)
        if verified and local_port and remote_port:
            label = f"{local_port} / {remote_port}"
            lines.append(f"  {a} ---|{label}| {b}")
        elif verified:
            port = local_port or remote_port
            if port:
                lines.append(f"  {a} ---|{port}| {b}")
            else:
                lines.append(f"  {a} --- {b}")
        else:
            if local_port:
                lines.append(f"  {a} -.->|{local_port}| {b}")
            else:
                lines.append(f"  {a} -.-> {b}")
    return "\n".join(lines) + "\n"


def format_topology_table(edges: list[dict[str, Any]]) -> str:
    """GitHub-flavored markdown table from merged edges with side_a/side_b structure."""
    rows = [
        "| Host A | Port A | Host B | Port B | MAC | Mismatch |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for e in edges:
        side_a = e.get("side_a") or {}
        side_b = e.get("side_b")
        host_a = side_a.get("hostname") or side_a.get("serial") or ""
        port_a = side_a.get("port") or ""
        if side_b:
            host_b = side_b.get("hostname") or side_a.get("remote_system_name") or ""
            port_b = side_b.get("port") or ""
        else:
            host_b = side_a.get("remote_system_name") or ""
            port_b = side_a.get("remote_port_id") or ""
        mac = side_a.get("remote_eth_addr") or side_a.get("remote_chassis_id") or ""
        mismatch_str = ", ".join(e.get("mismatches") or [])
        rows.append(
            "| "
            + " | ".join(
                [
                    str(host_a),
                    str(port_a),
                    str(host_b),
                    str(port_b),
                    str(mac),
                    mismatch_str,
                ]
            )
            + " |"
        )
    return "\n".join(rows) + "\n"


def _sanitize_clab_node_name(hostname: str, used: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9-]+", "-", (hostname or "node").lower()).strip("-")
    if not base:
        base = "node"
    name = base[:40]
    if name not in used:
        used.add(name)
        return name
    i = 2
    while f"{name}-{i}" in used:
        i += 1
    out = f"{name}-{i}"
    used.add(out)
    return out


def _clab_kind_for_node(n: dict[str, Any]) -> str:
    dt = (n.get("device_type") or "").lower()
    if "eos" in dt or "virtual" in dt:
        return "arista_ceos"
    if n.get("kind") == "lldp_external":
        return "linux"
    if "third" in dt:
        return "linux"
    return "linux"


def _eth_to_clab_if(iface: str) -> str:
    """Map ``Ethernet5`` -> ``eth5`` for common containerlab examples."""
    raw = (iface or "").strip()
    m = re.match(r"^[Ee]thernet\s*(\d+)$", raw)
    if m:
        return f"eth{int(m.group(1))}"
    if re.match(r"^[Mm]anagement", raw):
        return "eth1"
    if not raw:
        return "eth1"
    return re.sub(r"[^a-zA-Z0-9/._-]+", "-", raw.lower())[:32] or "eth1"


def format_topology_containerlab(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    topology_name: str,
    ceos_image: str = _DEFAULT_CEOS_IMAGE,
    linux_image: str = _DEFAULT_LINUX_IMAGE,
) -> str:
    """Emit a ``.clab.yml``-style document (YAML string)."""
    used: set[str] = set()
    id_to_clab: dict[str, str] = {}
    topo_nodes: dict[str, Any] = {}

    for n in nodes:
        lab = str(n.get("label") or n["id"])
        cname = _sanitize_clab_node_name(lab, used)
        id_to_clab[str(n["id"])] = cname
        kind = _clab_kind_for_node(n)
        img = ceos_image if kind == "arista_ceos" else linux_image
        topo_nodes[cname] = {"kind": kind, "image": img}

    clab_links: list[dict[str, Any]] = []
    for lk in links:
        sa = id_to_clab.get(str(lk.get("source_id")))
        ta = id_to_clab.get(str(lk.get("target_id")))
        if not sa or not ta:
            continue
        lp = _eth_to_clab_if(str(lk.get("local_port") or ""))
        rp = _eth_to_clab_if(str(lk.get("remote_port_id") or "eth1"))
        if not str(lk.get("remote_port_id") or "").strip():
            rp = "eth1"
        clab_links.append({"endpoints": [f"{sa}:{lp}", f"{ta}:{rp}"]})

    doc = {
        "name": topology_name[:60],
        "topology": {"nodes": topo_nodes, "links": clab_links},
    }
    return yaml.safe_dump(
        doc,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def grpc_map_network_topology(
    datadict: dict[str, Any],
    *,
    output_format: str = "json",
    include_inactive_devices: bool = False,
    max_ethernet_ports: int | None = None,
    device_serial_allowlist: str = "",
    topology_name: str = "cvp-lldp",
    topology_node_scope: str = "full_inventory",
    lldp_port_source: str = "auto",
) -> dict[str, Any]:
    """
    Discover LLDP adjacencies across CVP inventory and render the chosen format.

    ``device_serial_allowlist``: comma-separated serials to scan (empty = all).
    ``lldp_port_source``: ``auto`` (oper-up ports from Sysdb when available, else
    Ethernet sweep), ``oper_up_only`` (no fallback sweep), or ``full_range``
    (always ``Ethernet1..N``).

    Agent reliability guidance:
    - Prefer batched scans with ``device_serial_allowlist`` (about 1-5 serials per call).
    - Set ``max_ethernet_ports`` to a practical cap for your devices.
    - Merge results client-side across multiple calls for full-fabric mapping.
    """
    guidance: list[str] = []
    if not (device_serial_allowlist or "").strip():
        guidance.append(
            "best_practice: set device_serial_allowlist and scan in batches (1-5 devices per call)"
        )
    if max_ethernet_ports is None:
        guidance.append(
            "best_practice: set max_ethernet_ports to cap per-device sweep time"
        )
    if (lldp_port_source or "auto").strip().lower() == "auto":
        guidance.append(
            "note: lldp_port_source=auto may fallback to Ethernet1..N when oper-up ports are unavailable"
        )

    warnings: list[str] = []
    fmt = (output_format or "json").strip().lower()
    if fmt not in _VALID_OUTPUT:
        warnings.append(f"unknown_output_format:{output_format!r}; using json")
        fmt = "json"

    allow_list: list[str] | None = None
    if (device_serial_allowlist or "").strip():
        allow_list = [
            x.strip() for x in device_serial_allowlist.split(",") if x.strip()
        ]

    cred_miss = cvp_credentials_missing_reasons(datadict)
    if cred_miss:
        scope0 = (topology_node_scope or "full_inventory").strip().lower()
        if scope0 not in ("full_inventory", "connected"):
            scope0 = "full_inventory"
        empty_topology: dict[str, Any] = {
            "data_source": NETWORK_MAP_DATA_SOURCE,
            "topology_node_scope": scope0,
            "nodes": [],
            "links": [],
            "edges": [],
            "stats": {
                "devices_scanned": 0,
                "port_probes": 0,
                "edges_found": 0,
                "extra_neighbor_index_probes": 0,
                "devices_port_source_oper_up": 0,
                "devices_port_source_ethernet_range": 0,
                "devices_skipped_no_oper_up_ports": 0,
                "unidirectional_links": 0,
                "mismatched_links": 0,
                "lldp_port_source": (lldp_port_source or "auto").strip().lower(),
                "inventory_warnings": cred_miss,
                "credential_error": True,
            },
        }
        cred_warn = cred_miss + [
            "mcp_server_missing_cloudvision_credentials",
            "remote_mcp_clients_cannot_set_CVP_or_CVPTOKEN_configure_the_server",
        ]
        if fmt == "json":
            text_out = json.dumps(empty_topology, indent=2)
        elif fmt == "mermaid":
            text_out = (
                'flowchart LR\n  n0["(no data — missing CVP/CVPTOKEN on MCP server)"]\n'
            )
        elif fmt == "table":
            text_out = (
                "| Error |\n| --- |\n"
                "| Missing CVP/CVPTOKEN on MCP server — see README. |\n"
            )
        else:
            text_out = yaml.safe_dump(
                {
                    "name": topology_name or "cvp-lldp",
                    "topology": {"nodes": {}, "links": []},
                },
                sort_keys=False,
                default_flow_style=False,
            )
        return {
            "output_format": fmt,
            "text": text_out,
            "topology": empty_topology,
            "warnings": warnings + cred_warn,
            "execution_guidance": guidance,
        }

    lps = (lldp_port_source or "auto").strip().lower()
    if lps not in ("auto", "oper_up_only", "full_range"):
        warnings.append(f"unknown_lldp_port_source:{lldp_port_source!r}; using auto")
        lps = "auto"

    edges, stats = scan_lldp_topology_edges(
        datadict,
        include_inactive_devices=include_inactive_devices,
        max_ethernet_ports=max_ethernet_ports,
        device_serial_allowlist=allow_list,
        lldp_port_source=lps,
    )

    inventory, _ = _collect_inventory(
        datadict, include_inactive=include_inactive_devices
    )
    scope = (topology_node_scope or "full_inventory").strip().lower()
    if scope not in ("full_inventory", "connected"):
        warnings.append(
            f"unknown_topology_node_scope:{topology_node_scope!r}; using full_inventory"
        )
        scope = "full_inventory"
    nodes, links = build_topology_nodes_and_links(edges, inventory, node_scope=scope)

    topology: dict[str, Any] = {
        "data_source": NETWORK_MAP_DATA_SOURCE,
        "topology_node_scope": scope,
        "nodes": nodes,
        "links": links,
        "edges": edges,
        "stats": stats,
    }

    text = ""
    if fmt == "json":
        text = json.dumps(topology, indent=2)
    elif fmt == "mermaid":
        text = format_topology_mermaid(nodes, links)
    elif fmt == "table":
        text = format_topology_table(edges)
    else:
        text = format_topology_containerlab(
            nodes, links, topology_name=topology_name or "cvp-lldp"
        )

    return {
        "output_format": fmt,
        "text": text,
        "topology": topology,
        "warnings": warnings,
        "execution_guidance": guidance,
    }
