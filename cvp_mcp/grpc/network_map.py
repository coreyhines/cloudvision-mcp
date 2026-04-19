"""LLDP-based network topology discovery and multi-format export."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import grpc
import yaml

from cvp_mcp.grpc.inventory import grpc_all_inventory
from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors
from cvp_mcp.grpc.utils import createConnection

_VALID_OUTPUT = frozenset({"json", "mermaid", "table", "containerlab"})

NETWORK_MAP_DATA_SOURCE = "inventory+connector:lldp_topology_scan"

# Placeholder images — edit before ``containerlab deploy`` if versions matter.
_DEFAULT_CEOS_IMAGE = "ceos:4.32.0F"
_DEFAULT_LINUX_IMAGE = "alpine:latest"


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


def _remote_row_signature(row: dict[str, Any]) -> str:
    """Stable key to dedupe multiple LLDP indices on the same port."""
    mac = _norm_mac(row.get("eth_addr") or row.get("remote_chassis_id") or "")
    if mac:
        return f"mac:{mac}"
    name = (row.get("system_name") or "").strip().lower()
    rport = (row.get("remote_port_id") or "").strip()
    return f"name:{name}|rp:{rport}"


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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Walk inventory EOS (and other) devices and probe ``Ethernet1..N`` LLDP per device.

    Returns ``(edges, stats)`` where each edge is a flat dict suitable for export.
    """
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
        logging.info(
            "topology LLDP scan: %s (%s) ports 1..%s",
            serial,
            dev.get("hostname") or "",
            cap,
        )
        for i in range(1, cap + 1):
            port_name = f"Ethernet{i}"
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
            # Additional ``remoteSystem/<n>`` indices when the first snapshot lists only one row.
            if isinstance(out, dict) and out.get("items"):
                for idx in ("2", "3"):
                    stats["extra_neighbor_index_probes"] += 1
                    try:
                        out_n = grpc_get_lldp_neighbors(
                            datadict,
                            serial,
                            port_name=port_name,
                            remote_neighbor_key=idx,
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

    return edges, stats


def _node_key_for_local(dev: dict[str, Any]) -> str:
    return (dev.get("serial_number") or dev.get("hostname") or "unknown").strip()


def _node_key_for_remote(edge: dict[str, Any]) -> str:
    mac = _norm_mac(edge.get("remote_eth_addr") or "")
    if mac:
        return f"mac:{mac}"
    ch = _norm_mac(edge.get("remote_chassis_id") or "")
    if ch:
        return f"chassis:{ch}"
    name = (edge.get("remote_system_name") or "").strip()
    if name:
        return f"name:{name.lower()}"
    return "remote:unknown"


def _match_remote_to_inventory(
    edge: dict[str, Any], by_mac: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    for key in ("remote_eth_addr", "remote_chassis_id"):
        mac = _norm_mac(edge.get(key) or "")
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
        ls = (e.get("local_serial") or "").strip()
        if ls and ls not in node_map:
            node_map[ls] = {
                "id": ls,
                "label": e.get("local_hostname") or ls,
                "kind": "local_only",
                "device_type": "",
                "model": e.get("local_model") or "",
                "serial_number": ls,
                "system_mac": "",
            }

        matched = _match_remote_to_inventory(e, by_mac)
        if matched:
            rk = _node_key_for_local(matched)
            if rk not in node_map:
                node_map[rk] = {
                    "id": rk,
                    "label": matched.get("hostname") or rk,
                    "kind": "inventory",
                    "device_type": matched.get("device_type") or "",
                    "model": matched.get("model") or "",
                    "serial_number": matched.get("serial_number") or "",
                    "system_mac": matched.get("system_mac") or "",
                }
            remote_id = rk
        else:
            remote_id = _node_key_for_remote(e)
            if remote_id not in node_map:
                label = (e.get("remote_system_name") or "").strip() or remote_id
                node_map[remote_id] = {
                    "id": remote_id,
                    "label": label,
                    "kind": "lldp_external",
                    "device_type": "",
                    "model": "",
                    "serial_number": "",
                    "system_mac": _norm_mac(e.get("remote_eth_addr") or ""),
                }

        links.append(
            {
                "source_id": ls,
                "target_id": remote_id,
                "local_port": e.get("local_port") or "",
                "remote_port_id": e.get("remote_port_id") or "",
                "remote_system_name": e.get("remote_system_name") or "",
                "matched_inventory": matched is not None,
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
    """Directed edges with local port labels."""
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
        port = str(lk.get("local_port") or "").replace('"', "'")
        if port:
            lines.append(f"  {a} -->|{port}| {b}")
        else:
            lines.append(f"  {a} --> {b}")
    return "\n".join(lines) + "\n"


def format_topology_table(edges: list[dict[str, Any]]) -> str:
    """GitHub-flavored markdown table."""
    rows = [
        "| Local hostname | Local port | Remote system | Remote MAC | Remote port |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in edges:
        rows.append(
            "| "
            + " | ".join(
                [
                    str(e.get("local_hostname") or ""),
                    str(e.get("local_port") or ""),
                    str(e.get("remote_system_name") or ""),
                    str(e.get("remote_eth_addr") or e.get("remote_chassis_id") or ""),
                    str(e.get("remote_port_id") or ""),
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
) -> dict[str, Any]:
    """
    Discover LLDP adjacencies across CVP inventory and render the chosen format.

    ``device_serial_allowlist``: comma-separated serials to scan (empty = all).
    """
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

    edges, stats = scan_lldp_topology_edges(
        datadict,
        include_inactive_devices=include_inactive_devices,
        max_ethernet_ports=max_ethernet_ports,
        device_serial_allowlist=allow_list,
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
    }
