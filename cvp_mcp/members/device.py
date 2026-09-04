"""Device detail member callables."""

from __future__ import annotations

from collections.abc import Callable

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.config import grpc_get_device_config
from cvp_mcp.grpc.device_resolve import resolve_device_to_serial
from cvp_mcp.grpc.interfaces import (
    grpc_get_interfaces,
    grpc_get_ip_interfaces,
    grpc_get_vlans,
)
from cvp_mcp.grpc.overlay import grpc_get_features, grpc_get_system_health
from cvp_mcp.grpc.utils import createConnection
from cvp_mcp.members._device import (
    attach_device_resolution,
    device_not_found_envelope,
)


def device_config(device_id: str, include_running_config: bool = False) -> dict:
    """Return device configuration metadata and optional running config."""
    datadict = env_datadict_from_os()
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            return grpc_get_device_config(
                channel,
                datadict,
                device_id,
                include_running_config=include_running_config,
            )
    except Exception as exc:
        return client_error(
            "device_config_failed",
            log_exc=exc,
            context="get_cvp_device_config",
        )


def _resolved_device_query(
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


def device_interfaces(device_id: str) -> dict:
    """Return device interfaces and operational details."""
    return _resolved_device_query(
        device_id,
        data_source="connector:device:Sysdb/interface",
        failure_code="interfaces_failed",
        context="get_cvp_interfaces",
        query=grpc_get_interfaces,
    )


def device_vlans(device_id: str) -> dict:
    """Return VLAN and switchport rows for a device."""
    return _resolved_device_query(
        device_id,
        data_source="connector:device:Sysdb/bridging",
        failure_code="vlans_failed",
        context="get_cvp_vlans",
        query=grpc_get_vlans,
    )


def device_ip_interfaces(device_id: str) -> dict:
    """Return layer-three addresses by device interface."""
    return _resolved_device_query(
        device_id,
        data_source="connector:device:Sysdb/ip",
        failure_code="ip_interfaces_failed",
        context="get_cvp_ip_interfaces",
        query=grpc_get_ip_interfaces,
    )


def device_features(device_id: str) -> dict:
    """Return enabled-feature-related device snapshots."""
    return _resolved_device_query(
        device_id,
        data_source="connector:device:Sysdb/feature",
        failure_code="features_failed",
        context="get_cvp_features",
        query=grpc_get_features,
    )


def device_health(device_id: str) -> dict:
    """Return system status and environment sensor snapshots."""
    return _resolved_device_query(
        device_id,
        data_source="connector:device:Sysdb/sys+environment",
        failure_code="system_health_failed",
        context="get_cvp_system_health",
        query=grpc_get_system_health,
    )


def _device_id_property() -> dict[str, dict[str, str]]:
    return {
        "device_id": {
            "type": "string",
            "description": "Device serial, hostname, FQDN, or system MAC.",
        }
    }


def members() -> dict[str, MemberSpec]:
    """Return device member specifications keyed by action."""
    return {
        "config": MemberSpec(
            action="config",
            description="Get device config metadata and optional running config.",
            required=["device_id"],
            properties={
                **_device_id_property(),
                "include_running_config": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include running-config text fetched from its URI.",
                },
            },
            call=device_config,
        ),
        "interfaces": MemberSpec(
            action="interfaces",
            description="Get interface state, descriptions, and counters.",
            required=["device_id"],
            properties=_device_id_property(),
            call=device_interfaces,
        ),
        "vlans": MemberSpec(
            action="vlans",
            description="Get VLAN and switchport-related rows.",
            required=["device_id"],
            properties=_device_id_property(),
            call=device_vlans,
        ),
        "ip_interfaces": MemberSpec(
            action="ip_interfaces",
            description="Get layer-three addresses by interface.",
            required=["device_id"],
            properties=_device_id_property(),
            call=device_ip_interfaces,
        ),
        "features": MemberSpec(
            action="features",
            description="Get enabled-feature-related snapshots.",
            required=["device_id"],
            properties=_device_id_property(),
            call=device_features,
        ),
        "health": MemberSpec(
            action="health",
            description="Get system status and environment sensor snapshots.",
            required=["device_id"],
            properties=_device_id_property(),
            call=device_health,
        ),
    }
