"""Routing member callables."""

from __future__ import annotations

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.device_resolve import resolve_device_to_serial
from cvp_mcp.grpc.routing import grpc_get_bgp_status, grpc_get_routes
from cvp_mcp.grpc.utils import createConnection
from cvp_mcp.members._device import (
    attach_device_resolution,
    device_not_found_envelope,
)


def routing_bgp(device_id: str) -> dict:
    """Return a BGP operational snapshot."""
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            serial, _info, warnings, candidates = resolve_device_to_serial(
                datadict, device_id, channel=channel
            )
            if not serial:
                return device_not_found_envelope(
                    device_id,
                    "connector:device:Sysdb/routing/bgp",
                    warnings,
                    candidates,
                )
            result = grpc_get_bgp_status(datadict, serial)
            return attach_device_resolution(result, device_id, serial, warnings)
    except Exception as exc:
        return client_error(
            "bgp_status_failed", log_exc=exc, context="get_cvp_bgp_status"
        )


def routing_routes(device_id: str, vrf: str = "default") -> dict:
    """Return active route-like RIB entries."""
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            serial, _info, warnings, candidates = resolve_device_to_serial(
                datadict, device_id, channel=channel
            )
            if not serial:
                return device_not_found_envelope(
                    device_id,
                    "connector:device:Sysdb/routing",
                    warnings,
                    candidates,
                )
            result = grpc_get_routes(datadict, serial, vrf=vrf)
            return attach_device_resolution(result, device_id, serial, warnings)
    except Exception as exc:
        return client_error("routes_failed", log_exc=exc, context="get_cvp_routes")


def members() -> dict[str, MemberSpec]:
    """Return routing member specifications keyed by action."""
    device_id = {
        "device_id": {
            "type": "string",
            "description": "Device serial, hostname, FQDN, or system MAC.",
        }
    }
    return {
        "bgp": MemberSpec(
            action="bgp",
            description="Get a BGP operational snapshot.",
            required=["device_id"],
            properties=device_id,
            call=routing_bgp,
        ),
        "routes": MemberSpec(
            action="routes",
            description="Get active route-like RIB entries.",
            required=["device_id"],
            properties={
                **device_id,
                "vrf": {
                    "type": "string",
                    "default": "default",
                    "description": "VRF used to label route path selection.",
                },
            },
            call=routing_routes,
        ),
    }
