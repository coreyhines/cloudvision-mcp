"""Connectivity-monitor probe member callables."""

from __future__ import annotations

import json
import logging

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.hostname_resolve import resolve_endpoint_query
from cvp_mcp.grpc.inventory import grpc_one_inventory_serial
from cvp_mcp.grpc.monitor import grpc_all_probe_status, grpc_one_probe_status
from cvp_mcp.grpc.utils import createConnection


def probes_list() -> dict:
    """Return all connectivity probes and their source devices."""
    datadict = env_datadict_from_os()
    devices: dict = {}
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
        probes = grpc_all_probe_status(channel)
        for probe in probes:
            serial = probe["serial_number"]
            if serial not in devices:
                devices[serial] = grpc_one_inventory_serial(channel, serial)
    return {"devices": devices, "probes": probes}


def probes_get(
    serial_number: str | None = None,
    endpoint: str | None = None,
    vrf: str | None = None,
    source_interface: str | None = None,
) -> str:
    """Return matching connectivity probes and their source devices."""
    datadict = env_datadict_from_os()
    devices: dict = {}
    probe_hosts = [""]
    if endpoint and endpoint.strip():
        raw_endpoint = endpoint.strip()
        resolved = resolve_endpoint_query(raw_endpoint)
        probe_hosts = (
            [resolved, raw_endpoint] if resolved != raw_endpoint else [raw_endpoint]
        )
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            probes: list = []
            for host_key in probe_hosts:
                probes = grpc_one_probe_status(
                    channel,
                    serial_number or "",
                    host_key,
                    vrf or "",
                    source_interface or "",
                )
                if probes:
                    break
            for probe in probes:
                serial = probe["serial_number"]
                if serial not in devices:
                    devices[serial] = grpc_one_inventory_serial(channel, serial)
    except Exception as exc:
        logging.error("Error fetching connectivity probe: %s", exc)
        return '{"error": "Lifecycle fetch failed"}'
    return json.dumps({"probes": probes, "devices": devices}, indent=2)


def members() -> dict[str, MemberSpec]:
    """Return connectivity-probe member specifications keyed by action."""
    return {
        "list": MemberSpec(
            action="list",
            description="List all connectivity monitor probes.",
            required=[],
            properties={},
            call=probes_list,
        ),
        "get": MemberSpec(
            action="get",
            description="Get connectivity probe status using optional filters.",
            required=[],
            properties={
                "serial_number": {
                    "type": "string",
                    "description": "Source device serial number.",
                },
                "endpoint": {
                    "type": "string",
                    "description": "Probe endpoint IP or hostname.",
                },
                "vrf": {"type": "string", "description": "Probe VRF."},
                "source_interface": {
                    "type": "string",
                    "description": "Probe source interface.",
                },
            },
            call=probes_get,
        ),
    }
