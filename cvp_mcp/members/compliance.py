"""Bug, lifecycle, designed-config, and compliance-status members."""

from __future__ import annotations

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.bugs import grpc_all_bug_exposure
from cvp_mcp.grpc.connector import conn_get_info_bugs
from cvp_mcp.grpc.inventory import grpc_one_inventory_serial
from cvp_mcp.grpc.lifecycle import grpc_all_device_lifecycle
from cvp_mcp.grpc.studios import (
    get_cvp_designed_config as grpc_get_designed_config,
)
from cvp_mcp.grpc.utils import createConnection
from cvp_mcp.members.compliance_status import config_status, image_status


def compliance_bugs() -> dict:
    """Return fleet bug exposure with bug and device details."""
    datadict = env_datadict_from_os()
    devices = []
    bug_ids: list[str] = []
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
        bugs = grpc_all_bug_exposure(channel)
        for bug in bugs or []:
            for bug_id in bug["bug_ids"]:
                if bug_id not in bug_ids:
                    bug_ids.append(bug_id)
            device = grpc_one_inventory_serial(channel, bug["serial_number"])
            if device:
                devices.append(device)
    return {
        "bug_info": conn_get_info_bugs(datadict, bug_ids),
        "bugs": bugs,
        "devices": devices,
    }


def compliance_lifecycle() -> dict:
    """Return fleet hardware and software lifecycle information."""
    datadict = env_datadict_from_os()
    devices: dict = {}
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
        lifecycle = grpc_all_device_lifecycle(channel)
        for item in lifecycle:
            serial = item["serial_number"]
            if serial not in devices:
                devices[serial] = grpc_one_inventory_serial(channel, serial)
    return {"devices": devices, "lifecycle": lifecycle}


def compliance_designed_config(device_id: str) -> dict:
    """Return designed-config text and studio provenance for one device."""
    datadict = env_datadict_from_os()
    try:
        return grpc_get_designed_config(datadict, device_id)
    except Exception as exc:
        return client_error(
            "designed_config_failed",
            log_exc=exc,
            context="get_cvp_designed_config",
        )


def members() -> dict[str, MemberSpec]:
    """Return compliance member specifications keyed by action."""
    device_id = {
        "device_id": {
            "type": "string",
            "description": "Device serial, hostname, FQDN, or system MAC.",
        }
    }
    return {
        "bugs": MemberSpec(
            action="bugs",
            description="List fleet bug and CVE exposure.",
            required=[],
            properties={},
            call=compliance_bugs,
        ),
        "lifecycle": MemberSpec(
            action="lifecycle",
            description="List fleet hardware and software lifecycle information.",
            required=[],
            properties={},
            call=compliance_lifecycle,
        ),
        "designed_config": MemberSpec(
            action="designed_config",
            description="Get designed-config text and studio provenance.",
            required=["device_id"],
            properties=device_id,
            call=compliance_designed_config,
        ),
        "config_status": MemberSpec(
            action="config_status",
            description="Get product config compliance status when API access permits.",
            required=["device_id"],
            properties=device_id,
            call=config_status,
        ),
        "image_status": MemberSpec(
            action="image_status",
            description="Get product image compliance status when API access permits.",
            required=["device_id"],
            properties=device_id,
            call=image_status,
        ),
    }
