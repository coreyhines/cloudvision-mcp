"""Inventory member callables."""

from __future__ import annotations

import logging

import grpc

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.device_resolve import (
    resolve_device_to_serial,
    search_inventory_candidates,
    summarize_inventory_candidates,
)
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.inventory import grpc_all_inventory, grpc_one_inventory_serial
from cvp_mcp.grpc.utils import createConnection


def inventory_get(device_id: str) -> dict:
    """Return information about one inventory device."""
    datadict = env_datadict_from_os()
    logging.debug("CVP Get One Device Tool - %s", device_id)
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            serial, device, warnings, candidates = resolve_device_to_serial(
                datadict, device_id, channel=channel
            )
            if not serial:
                error = {
                    "error": (
                        "device_ambiguous"
                        if "device_ambiguous" in warnings
                        else "device_not_found"
                    ),
                    "device_id_input": (device_id or "").strip(),
                    "warnings": warnings,
                }
                rows = summarize_inventory_candidates(candidates)
                if rows:
                    error["candidates"] = rows
                return error
            if device is None:
                device = grpc_one_inventory_serial(channel, serial)
            if device is None:
                return {
                    "error": "device_not_found",
                    "device_id_input": (device_id or "").strip(),
                    "device_id_resolved": serial,
                    "warnings": [*warnings, "inventory_record_not_found"],
                }
    except Exception as exc:
        logging.error("Error fetching device %s: %s", device_id, exc)
        return {"error": "Device fetch failed"}
    logging.debug("Inventory device: %s", device)
    return device


def inventory_list() -> dict:
    """Return all EOS switches and devices in CloudVision inventory."""
    datadict = env_datadict_from_os()
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
        active, inactive = grpc_all_inventory(channel, exclude_access_points=True)
    return {
        "streaming_active": active,
        "streaming_inactive": inactive,
    }


def inventory_search(query: str) -> dict:
    """Search inventory by hostname, model, serial, FQDN, or MAC fragment."""
    datadict = env_datadict_from_os()
    normalized_query = (query or "").strip()
    if len(normalized_query) < 3:
        return tool_envelope(
            data_source="inventory:search",
            coverage="none",
            items=[],
            warnings=["query_too_short"],
            obj={
                "query": normalized_query,
                "hint": "Provide at least 3 characters (e.g. 720xp, spine-1).",
            },
        )
    try:
        conn_creds = createConnection(datadict)
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            matches, warnings = search_inventory_candidates(
                datadict, normalized_query, channel=channel
            )
        items = summarize_inventory_candidates(matches)
        return tool_envelope(
            data_source="inventory:search",
            coverage="full" if items else "none",
            items=items,
            warnings=warnings,
            obj={
                "query": normalized_query,
                "match_count": len(items),
                "next_step": ('topology(action="lldp", device_id=<serial_number>)'),
            },
        )
    except Exception as exc:
        return client_error(
            "inventory_search_failed",
            log_exc=exc,
            context="search_cvp_inventory",
        )


def members() -> dict[str, MemberSpec]:
    """Return inventory member specifications keyed by action."""
    return {
        "get": MemberSpec(
            action="get",
            description="Get one device by serial, hostname, FQDN, or system MAC.",
            required=["device_id"],
            properties={
                "device_id": {
                    "type": "string",
                    "description": "Device identifier.",
                }
            },
            call=inventory_get,
        ),
        "list": MemberSpec(
            action="list",
            description="List all EOS devices in CloudVision inventory.",
            required=[],
            properties={},
            call=inventory_list,
            rate_limit_key="inventory.list",
        ),
        "search": MemberSpec(
            action="search",
            description="Search inventory by hostname, model, serial, FQDN, or MAC.",
            required=["query"],
            properties={
                "query": {
                    "type": "string",
                    "description": "Inventory search text; at least three characters.",
                }
            },
            call=inventory_search,
        ),
    }
