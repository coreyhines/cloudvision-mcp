"""Overlay member callables."""

from __future__ import annotations

from collections.abc import Callable

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.device_resolve import resolve_device_to_serial
from cvp_mcp.grpc.overlay import grpc_get_evpn, grpc_get_vxlan
from cvp_mcp.grpc.utils import createConnection
from cvp_mcp.members._device import (
    attach_device_resolution,
    device_not_found_envelope,
)


def _overlay_query(
    device_id: str,
    *,
    data_source: str,
    failure_code: str,
    context: str,
    query: Callable[[dict, str], dict],
) -> dict:
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            serial, _info, warnings, candidates = resolve_device_to_serial(
                datadict, device_id, channel=channel
            )
            if not serial:
                return device_not_found_envelope(
                    device_id, data_source, warnings, candidates
                )
            result = query(datadict, serial)
            return attach_device_resolution(result, device_id, serial, warnings)
    except Exception as exc:
        return client_error(failure_code, log_exc=exc, context=context)


def overlay_evpn(device_id: str) -> dict:
    """Return an EVPN-related device snapshot."""
    return _overlay_query(
        device_id,
        data_source="connector:device:Sysdb/evpn",
        failure_code="evpn_failed",
        context="get_cvp_evpn",
        query=grpc_get_evpn,
    )


def overlay_vxlan(device_id: str) -> dict:
    """Return VxLAN-related device snapshots."""
    return _overlay_query(
        device_id,
        data_source="connector:device:Sysdb/vxlan",
        failure_code="vxlan_failed",
        context="get_cvp_vxlan",
        query=grpc_get_vxlan,
    )


def members() -> dict[str, MemberSpec]:
    """Return overlay member specifications keyed by action."""
    properties = {
        "device_id": {
            "type": "string",
            "description": "Device serial, hostname, FQDN, or system MAC.",
        }
    }
    return {
        "evpn": MemberSpec(
            action="evpn",
            description="Get an EVPN-related device snapshot.",
            required=["device_id"],
            properties=properties,
            call=overlay_evpn,
        ),
        "vxlan": MemberSpec(
            action="vxlan",
            description="Get VxLAN-related device snapshots.",
            required=["device_id"],
            properties=properties,
            call=overlay_vxlan,
        ),
    }
