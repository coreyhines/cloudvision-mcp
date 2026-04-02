"""
Clover flow data via CloudVision Connector (path-based queries).

Path shape: /Clover/flows/v1/path and per-flow children .../path/{index}
(e.g. .../path/0) with camelCase fields and optional [header, stats[]] layout.
"""

from __future__ import annotations

import logging

from cloudvision.Connector.grpc_client import GRPCClient

from cvp_mcp.grpc.connector import get, serialize_cloudvision_data
from cvp_mcp.grpc.models import FlowRecord

FLOW_DATASET = "analytics"
# Parent path for Clover flows; integer indices (0, 1, ...) appear as child keys or path suffixes.
FLOW_BASE_PATH = ["Clover", "flows", "v1", "path"]

STAT_KEYS = frozenset({"bytes", "packets", "nodes", "applications"})

_IANA_PROTO = {
    1: "ICMP",
    6: "TCP",
    17: "UDP",
    47: "GRE",
    50: "ESP",
}


def conn_get_flow_data(datadict, device_id=None, flow_index=None):
    """
    Queries CloudVision analytics for Clover flow records under FLOW_BASE_PATH.

    device_id: optional switch serial; filters parsed records to matching node.device.
    flow_index: optional path suffix (e.g. 0 for .../path/0); if unset, queries the parent path.
    Returns list of FlowRecord dicts.
    """
    all_flows: list[FlowRecord] = []
    cvp = (datadict.get("cvp") or "").strip()
    if cvp and ":" not in cvp:
        cvp = f"{cvp}:443"
    token = (datadict.get("cvtoken") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    path = list(FLOW_BASE_PATH)
    if flow_index is not None and str(flow_index) != "":
        path.append(str(flow_index))

    try:
        with GRPCClient(grpcAddr=cvp, tokenValue=token) as client:
            raw = get(client, FLOW_DATASET, path)
            all_flows = _parse_flow_records(raw)
    except Exception as e:
        logging.error(f"Flow data query error: {e}")

    if device_id:
        all_flows = [r for r in all_flows if r.get("device_id") == device_id]
    return all_flows


def _protocol_str(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, int):
        return _IANA_PROTO.get(value, str(value))
    return str(value)


def _is_header(d: dict) -> bool:
    return ("srcIP" in d or "srcIp" in d) and ("dstIP" in d or "dstIp" in d)


def _split_merged(d: dict) -> tuple[dict, dict]:
    header = {k: v for k, v in d.items() if k not in STAT_KEYS}
    stat = {k: d[k] for k in STAT_KEYS if k in d}
    return header, stat


def _clover_record(path_index: str, header: dict, stat: dict) -> FlowRecord:
    nodes = stat.get("nodes") if isinstance(stat.get("nodes"), list) else []
    n0 = nodes[0] if nodes else {}
    if not isinstance(n0, dict):
        n0 = {}
    proto = header.get("protocol")
    return {
        "flow_path_index": path_index,
        "src_ip": str(header.get("srcIP") or header.get("srcIp") or ""),
        "dst_ip": str(header.get("dstIP") or header.get("dstIp") or ""),
        "src_port": int(header.get("srcPort") or header.get("src_port") or 0),
        "dst_port": int(header.get("dstPort") or header.get("dst_port") or 0),
        "vrf_name": str(header.get("vrfName") or ""),
        "protocol": _protocol_str(proto),
        "bytes_count": int(stat.get("bytes") or 0),
        "packet_count": int(stat.get("packets") or 0),
        "device_id": str(n0.get("device") or ""),
        "ingress_interface": str(n0.get("ingressInterface") or ""),
        "egress_interface": str(n0.get("egressInterface") or ""),
        "flow_type": "Clover",
        "applications": list(stat.get("applications") or []),
    }


def _parse_flow_node(path_index: str, value) -> list[FlowRecord]:
    if value is None:
        return []
    if isinstance(value, list):
        if not value:
            return []
        if isinstance(value[0], dict) and _is_header(value[0]):
            h = value[0]
            out: list[FlowRecord] = []
            for part in value[1:]:
                if isinstance(part, list):
                    for stat in part:
                        if isinstance(stat, dict):
                            out.append(_clover_record(path_index, h, stat))
                elif isinstance(part, dict):
                    out.append(_clover_record(path_index, h, part))
            return out
        return [r for item in value for r in _parse_flow_node(path_index, item)]

    if not isinstance(value, dict):
        return []

    if _is_header(value) and bool(STAT_KEYS & value.keys()):
        h, s = _split_merged(value)
        return [_clover_record(path_index, h, s)]

    if value.keys() and all(str(k).isdigit() for k in value):
        acc: list[FlowRecord] = []
        for ck, cv in value.items():
            acc.extend(_parse_flow_node(str(ck), cv))
        return acc

    if _is_header(value):
        return [_clover_record(path_index, value, {})]

    acc = []
    for ck, cv in value.items():
        next_index = f"{path_index}/{ck}" if path_index else str(ck)
        acc.extend(_parse_flow_node(next_index, cv))
    return acc


def _parse_flow_records(raw) -> list[FlowRecord]:
    if not raw:
        return []
    raw = serialize_cloudvision_data(raw)

    if isinstance(raw, list):
        return [r for i, v in enumerate(raw) for r in _parse_flow_node(str(i), v)]

    if not isinstance(raw, dict):
        return []

    if _is_header(raw) and bool(STAT_KEYS & raw.keys()):
        h, s = _split_merged(raw)
        return [_clover_record("", h, s)]

    acc: list[FlowRecord] = []
    for k, v in raw.items():
        acc.extend(_parse_flow_node(str(k), v))
    return acc
