"""LLDP and network-topology member callables."""

from __future__ import annotations

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.device_resolve import resolve_device_to_serial
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.lldp import LLDP_DATA_SOURCE, grpc_get_lldp_neighbors
from cvp_mcp.grpc.network_map import grpc_map_network_topology
from cvp_mcp.grpc.utils import _is_lab_device, createConnection
from cvp_mcp.members._device import (
    attach_device_resolution,
    device_not_found_envelope,
)


def topology_lldp(
    device_id: str,
    port_name: str = "",
    remote_neighbor_key: str = "",
    include_lab_devices: bool = False,
) -> dict:
    """Return LLDP neighbors for a resolved physical device."""
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            serial, device_info, warnings, candidates = resolve_device_to_serial(
                datadict, device_id, channel=channel
            )
            if not serial:
                return device_not_found_envelope(
                    device_id, LLDP_DATA_SOURCE, warnings, candidates
                )
            if not include_lab_devices and _is_lab_device(device_info):
                return tool_envelope(
                    device_id=serial,
                    data_source=LLDP_DATA_SOURCE,
                    coverage="none",
                    items=[],
                    warnings=["device_excluded_lab_or_virtual"],
                    obj={
                        "device_id_input": (device_id or "").strip(),
                        "device_id_resolved": serial,
                        "hint": (
                            "Device is a virtual/lab EOS instance (vEOS or cEOS). "
                            "Pass include_lab_devices=True to query it explicitly."
                        ),
                    },
                )
            if (device_info or {}).get("streaming_status") == "Inactive":
                return tool_envelope(
                    device_id=serial,
                    data_source=LLDP_DATA_SOURCE,
                    coverage="none",
                    items=[],
                    warnings=["device_inactive_not_streaming"],
                    obj={
                        "device_id_input": (device_id or "").strip(),
                        "device_id_resolved": serial,
                    },
                )
            result = grpc_get_lldp_neighbors(
                datadict,
                serial,
                port_name=port_name,
                remote_neighbor_key=remote_neighbor_key,
                device_model=str((device_info or {}).get("model") or ""),
            )
            return attach_device_resolution(result, device_id, serial, warnings)
    except Exception as exc:
        return client_error(
            "lldp_neighbors_failed", log_exc=exc, context="get_cvp_lldp_neighbors"
        )


def topology_map(
    output_format: str = "json",
    include_inactive_devices: bool = False,
    max_ethernet_ports: int | None = None,
    device_serial_allowlist: str = "",
    topology_name: str = "cvp-lldp",
    topology_node_scope: str = "full_inventory",
    lldp_port_source: str = "auto",
    include_lab_devices: bool = False,
) -> dict:
    """Discover and format LLDP topology across CloudVision inventory."""
    datadict = env_datadict_from_os()
    allowed = {"json", "mermaid", "table", "containerlab"}
    output = (output_format or "json").strip().lower()
    if output not in allowed:
        return {
            "error": f"output_format must be one of {sorted(allowed)}",
            "output_format": output_format,
        }
    try:
        return grpc_map_network_topology(
            datadict,
            output_format=output,
            include_inactive_devices=include_inactive_devices,
            max_ethernet_ports=max_ethernet_ports,
            device_serial_allowlist=device_serial_allowlist,
            topology_name=topology_name,
            topology_node_scope=topology_node_scope,
            lldp_port_source=lldp_port_source,
            include_lab_devices=include_lab_devices,
        )
    except Exception as exc:
        return client_error(
            "network_topology_failed",
            log_exc=exc,
            context="map_cvp_network_topology",
        )


def members() -> dict[str, MemberSpec]:
    """Return topology member specifications keyed by action."""
    return {
        "lldp": MemberSpec(
            action="lldp",
            description="Get LLDP neighbors for one device.",
            required=["device_id"],
            properties={
                "device_id": {"type": "string", "description": "Device identifier."},
                "port_name": {
                    "type": "string",
                    "default": "",
                    "description": "Optional local port to probe.",
                },
                "remote_neighbor_key": {
                    "type": "string",
                    "default": "",
                    "description": "Optional remote-neighbor index.",
                },
                "include_lab_devices": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include virtual or lab EOS devices.",
                },
            },
            call=topology_lldp,
        ),
        "map": MemberSpec(
            action="map",
            description="Discover and export LLDP topology.",
            required=[],
            properties={
                "output_format": {
                    "type": "string",
                    "default": "json",
                    "description": "json, mermaid, table, or containerlab.",
                },
                "include_inactive_devices": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include inactive inventory devices.",
                },
                "max_ethernet_ports": {
                    "type": "integer",
                    "description": "Maximum ports probed per device.",
                },
                "device_serial_allowlist": {
                    "type": "string",
                    "default": "",
                    "description": "Comma-separated serials to scan.",
                },
                "topology_name": {
                    "type": "string",
                    "default": "cvp-lldp",
                    "description": "Topology export name.",
                },
                "topology_node_scope": {
                    "type": "string",
                    "default": "full_inventory",
                    "description": "full_inventory or connected.",
                },
                "lldp_port_source": {
                    "type": "string",
                    "default": "auto",
                    "description": "auto, oper_up_only, or full_range.",
                },
                "include_lab_devices": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include virtual or lab EOS devices.",
                },
            },
            call=topology_map,
            rate_limit_key="topology.map",
        ),
    }
