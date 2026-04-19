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

# Primary tree: Sysdb/l2discovery/lldp/status/local/…/portStatus/<intf>/{remoteSystem,remoteSystemByMsap}/…
# Aeris shows fixed instance ids, e.g. …/local/1/portStatus/Ethernet6/remoteSystem/1/
LLDP_DATA_SOURCE = "connector:device:Sysdb/l2discovery/lldp"


def _lldp_l2discovery_literal_local_paths(device_id: str) -> tuple[list[Any], ...]:
    """
    Connector often matches Aeris when ``local`` is a literal instance (``1``, ``0``),
    not only ``Wildcard()`` under ``local``.
    """
    out: list[list[Any]] = []
    for lid in ("1", "0"):
        out.extend(
            (
                [
                    device_id,
                    "Sysdb",
                    "l2discovery",
                    "lldp",
                    "status",
                    "local",
                    lid,
                    "portStatus",
                    Wildcard(),
                ],
                [
                    device_id,
                    "Sysdb",
                    "l2discovery",
                    "lldp",
                    "status",
                    "local",
                    lid,
                    "portStatus",
                    Wildcard(),
                    "remoteSystem",
                    Wildcard(),
                ],
                [
                    device_id,
                    "Sysdb",
                    "l2discovery",
                    "lldp",
                    "status",
                    "local",
                    lid,
                    "portStatus",
                    Wildcard(),
                    "remoteSystemByMsap",
                    Wildcard(),
                ],
            )
        )
    return tuple(out)


def _lldp_telemetry_browser_leaf_paths(device_id: str) -> tuple[list[Any], ...]:
    """
    Telemetry Browser uses concrete ``portStatus/<intf>/…`` segments. Connector
    ``Get`` may return no keys for ``portStatus/*`` while literal interface names work
    (see Aeris path …/portStatus/Ethernet6/remoteSystem/1/).
    """
    out: list[list[Any]] = []
    # Ethernet6 first: common for lab uplinks; matches Telemetry Browser examples.
    for port in (
        "Ethernet6",
        "Ethernet1",
        "Ethernet2",
        "Ethernet3",
        "Ethernet4",
        "Ethernet5",
        "Ethernet7",
        "Ethernet8",
    ):
        for lid in ("1", "0"):
            base = [
                device_id,
                "Sysdb",
                "l2discovery",
                "lldp",
                "status",
                "local",
                lid,
                "portStatus",
                port,
            ]
            out.extend(
                (
                    base + ["remoteSystem", "1"],
                    base + ["remoteSystem", Wildcard()],
                    base + ["remoteSystemByMsap", Wildcard()],
                )
            )
    return tuple(out)


def _lldp_paths_for_port_name(
    device_id: str, port_name: str, remote_neighbor_key: str
) -> tuple[list[Any], ...]:
    """User-supplied port (and optional remote index) matching Telemetry Browser."""
    port_name = (port_name or "").strip()
    if not port_name:
        return ()
    rk = (remote_neighbor_key or "").strip()
    out: list[list[Any]] = []
    for lid in ("1", "0"):
        base = [
            device_id,
            "Sysdb",
            "l2discovery",
            "lldp",
            "status",
            "local",
            lid,
            "portStatus",
            port_name,
        ]
        out.extend(
            (
                base + ["remoteSystemByMsap", Wildcard()],
                base + ["remoteSystem", Wildcard()],
            )
        )
        if rk:
            out.append(base + ["remoteSystem", rk])
        else:
            out.append(base + ["remoteSystem", "1"])
    return tuple(out)


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


def _path_for_log(path: list[Any]) -> str:
    """Human-readable path for logs (wildcards shown as ``*``)."""
    return "/".join("*" if isinstance(x, Wildcard) else str(x) for x in path)


def _unwrap_lldp_container(flat: dict[str, Any]) -> dict[str, Any]:
    """If the snapshot is wrapped (neighborStatus / neighborTable / …), return inner map."""
    current = flat
    while isinstance(current, dict) and current:
        unwrapped = False
        for key in ("neighborStatus", "neighborTable", "neighbors", "status"):
            inner = current.get(key)
            if isinstance(inner, dict) and inner:
                if inner is current:
                    return current
                current = inner
                unwrapped = True
                break
        if not unwrapped:
            break
    return current


def _tlv_string(blob: Any) -> str:
    """String from EOS LLDP TLV blobs: plain str or { value: { value: "…" } } / valueStr."""
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    if isinstance(blob, dict):
        vs = blob.get("valueStr")
        if isinstance(vs, dict):
            inner = vs.get("value")
            if isinstance(inner, str):
                return inner
        val = blob.get("value")
        if isinstance(val, dict):
            inner = val.get("value")
            if isinstance(inner, str):
                return inner
            if inner is not None:
                return str(inner)
    return ""


def _msap_fields(nb: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    m = nb.get("msap")
    if not isinstance(m, dict):
        return out
    pid = m.get("portIdentifier")
    if isinstance(pid, dict) and pid.get("portId") is not None:
        out["remote_port_id"] = str(pid.get("portId"))
    cid = m.get("chassisIdentifier")
    if isinstance(cid, dict) and cid.get("chassisId") is not None:
        out["remote_chassis_id"] = str(cid.get("chassisId"))
    return out


def _l2_remote_row(
    local_interface: str,
    neighbor_key: str,
    nb: dict[str, Any],
    *,
    neighbor_source: str,
) -> dict[str, Any]:
    """One row from l2discovery LLDP remoteSystem* buckets (Aeris / EOS 4.35+ style)."""
    row: dict[str, Any] = {
        "local_interface": local_interface,
        "neighbor_key": neighbor_key,
        "neighbor_source": neighbor_source,
    }
    name = _tlv_string(nb.get("sysName"))
    if name:
        row["system_name"] = name
    desc = _tlv_string(nb.get("sysDesc"))
    if desc:
        row["system_description"] = desc
    eth = nb.get("ethAddr")
    if isinstance(eth, str) and eth:
        row["eth_addr"] = eth
    row.update(_msap_fields(nb))
    ttl = nb.get("ttl")
    if isinstance(ttl, (int, float)):
        row["ttl_sec"] = int(ttl)
    return row


def _collect_l2discovery_port_status(obj: Any, items: list[dict[str, Any]]) -> None:
    """Recurse and harvest portStatus/*/remoteSystem*/* leaves."""
    if not isinstance(obj, dict):
        return
    ps = obj.get("portStatus")
    if isinstance(ps, dict):
        for intf_name, pnode in ps.items():
            if not isinstance(pnode, dict):
                continue
            for bucket, label in (
                ("remoteSystem", "remoteSystem"),
                ("remoteSystemByMsap", "remoteSystemByMsap"),
            ):
                rs = pnode.get(bucket)
                if not isinstance(rs, dict):
                    continue
                for ridx, rnode in rs.items():
                    if isinstance(rnode, dict):
                        items.append(
                            _l2_remote_row(
                                str(intf_name),
                                str(ridx),
                                rnode,
                                neighbor_source=label,
                            )
                        )
        return
    for v in obj.values():
        _collect_l2discovery_port_status(v, items)


def _parse_l2discovery_lldp_tree(flat: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    _collect_l2discovery_port_status(flat, items)
    return items


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
    sn = _tlv_string(nb.get("sysName"))
    if sn and "system_name" not in row:
        row["system_name"] = sn
    return row


def _parse_l2discovery_remote_leaf(flat: dict[str, Any]) -> list[dict[str, Any]]:
    """Handle a Get that returns only one remoteSystem object (no portStatus wrapper)."""
    if not flat:
        return []
    if flat.get("msap") is not None or isinstance(flat.get("sysName"), dict):
        idx = str(flat.get("index", flat.get("name", "0")))
        return [_l2_remote_row("", idx, flat, neighbor_source="remoteLeaf")]
    if flat.get("ethAddr") and (
        flat.get("index") is not None or flat.get("name") is not None
    ):
        idx = str(flat.get("index", flat.get("name", "0")))
        return [_l2_remote_row("", idx, flat, neighbor_source="remoteLeaf")]
    return []


def parse_lldp_flat_to_items(flat: dict[str, Any]) -> list[dict[str, Any]]:
    """Map flattened LLDP Sysdb snapshot to stable rows for MCP clients."""
    items: list[dict[str, Any]] = []
    if not flat:
        return items
    l2 = _parse_l2discovery_lldp_tree(flat)
    if l2:
        l2.sort(
            key=lambda r: (r.get("local_interface") or "", r.get("neighbor_key") or "")
        )
        return l2
    leaf = _parse_l2discovery_remote_leaf(flat)
    if leaf:
        return leaf
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


def grpc_get_lldp_neighbors(
    datadict: dict[str, Any],
    device_id: str,
    port_name: str = "",
    remote_neighbor_key: str = "",
) -> dict[str, Any]:
    warnings: list[str] = []
    device_id = (device_id or "").strip()
    if not device_id:
        return tool_envelope(
            device_id=None,
            data_source=LLDP_DATA_SOURCE,
            coverage="none",
            items=[],
            warnings=["missing_device_id"],
        )
    explicit = _lldp_paths_for_port_name(device_id, port_name, remote_neighbor_key)
    candidate_paths: tuple[list[Any], ...] = (
        *explicit,
        *_lldp_telemetry_browser_leaf_paths(device_id),
        *_lldp_l2discovery_literal_local_paths(device_id),
        # Full per-port blob (remoteSystem + remoteSystemByMsap, e.g. Ethernet6 / rpi4-0 MAC)
        [
            device_id,
            "Sysdb",
            "l2discovery",
            "lldp",
            "status",
            "local",
            Wildcard(),
            "portStatus",
            Wildcard(),
        ],
        [
            device_id,
            "Sysdb",
            "l2discovery",
            "lldp",
            "status",
            "local",
            Wildcard(),
            "portStatus",
            Wildcard(),
            "remoteSystemByMsap",
            Wildcard(),
        ],
        [
            device_id,
            "Sysdb",
            "l2discovery",
            "lldp",
            "status",
            "local",
            Wildcard(),
            "portStatus",
            Wildcard(),
            "remoteSystem",
            Wildcard(),
        ],
        [
            device_id,
            "Sysdb",
            "l2discovery",
            "lldp",
            "status",
            Wildcard(),
        ],
        [device_id, "Sysdb", "l2discovery", "lldp", Wildcard()],
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
        path_line = _path_for_log(p)
        try:
            snap = _get_path(datadict, device_id, list(p))
            n = len(snap) if isinstance(snap, dict) else 0
            preview = list(snap.keys())[:12] if isinstance(snap, dict) and snap else []
            logging.info(
                "lldp connector: device=%s path=%s key_count=%s keys=%s",
                device_id,
                path_line,
                n,
                preview,
            )
            if snap:
                flat = snap
                path_used = path_line
                break
        except Exception as e:
            logging.warning(
                "lldp connector: device=%s path=%s failed: %s",
                device_id,
                path_line,
                e,
            )
    items = parse_lldp_flat_to_items(flat)
    if flat and not items:
        warnings.append("lldp_data_unparsed")
    if not items and not flat:
        warnings.append("no_lldp_data_at_known_paths")
    logging.info(
        "lldp result: device=%s path_used=%s items=%s warnings=%s",
        device_id,
        path_used or "(none)",
        len(items),
        warnings,
    )
    cov = "full" if items else ("partial" if flat else "none")
    return tool_envelope(
        device_id=device_id,
        data_source=LLDP_DATA_SOURCE,
        coverage=cov,
        items=items,
        obj={"path_hint": path_used, "raw_key_count": len(flat)},
        warnings=warnings,
    )
