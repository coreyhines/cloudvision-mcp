"""Endpoint-location member callables."""

from __future__ import annotations

import logging

import grpc

from cvp_mcp.env import cvp_credentials_missing_reasons, env_datadict_from_os
from cvp_mcp.grouped_tool import MemberSpec
from cvp_mcp.grpc.device_resolve import (
    resolve_device_to_serial,
    summarize_inventory_candidates,
)
from cvp_mcp.grpc.endpoint import (
    endpoint_location_matches_filters,
    grpc_endpoints_for_search_keys,
    grpc_one_endpoint_location,
)
from cvp_mcp.grpc.endpoint_seed import seed_endpoint_search_keys
from cvp_mcp.grpc.hostname_resolve import resolve_endpoint_query
from cvp_mcp.grpc.inventory import grpc_one_inventory_serial
from cvp_mcp.grpc.utils import createConnection


def _endpoint_search_queries(search_term: str) -> list[str]:
    """Try a resolved IP before the raw endpoint search term."""
    normalized = (search_term or "").strip()
    if not normalized:
        return [normalized]
    resolved = resolve_endpoint_query(normalized)
    if resolved != normalized:
        return [resolved, normalized]
    return [normalized]


def endpoint_get(search_term: str) -> dict:
    """Return endpoint locations matching a MAC, IP, or hostname."""
    datadict = env_datadict_from_os()
    all_devices: dict = {}
    all_endpoints: list = []
    logging.info("CVP Get Endpoint Location")
    conn_creds = createConnection(datadict)
    with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
        for query in _endpoint_search_queries(search_term):
            all_endpoints = grpc_one_endpoint_location(channel, query)
            if all_endpoints:
                break
        for endpoint in all_endpoints:
            for device in endpoint["location_list"]:
                serial_number = device["device_id"]["value"]
                if serial_number not in all_devices:
                    all_devices[serial_number] = grpc_one_inventory_serial(
                        channel, serial_number
                    )
    return {"devices": all_devices, "endpoints": all_endpoints}


def endpoint_list(
    device_serial_allowlist: list[str] | None = None,
    max_search_keys: int | None = None,
) -> dict:
    """Return LLDP-seeded endpoint locations from CloudVision."""
    datadict = env_datadict_from_os()
    credential_warnings = cvp_credentials_missing_reasons(datadict)
    if credential_warnings:
        return {
            "error": "missing_cloudvision_credentials",
            "warnings": credential_warnings,
        }
    all_devices: dict = {}
    logging.info("CVP Get All Endpoint Locations")
    conn_creds = createConnection(datadict)
    warnings: list[str] = []
    try:
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            seed = seed_endpoint_search_keys(
                datadict,
                channel,
                device_serials=device_serial_allowlist,
            )
            warnings.extend(seed["warnings"])
            search_keys = seed["search_keys"]
            if max_search_keys is not None and len(search_keys) > max_search_keys:
                truncated = len(search_keys) - max_search_keys
                warnings.append(f"search_keys_truncated:{truncated}")
                search_keys = search_keys[:max_search_keys]
            lookup = grpc_endpoints_for_search_keys(channel, search_keys)
            warnings.extend(lookup["warnings"])
            all_endpoints = lookup["endpoints"]
            for endpoint in all_endpoints:
                for device in endpoint["location_list"]:
                    serial_number = device["device_id"]["value"]
                    if serial_number not in all_devices:
                        all_devices[serial_number] = grpc_one_inventory_serial(
                            channel, serial_number
                        )
            seed_stats = {
                **seed["seed_stats"],
                "getsome_hits": lookup["hits"],
                "getsome_misses": lookup["misses"],
                "lookup_method": lookup["method"],
            }
            return {
                "devices": all_devices,
                "endpoints": all_endpoints,
                "seed_stats": seed_stats,
                "warnings": warnings,
            }
    except Exception as exc:
        logging.error("Endpoint location pipeline failed: %s", exc)
        return {"error": f"seed_failed:{exc}", "warnings": warnings}


def endpoint_filter(
    device_id: str | None = None,
    interface: str | None = None,
    vlan_id: int | None = None,
) -> dict:
    """Filter LLDP-seeded endpoint locations by switch, interface, or VLAN."""
    datadict = env_datadict_from_os()
    credential_warnings = cvp_credentials_missing_reasons(datadict)
    if credential_warnings:
        return {
            "error": "missing_cloudvision_credentials",
            "warnings": credential_warnings,
        }
    all_devices: dict = {}
    logging.info(
        "CVP Get Filtered Endpoint Locations: device=%s intf=%s vlan=%s",
        device_id,
        interface,
        vlan_id,
    )
    if device_id is None and interface is None and vlan_id is None:
        logging.warning(
            "get_cvp_endpoint_locations_filtered called with no filters; "
            "this may return a large result set."
        )
    filter_device_id = device_id
    conn_creds = createConnection(datadict)
    warnings: list[str] = []
    try:
        with grpc.secure_channel(datadict["cvp"], conn_creds) as channel:
            if filter_device_id:
                serial, _info, resolution_warnings, candidates = (
                    resolve_device_to_serial(
                        datadict, filter_device_id, channel=channel
                    )
                )
                if not serial:
                    error = {
                        "error": (
                            "device_ambiguous"
                            if "device_ambiguous" in resolution_warnings
                            else "device_not_found"
                        ),
                        "device_id_input": (filter_device_id or "").strip(),
                        "warnings": resolution_warnings,
                    }
                    rows = summarize_inventory_candidates(candidates)
                    if rows:
                        error["candidates"] = rows
                    return error
                filter_device_id = serial
            seed = seed_endpoint_search_keys(
                datadict,
                channel,
                device_serials=[filter_device_id] if filter_device_id else None,
            )
            warnings.extend(seed["warnings"])
            lookup = grpc_endpoints_for_search_keys(channel, seed["search_keys"])
            warnings.extend(lookup["warnings"])
            all_endpoints = [
                endpoint
                for endpoint in lookup["endpoints"]
                if endpoint_location_matches_filters(
                    endpoint,
                    device_id=filter_device_id,
                    interface=interface,
                    vlan_id=vlan_id,
                )
            ]
            for endpoint in all_endpoints:
                for device in endpoint["location_list"]:
                    serial_number = device["device_id"]["value"]
                    if serial_number not in all_devices:
                        all_devices[serial_number] = grpc_one_inventory_serial(
                            channel, serial_number
                        )
            seed_stats = {
                **seed["seed_stats"],
                "getsome_hits": lookup["hits"],
                "getsome_misses": lookup["misses"],
                "lookup_method": lookup["method"],
            }
            return {
                "devices": all_devices,
                "endpoints": all_endpoints,
                "seed_stats": seed_stats,
                "warnings": warnings,
            }
    except Exception as exc:
        logging.error("Filtered endpoint location pipeline failed: %s", exc)
        return {"error": f"seed_failed:{exc}", "warnings": warnings}


def members() -> dict[str, MemberSpec]:
    """Return endpoint member specifications keyed by action."""
    return {
        "get": MemberSpec(
            action="get",
            description="Find endpoint locations by MAC, IP, or hostname.",
            required=["search_term"],
            properties={
                "search_term": {
                    "type": "string",
                    "description": "Endpoint MAC address, IP address, or hostname.",
                }
            },
            call=endpoint_get,
        ),
        "list": MemberSpec(
            action="list",
            description="List LLDP-seeded endpoint locations.",
            required=[],
            properties={
                "device_serial_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional switch serials used for LLDP seeding.",
                },
                "max_search_keys": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of deduplicated keys to query.",
                },
            },
            call=endpoint_list,
        ),
        "filter": MemberSpec(
            action="filter",
            description="Filter endpoint locations by device, interface, or VLAN.",
            required=[],
            properties={
                "device_id": {"type": "string", "description": "Device identifier."},
                "interface": {
                    "type": "string",
                    "description": "Attached switch interface.",
                },
                "vlan_id": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4094,
                    "description": "Attached VLAN ID.",
                },
            },
            call=endpoint_filter,
        ),
    }
