#!/usr/bin/python3

import argparse
import json
import logging
import os
import re
import sys
from typing import Any

import grpc
from mcp.server.fastmcp import FastMCP

from cvp_mcp.env import cvp_credentials_missing_reasons, env_datadict_from_os
from cvp_mcp.errors import client_error
from cvp_mcp.grpc.bugs import grpc_all_bug_exposure
from cvp_mcp.grpc.capability import probe_arista_v1_packages
from cvp_mcp.grpc.config import grpc_get_device_config
from cvp_mcp.grpc.connector import conn_get_info_bugs
from cvp_mcp.grpc.device_resolve import (
    resolve_device_to_serial,
    search_inventory_candidates,
    summarize_inventory_candidates,
)
from cvp_mcp.grpc.endpoint import (
    endpoint_location_matches_filters,
    grpc_endpoints_for_search_keys,
    grpc_one_endpoint_location,
)
from cvp_mcp.grpc.endpoint_seed import seed_endpoint_search_keys
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.events import grpc_get_cvp_events, grpc_search_cvp_events
from cvp_mcp.grpc.flow import conn_get_flow_data
from cvp_mcp.grpc.hostname_resolve import resolve_endpoint_query
from cvp_mcp.grpc.interfaces import (
    grpc_get_interfaces,
    grpc_get_ip_interfaces,
    grpc_get_vlans,
)
from cvp_mcp.grpc.inventory import grpc_all_inventory, grpc_one_inventory_serial
from cvp_mcp.grpc.lifecycle import grpc_all_device_lifecycle
from cvp_mcp.grpc.lldp import LLDP_DATA_SOURCE, grpc_get_lldp_neighbors
from cvp_mcp.grpc.monitor import grpc_all_probe_status, grpc_one_probe_status
from cvp_mcp.grpc.network_map import grpc_map_network_topology
from cvp_mcp.grpc.overlay import (
    grpc_get_evpn,
    grpc_get_features,
    grpc_get_system_health,
    grpc_get_vxlan,
)
from cvp_mcp.grpc.routing import grpc_get_bgp_status, grpc_get_routes
from cvp_mcp.grpc.studio_crud import (
    create_cvp_studio as studio_crud_create,
)
from cvp_mcp.grpc.studio_crud import (
    delete_cvp_studio as studio_crud_delete,
)
from cvp_mcp.grpc.studio_inputs_generic import (
    set_cvp_studio_inputs as studio_inputs_set,
)
from cvp_mcp.grpc.studio_tags import (
    assign_cvp_studio_tags as studio_tags_assign,
)
from cvp_mcp.grpc.studio_tags import (
    get_cvp_studio_assigned_tags as studio_tags_get_assigned,
)
from cvp_mcp.grpc.studios import (
    get_cvp_designed_config as studios_get_designed_config,
)
from cvp_mcp.grpc.studios import (
    get_cvp_studio as studios_get_studio,
)
from cvp_mcp.grpc.studios import (
    get_cvp_studio_inputs as studios_get_studio_inputs,
)
from cvp_mcp.grpc.studios import (
    get_cvp_studios as studios_get_studios,
)
from cvp_mcp.grpc.studios import (
    get_cvp_workspace as studios_get_workspace,
)
from cvp_mcp.grpc.studios import (
    get_cvp_workspace_build as studios_get_workspace_build,
)
from cvp_mcp.grpc.studios import (
    get_cvp_workspaces as studios_get_workspaces,
)
from cvp_mcp.grpc.studios import (
    search_cvp_studio_templates as studios_search_templates,
)
from cvp_mcp.grpc.studios_write import (
    build_cvp_workspace as studios_build_workspace,
)
from cvp_mcp.grpc.studios_write import (
    create_cvp_workspace as studios_create_workspace,
)
from cvp_mcp.grpc.studios_write import (
    delete_cvp_workspace as studios_delete_workspace,
)
from cvp_mcp.grpc.studios_write import (
    set_cvp_access_interface_description as studios_set_access_description,
)
from cvp_mcp.grpc.utils import _is_lab_device, createConnection
from cvp_mcp.rate_limit import rate_limited_tool
from cvp_mcp.tool_access import tool_enabled
from cvp_mcp.transport_security_config import build_transport_security
from cvp_mcp.write_access import writes_enabled

CVP_TRANSPORT = "grpc"

logging.basicConfig(
    level=logging.INFO,  # Minimum log level
    format="%(asctime)s - %(levelname)s - %(message)s",  # Log message format
)


_NOISY_ACCESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"GET / HTTP/1\.1" 404 Not Found'),
    re.compile(r'"GET /lldp/nodes HTTP/1\.1" 404 Not Found'),
    re.compile(r'"GET /v1/topology HTTP/1\.1" 404 Not Found'),
    re.compile(
        r'"GET /\.(well-known/oauth-protected-resource(?:/mcp)?) HTTP/1\.1" 404 Not Found'
    ),
)

_NOISY_MESSAGE_SUBSTRINGS: tuple[str, ...] = (
    "Error handling POST request",
    "starlette.requests.ClientDisconnect",
    "aborting with incomplete response",
    "reading: context canceled",
    "Stateless session crashed",
    "ClosedResourceError",
)


def _is_noise_record(record: logging.LogRecord) -> bool:
    """
    Filter known noisy disconnect/probe logs from MCP streamable-http usage.

    Keep real backend/tool failures visible while dropping high-volume
    disconnect churn and endpoint-probe 404 spam.
    """
    msg = record.getMessage()
    if any(s in msg for s in _NOISY_MESSAGE_SUBSTRINGS):
        return True
    if record.name == "uvicorn.access":
        return any(p.search(msg) for p in _NOISY_ACCESS_PATTERNS)
    return False


class _NoiseSuppressFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _is_noise_record(record)


def _install_noise_filters() -> None:
    filt = _NoiseSuppressFilter()
    # Root handlers catch most output from this app.
    for handler in logging.getLogger().handlers:
        handler.addFilter(filt)
    # Add explicit logger filters for third-party emitters.
    for name in (
        "uvicorn.access",
        "uvicorn.error",
        "mcp.server.streamable_http",
        "mcp",
        "starlette",
    ):
        logging.getLogger(name).addFilter(filt)


_install_noise_filters()

logging.info("Starting the FastMCP server...")

# Initialize FastMCP server (bind host updated from CLI in main() for HTTP transport)
_mcp_http_host = os.environ.get("CVP_MCP_HTTP_HOST", "127.0.0.1")
mcp = FastMCP(
    name="CVP MCP Server",
    host=_mcp_http_host,
    stateless_http=True,
    log_level="WARNING",
    transport_security=build_transport_security(),
)


# async function to return creds
def get_env_vars():
    return env_datadict_from_os()


def _endpoint_search_queries(search_term: str) -> list[str]:
    """Try resolved IP first, then the raw string (CVP may index by hostname or IP)."""
    st = (search_term or "").strip()
    if not st:
        return [st]
    resolved = resolve_endpoint_query(st)
    if resolved != st:
        return [resolved, st]
    return [st]


def _resolve_device_serial(
    datadict: dict,
    device_id: str,
    *,
    channel: grpc.Channel | None = None,
) -> tuple[str | None, dict | None, list[str], list[dict]]:
    return resolve_device_to_serial(datadict, device_id, channel=channel)


def _device_resolution_failure_envelope(
    device_id: str,
    data_source: str,
    warnings: list[str] | None = None,
    candidates: list | None = None,
) -> dict:
    inp = (device_id or "").strip()
    warns = list(warnings or [])
    ambiguous = "device_ambiguous" in warns
    primary = "device_ambiguous" if ambiguous else "device_not_found"
    if primary not in warns:
        warns.insert(0, primary)
    candidate_rows = summarize_inventory_candidates(candidates)
    hint = (
        "Multiple inventory devices match this shorthand. Pick one serial_number "
        "from candidates and re-call with device_id=<serial_number>."
        if ambiguous and candidate_rows
        else (
            "No device matched. Run search_cvp_inventory or get_cvp_all_inventory "
            "first, then pass device_id as the CloudVision serial_number "
            "(not a model name like 720xp)."
        )
    )
    if candidate_rows and not ambiguous:
        hint = (
            "No exact device match. Partial inventory matches are listed in "
            "candidates — re-call with device_id=<serial_number>."
        )
    obj: dict = {
        "device_id_input": inp,
        "hint": hint,
        "next_step": "search_cvp_inventory(query) -> get_cvp_lldp_neighbors(serial_number)",
    }
    if candidate_rows:
        obj["candidates"] = candidate_rows
    return tool_envelope(
        device_id=inp or None,
        data_source=data_source,
        coverage="none",
        items=[],
        warnings=warns,
        obj=obj,
    )


def _device_not_found_envelope(
    device_id: str,
    data_source: str,
    warnings: list[str] | None = None,
    candidates: list | None = None,
) -> dict:
    return _device_resolution_failure_envelope(
        device_id, data_source, warnings, candidates
    )


def _attach_device_resolution(
    result: dict,
    device_id_input: str,
    serial: str,
    resolution_warnings: list[str],
) -> dict:
    if not isinstance(result, dict):
        return result
    inp = (device_id_input or "").strip()
    result["device_id"] = serial
    if inp and inp != serial:
        obj = dict(result.get("object") or {})
        obj["device_id_input"] = inp
        obj["device_id_resolved"] = serial
        result["object"] = obj
    if resolution_warnings:
        result["warnings"] = list(result.get("warnings") or []) + resolution_warnings
    return result


# ===================================================
# Inventory Based Tools
# ===================================================


@mcp.tool()
def get_cvp_one_device(device_id) -> str:
    """
    Prints out information about a single device in CVP
    For one switch it gets the serial number, system mac address,
    hostname, EOS version, streaming status, device type, hardware revision,
    FQDN, domain name, and model

    ``device_id``: Accepts CloudVision serial number (canonical for device datasets),
    hostname, FQDN, or system MAC; resolved to serial before querying.
    """
    datadict = get_env_vars()
    logging.debug(f"CVP Get One Device Tool - {device_id}")
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, device, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        err = {
                            "error": (
                                "device_ambiguous"
                                if "device_ambiguous" in warns
                                else "device_not_found"
                            ),
                            "device_id_input": (device_id or "").strip(),
                            "warnings": warns,
                        }
                        rows = summarize_inventory_candidates(candidates)
                        if rows:
                            err["candidates"] = rows
                        return json.dumps(err, indent=2)
                    if device is None:
                        device = grpc_one_inventory_serial(channel, serial)
            case "http":
                device = ""
    except Exception as e:
        logging.error(f"Error fetching device {device_id}: {e}")
        return '{"error": "Device fetch failed"}'
    logging.debug(json.dumps(device, indent=2))
    return json.dumps(device, indent=2)


@mcp.tool()
@rate_limited_tool("get_cvp_all_inventory")
def get_cvp_all_inventory() -> dict:
    """
    Grabs switches and devices from CloudVision (CVP).

    WiFi access points (device_type Access Point) are omitted — they are not
    EOS switches and bulk config tooling does not apply.

    Per device: serial, system MAC, hostname, EOS version, streaming status,
    device type, hardware revision, FQDN, domain name, model.
    """
    datadict = get_env_vars()
    all_devices = {}
    logging.info("CVP Get all Tool")
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                all_active, all_inactive = grpc_all_inventory(
                    channel, exclude_access_points=True
                )
                all_devices["streaming_active"] = all_active
                all_devices["streaming_inactive"] = all_inactive
        case "http":
            return {"error": "grpc_only"}
    logging.debug(json.dumps(all_devices))
    # return(json.dumps(all_devices, indent=2))
    return all_devices


@mcp.tool()
@rate_limited_tool("search_cvp_inventory")
def search_cvp_inventory(query: str) -> dict:
    """
    Search CloudVision inventory by hostname, model substring, serial, FQDN, or MAC fragment.

    Use this **before** ``get_cvp_lldp_neighbors`` when the user names a model family
    (e.g. ``720xp``) or partial hostname. Each match includes ``serial_number`` — pass
    that value as ``device_id`` on per-device tools.

    Requires at least three characters in ``query``.
    """
    datadict = get_env_vars()
    q = (query or "").strip()
    if len(q) < 3:
        return tool_envelope(
            data_source="inventory:search",
            coverage="none",
            items=[],
            warnings=["query_too_short"],
            obj={
                "query": q,
                "hint": "Provide at least 3 characters (e.g. 720xp, spine-1).",
            },
        )
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    matches, warns = search_inventory_candidates(
                        datadict, q, channel=channel
                    )
                    items = summarize_inventory_candidates(matches)
                    return tool_envelope(
                        data_source="inventory:search",
                        coverage="full" if items else "none",
                        items=items,
                        warnings=warns,
                        obj={
                            "query": q,
                            "match_count": len(items),
                            "next_step": "get_cvp_lldp_neighbors(device_id=<serial_number>)",
                        },
                    )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "inventory_search_failed", log_exc=e, context="search_cvp_inventory"
        )


# ===================================================
# Bug Based Tools
# ===================================================


@mcp.tool()
def get_cvp_all_bugs() -> dict:
    """
    Prints out all bug exposures
    For each bug, it gets: device serial number, list of bug IDs,
    list of CVE IDs, bug count, cve count and the highest exposure to bugs and CVEs.
    This will also get switches based on the found serial numbers in the bug report,
    It will get  the serial number, system mac address,
    hostname, EOS version, streaming status, device type, hardware revision,
    FQDN, domain name, and model
    """
    all_data = {}
    all_devices = []
    all_bug_ids = []
    # all_bug_info = {}
    datadict = get_env_vars()
    logging.info("CVP Get all Bugs Tool")
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                all_bugs = grpc_all_bug_exposure(channel)
                if all_bugs:
                    for bug in all_bugs:
                        for id in bug["bug_ids"]:
                            if id not in all_bug_ids:
                                all_bug_ids.append(id)
                        device = grpc_one_inventory_serial(
                            channel, bug["serial_number"]
                        )
                        if device:
                            all_devices.append(device)
        case "http":
            logging.info("HTTP Transport to get all bugs")
            all_bugs = {}
    logging.debug(json.dumps(all_bugs))
    # Grab information about each bug
    all_bug_info = conn_get_info_bugs(datadict, all_bug_ids)
    all_data["bug_info"] = all_bug_info
    all_data["bugs"] = all_bugs
    all_data["devices"] = all_devices
    try:
        logging.debug(f"Bug Data: {type(all_data['bug_info'])} {all_data['bug_info']}")
        logging.debug(f"All data: {json.dumps(all_data)}")
    except Exception as y:
        logging.error(f"Error processing bug data: {y}")
        return '{"error": "Bug data processing failed"}'
    # return(json.dumps(all_data, indent=2))
    return all_data


# ===================================================
# Connectivity Monitor Based Tools
# ===================================================


@mcp.tool()
def get_cvp_all_connectivity_probes() -> dict:
    """
    Gets all connectivity monitor probes from CVP
    Displays latency, jitter, http response time and packet loss
    """
    datadict = get_env_vars()
    all_devices = {}
    all_data = {}
    logging.info("CVP Get all Probes")
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                all_probes = grpc_all_probe_status(channel)
                # Gather information about the source switches for analytics
                for probe in all_probes:
                    serial_number = probe["serial_number"]
                    if serial_number not in all_devices.keys():
                        all_devices[serial_number] = grpc_one_inventory_serial(
                            channel, serial_number
                        )
        case "http":
            logging.info("CVP HTTP Request for all devices")
            all_devices = ""
    all_data["devices"] = all_devices
    all_data["probes"] = all_probes
    logging.debug(json.dumps(all_data))
    # return(json.dumps(all_data, indent=2))
    return all_data


@mcp.tool()
def get_cvp_one_connectivity_probe(
    serial_number: str | None = None,
    endpoint: str | None = None,
    vrf: str | None = None,
    source_interface: str | None = None,
) -> str:
    """
    Prints out information about a single device in CVP
    Displays latency, jitter, http response time and packet loss.
    If ``endpoint`` is a hostname/FQDN, it is resolved to an IP (DNS) before
    querying probe stats, matching how OPNsense MCP resolves names before API calls.
    """
    datadict = get_env_vars()
    logging.debug("CVP Get One Probe State")
    all_data = {}
    all_devices = {}
    probe_hosts: list[str] = [""]
    if endpoint and endpoint.strip():
        raw_ep = endpoint.strip()
        resolved_ep = resolve_endpoint_query(raw_ep)
        probe_hosts = [resolved_ep, raw_ep] if resolved_ep != raw_ep else [raw_ep]
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
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
                    for _probe in probes:
                        logging.debug(f"MON S/n: {_probe['serial_number']}")
                        serial_number = _probe["serial_number"]
                        if serial_number not in all_devices.keys():
                            all_devices[serial_number] = grpc_one_inventory_serial(
                                channel, serial_number
                            )
                    all_data["probes"] = probes
                    all_data["devices"] = all_devices
            case "http":
                pass
    except Exception as e:
        logging.error(f"Error in lifecycle flow: {e}")
        return '{"error": "Lifecycle fetch failed"}'
    logging.debug(json.dumps(all_data, indent=2))
    return json.dumps(all_data, indent=2)


# ===================================================
# Device Lifecycle Based Tools
# ===================================================


@mcp.tool()
def get_cvp_all_device_lifecycle() -> dict:
    """
    Gets all device lifecycle from CVP
    Displays information about switch software end of life,
    and hardware end of support, end of rma, end of sale and end of life.
    """
    datadict = get_env_vars()
    all_devices = {}
    all_data = {}
    logging.info("CVP Get all Device Lifecycle")
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                all_lifecycle = grpc_all_device_lifecycle(channel)
                # Gather information about the source switches for analytics
                for _lifecycle in all_lifecycle:
                    serial_number = _lifecycle["serial_number"]
                    if serial_number not in all_devices.keys():
                        all_devices[serial_number] = grpc_one_inventory_serial(
                            channel, serial_number
                        )
        case "http":
            logging.info("CVP HTTP Request for all devices")
            all_devices = ""
    all_data["devices"] = all_devices
    all_data["lifecycle"] = all_lifecycle
    logging.debug(json.dumps(all_data))
    # return(json.dumps(all_data, indent=2))
    return all_data


# ===================================================
# Endpoint Location  Based Tools
# ===================================================


@mcp.tool()
def get_cvp_endpoint_location(search_term: str) -> dict:
    """
    Gets all endpoint locations from CVP for a user device, or connected endpoint
     based on a query of MAC, IP or hostname
    Displays information about endpoint device location, ip address
    mac address. This will also convert the switch serial number hostname and get information
    of the switch.
    Hostname/FQDN inputs are resolved via DNS to an IP before querying CVP when needed
    (same idea as OPNsense MCP resolve-then-query); if the IP lookup returns nothing,
    the original search term is tried as a fallback.
    """
    datadict = get_env_vars()
    all_devices = {}
    all_data = {}
    all_endpoints = []
    logging.info("CVP Get Endpoint Location")
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                for q in _endpoint_search_queries(search_term):
                    all_endpoints = grpc_one_endpoint_location(channel, q)
                    if all_endpoints:
                        break
                # Gather information about the source switches for analytics
                for _endpoint in all_endpoints:
                    logging.debug(f"END FOR: {_endpoint} - {_endpoint.keys()}")
                    for _device in _endpoint["location_list"]:
                        serial_number = _device["device_id"]["value"]
                        if serial_number not in all_devices.keys():
                            all_devices[serial_number] = grpc_one_inventory_serial(
                                channel, serial_number
                            )
        case "http":
            logging.info("CVP HTTP Request for all devices")
            all_devices = ""
    all_data["devices"] = all_devices
    all_data["endpoints"] = all_endpoints
    logging.debug(json.dumps(all_data))
    # return(json.dumps(all_data, indent=2))
    return all_data


@mcp.tool()
def get_cvp_all_endpoint_locations(
    device_serial_allowlist: list[str] | None = None,
    max_search_keys: int | None = None,
) -> dict:
    """LLDP-seeded endpoint locations from CVP via GetSome/GetOne lookups.

    Seeds search keys from LLDP neighbors on switches, then resolves endpoint
    locations. Returns endpoints with MAC, IP, hostname, and their switch
    attachment locations (device + interface + VLAN).

    Optional ``device_serial_allowlist`` limits LLDP seeding to those switch
    serials. ``max_search_keys`` caps how many deduped keys are looked up.
    """
    datadict = get_env_vars()
    cred_miss = cvp_credentials_missing_reasons(datadict)
    if cred_miss:
        return {"error": "missing_cloudvision_credentials", "warnings": cred_miss}
    all_devices = {}
    logging.info("CVP Get All Endpoint Locations")
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            warnings: list[str] = []
            try:
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    seed = seed_endpoint_search_keys(
                        datadict,
                        channel,
                        device_serials=device_serial_allowlist,
                    )
                    warnings.extend(seed["warnings"])
                    search_keys = seed["search_keys"]
                    if (
                        max_search_keys is not None
                        and len(search_keys) > max_search_keys
                    ):
                        truncated = len(search_keys) - max_search_keys
                        warnings.append(f"search_keys_truncated:{truncated}")
                        search_keys = search_keys[:max_search_keys]
                    lookup = grpc_endpoints_for_search_keys(channel, search_keys)
                    warnings.extend(lookup["warnings"])
                    all_endpoints = lookup["endpoints"]
                    for _endpoint in all_endpoints:
                        for _device in _endpoint["location_list"]:
                            serial_number = _device["device_id"]["value"]
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
            except Exception as e:
                logging.error("Endpoint location pipeline failed: %s", e)
                return {"error": f"seed_failed:{e}", "warnings": warnings}
        case "http":
            logging.info("CVP HTTP Request for all devices")
            return {"error": "grpc_only", "warnings": []}


@mcp.tool()
def get_cvp_endpoint_locations_filtered(
    device_id: str | None = None,
    interface: str | None = None,
    vlan_id: int | None = None,
) -> dict:
    """Filters LLDP-seeded endpoint locations by switch, interface, or VLAN.

    ``device_id``: optional; serial, hostname, FQDN, or system MAC (resolved to serial).
    Provide at least one filter; results are narrowed client-side after GetSome/GetOne lookup.
    """
    datadict = get_env_vars()
    cred_miss = cvp_credentials_missing_reasons(datadict)
    if cred_miss:
        return {"error": "missing_cloudvision_credentials", "warnings": cred_miss}
    all_devices = {}
    logging.info(
        f"CVP Get Filtered Endpoint Locations: device={device_id} intf={interface} vlan={vlan_id}"
    )
    if device_id is None and interface is None and vlan_id is None:
        logging.warning(
            "get_cvp_endpoint_locations_filtered called with no filters; "
            "this may return a large result set."
        )
    filter_device_id = device_id
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            warnings: list[str] = []
            try:
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    if filter_device_id:
                        serial, _info, warns, candidates = _resolve_device_serial(
                            datadict, filter_device_id, channel=channel
                        )
                        if not serial:
                            err = {
                                "error": (
                                    "device_ambiguous"
                                    if "device_ambiguous" in warns
                                    else "device_not_found"
                                ),
                                "device_id_input": (filter_device_id or "").strip(),
                                "warnings": warns,
                            }
                            rows = summarize_inventory_candidates(candidates)
                            if rows:
                                err["candidates"] = rows
                            return err
                        filter_device_id = serial
                    seed = seed_endpoint_search_keys(
                        datadict,
                        channel,
                        device_serials=[filter_device_id] if filter_device_id else None,
                    )
                    warnings.extend(seed["warnings"])
                    lookup = grpc_endpoints_for_search_keys(
                        channel, seed["search_keys"]
                    )
                    warnings.extend(lookup["warnings"])
                    all_endpoints = [
                        ep
                        for ep in lookup["endpoints"]
                        if endpoint_location_matches_filters(
                            ep,
                            device_id=filter_device_id,
                            interface=interface,
                            vlan_id=vlan_id,
                        )
                    ]
                    for _endpoint in all_endpoints:
                        for _device in _endpoint["location_list"]:
                            serial_number = _device["device_id"]["value"]
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
            except Exception as e:
                logging.error("Filtered endpoint location pipeline failed: %s", e)
                return {"error": f"seed_failed:{e}", "warnings": warnings}
        case "http":
            logging.info("CVP HTTP Request for all devices")
            return {"error": "grpc_only", "warnings": []}


# ===================================================
# Capability (installed Resource API packages)
# ===================================================


@mcp.tool()
def get_cvp_probe_arista_apis() -> dict:
    """Lists installed ``arista.*.v1`` Python API packages (Resource API clients bundled with cloudvision)."""
    return {"packages": probe_arista_v1_packages()}


# ===================================================
# Config / interfaces / VLAN / IP (hybrid Resource API + Connector)
# ===================================================


@mcp.tool()
@tool_enabled("get_cvp_device_config")
def get_cvp_device_config(device_id: str, include_running_config: bool = False) -> dict:
    """Device config summary (URIs, sync metadata) from configstatus API; optional running-config text via URI fetch.

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial internally).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    return grpc_get_device_config(
                        channel,
                        datadict,
                        device_id,
                        include_running_config=include_running_config,
                    )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "device_config_failed", log_exc=e, context="get_cvp_device_config"
        )


@mcp.tool()
def get_cvp_interfaces(device_id: str) -> dict:
    """Interface catalog (admin/oper, speed, MTU, description, counters) via Sysdb paths on the device dataset.

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/interface",
                            warns,
                            candidates,
                        )
                    result = grpc_get_interfaces(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "interfaces_failed", log_exc=e, context="get_cvp_interfaces"
        )


@mcp.tool()
def get_cvp_vlans(device_id: str) -> dict:
    """VLAN and switchport-related rows from Sysdb bridging paths (best-effort across EOS versions).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/bridging",
                            warns,
                            candidates,
                        )
                    result = grpc_get_vlans(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error("vlans_failed", log_exc=e, context="get_cvp_vlans")


@mcp.tool()
def get_cvp_ip_interfaces(device_id: str) -> dict:
    """L3 addresses per interface from Sysdb IP paths (best-effort parse).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/ip",
                            warns,
                            candidates,
                        )
                    result = grpc_get_ip_interfaces(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "ip_interfaces_failed", log_exc=e, context="get_cvp_ip_interfaces"
        )


# ===================================================
# Events (Resource API) + routing (Connector)
# ===================================================


@mcp.tool()
def get_cvp_events(
    severity: str | None = None,
    event_type: str | None = None,
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = 100,
) -> dict:
    """List CVP events with structured filters (severity, event_type, optional device_id substring, ISO time bounds)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    return grpc_get_cvp_events(
                        channel,
                        severity=severity,
                        event_type=event_type,
                        device_id=device_id,
                        start_time=start_time,
                        end_time=end_time,
                        limit=limit,
                    )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error("events_failed", log_exc=e, context="get_cvp_events")


@mcp.tool()
@rate_limited_tool("search_cvp_events")
def search_cvp_events(
    query: str,
    severity: str | None = None,
    event_type: str | None = None,
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int | None = 50,
) -> dict:
    """Search event title/description/type (client-side match) after optional structured filters."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    return grpc_search_cvp_events(
                        channel,
                        query,
                        severity=severity,
                        event_type=event_type,
                        device_id=device_id,
                        start_time=start_time,
                        end_time=end_time,
                        limit=limit,
                    )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "search_events_failed", log_exc=e, context="search_cvp_events"
        )


@mcp.tool()
@tool_enabled("get_cvp_bgp_status")
def get_cvp_bgp_status(device_id: str) -> dict:
    """BGP operational snapshot from Sysdb/Smash routing paths (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/routing/bgp",
                            warns,
                            candidates,
                        )
                    result = grpc_get_bgp_status(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "bgp_status_failed", log_exc=e, context="get_cvp_bgp_status"
        )


@mcp.tool()
@tool_enabled("get_cvp_routes")
def get_cvp_routes(device_id: str, vrf: str = "default") -> dict:
    """Active route-like RIB entries from Sysdb routing status (best-effort; vrf labels path selection).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/routing",
                            warns,
                            candidates,
                        )
                    result = grpc_get_routes(datadict, serial, vrf=vrf)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error("routes_failed", log_exc=e, context="get_cvp_routes")


@mcp.tool()
def get_cvp_lldp_neighbors(
    device_id: str,
    port_name: str = "",
    remote_neighbor_key: str = "",
    include_lab_devices: bool = False,
) -> dict:
    """LLDP neighbor table from EOS Sysdb via Connector (best-effort; requires LLDP enabled on device).

    **Agent workflow:** When the user names a switch by model or nickname (e.g. ``720xp``), call
    ``search_cvp_inventory`` or ``get_cvp_all_inventory`` first, then pass ``device_id`` as the
    CloudVision **serial_number** — not a model name. Model shorthands that match multiple devices
    return ``device_ambiguous`` with a ``candidates`` list instead of querying Connector.

    ``device_id``: CloudVision serial (preferred), or hostname, FQDN, or system MAC.

    By default, virtual/lab devices (vEOS, cEOS) and inactive devices are excluded.
    Pass ``include_lab_devices=True`` to query a virtual device explicitly.

    If Telemetry Browser shows a path like ``…/portStatus/Ethernet6/remoteSystem/1`` but wildcard
    queries return nothing, pass ``port_name`` (e.g. ``Ethernet6``) and optionally ``remote_neighbor_key`` (e.g. ``1``).

    When ``port_name`` is omitted, the server sweeps all candidate Ethernet ports (oper-up list
    when available, otherwise ``Ethernet1..N`` from the device model) and returns every LLDP
    neighbor — not just the first port that returns data.
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, device_info, res_warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            LLDP_DATA_SOURCE,
                            res_warns,
                            candidates,
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
                                "hint": "Device is a virtual/lab EOS instance (vEOS or cEOS). "
                                "Pass include_lab_devices=True to query it explicitly.",
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
                    return _attach_device_resolution(
                        result, device_id, serial, res_warns
                    )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "lldp_neighbors_failed", log_exc=e, context="get_cvp_lldp_neighbors"
        )


@mcp.tool()
@rate_limited_tool("map_cvp_network_topology")
def map_cvp_network_topology(
    output_format: str = "json",
    include_inactive_devices: bool = False,
    max_ethernet_ports: int | None = None,
    device_serial_allowlist: str = "",
    topology_name: str = "cvp-lldp",
    topology_node_scope: str = "full_inventory",
    lldp_port_source: str = "auto",
    include_lab_devices: bool = False,
) -> dict:
    """
    Discover LLDP adjacencies across CVP inventory (per-device Ethernet sweep) and export topology.

    ``output_format``: ``json`` (structured ``topology`` + ``text``), ``mermaid``, GitHub ``table`` markdown,
    or ``containerlab`` (YAML lab spec — **images are placeholders**; edit before deploy).

    ``device_serial_allowlist``: comma-separated serials to scan (empty = all inventory devices).
    ``max_ethernet_ports``: cap ports per device (default: inferred from model).
    ``topology_node_scope``: ``full_inventory`` (every CVP device as a node) or ``connected`` (only devices with LLDP edges).
    ``lldp_port_source``:
    - ``auto`` probes Sysdb oper-up physical ports first, then falls back to ``Ethernet1..N``.
    - ``oper_up_only`` probes only oper-up physical ports (no fallback sweep).
    - ``full_range`` always uses the legacy ``Ethernet1..N`` sweep.
    ``include_lab_devices``: include virtual/lab EOS devices (vEOS, cEOS) in the scan (default: False).

    Agent guidance for reliable mapping in flaky sessions:
    - Run batched calls with ``device_serial_allowlist`` (roughly 1-5 serials per call).
    - Set ``max_ethernet_ports`` to a realistic cap.
    - Merge outputs across batches for full-fabric topology.
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                allowed = {"json", "mermaid", "table", "containerlab"}
                fmt = (output_format or "json").strip().lower()
                if fmt not in allowed:
                    return {
                        "error": f"output_format must be one of {sorted(allowed)}",
                        "output_format": output_format,
                    }
                return grpc_map_network_topology(
                    datadict,
                    output_format=fmt,
                    include_inactive_devices=include_inactive_devices,
                    max_ethernet_ports=max_ethernet_ports,
                    device_serial_allowlist=device_serial_allowlist,
                    topology_name=topology_name,
                    topology_node_scope=topology_node_scope,
                    lldp_port_source=lldp_port_source,
                    include_lab_devices=include_lab_devices,
                )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "network_topology_failed", log_exc=e, context="map_cvp_network_topology"
        )


# ===================================================
# Features / overlay / system health (Connector)
# ===================================================


@mcp.tool()
def get_cvp_features(device_id: str) -> dict:
    """Enabled-feature-related Sysdb snapshots (best-effort; coverage often partial).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/feature",
                            warns,
                            candidates,
                        )
                    result = grpc_get_features(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error("features_failed", log_exc=e, context="get_cvp_features")


@mcp.tool()
def get_cvp_evpn(device_id: str) -> dict:
    """EVPN-related Sysdb subtree (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/evpn",
                            warns,
                            candidates,
                        )
                    result = grpc_get_evpn(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error("evpn_failed", log_exc=e, context="get_cvp_evpn")


@mcp.tool()
def get_cvp_vxlan(device_id: str) -> dict:
    """VxLAN-related Sysdb subtrees (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/vxlan",
                            warns,
                            candidates,
                        )
                    result = grpc_get_vxlan(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error("vxlan_failed", log_exc=e, context="get_cvp_vxlan")


@mcp.tool()
def get_cvp_system_health(device_id: str) -> dict:
    """System version/status and environment/platform sensors (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    serial, _info, warns, candidates = _resolve_device_serial(
                        datadict, device_id, channel=channel
                    )
                    if not serial:
                        return _device_not_found_envelope(
                            device_id,
                            "connector:device:Sysdb/sys+environment",
                            warns,
                            candidates,
                        )
                    result = grpc_get_system_health(datadict, serial)
                    return _attach_device_resolution(result, device_id, serial, warns)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        return client_error(
            "system_health_failed", log_exc=e, context="get_cvp_system_health"
        )


# ===================================================
# Flow Data Tools
# ===================================================


@mcp.tool()
def get_cvp_flow_data(
    device_id: str | None = None,
    flow_index: int | None = None,
) -> dict:
    """Retrieves Clover flow records from CloudVision analytics (/Clover/flows/v1/path/...).
    flow_index: optional integer path suffix (e.g. 0 for .../path/0); omit to query the parent path.
    device_id: optional serial, hostname, FQDN, or system MAC (resolved to serial); keeps only
    records whose node matches that device.
    Returns flow records with src/dst IPs, ports, protocol, bytes/packets, and interfaces.
    """
    datadict = get_env_vars()
    logging.info(f"CVP Get Flow Data: device={device_id} flow_index={flow_index}")
    filter_serial = device_id
    resolution: dict[str, str] = {}
    if filter_serial:
        serial, _info, warns, candidates = _resolve_device_serial(
            datadict, filter_serial
        )
        if not serial:
            err = {
                "error": (
                    "device_ambiguous"
                    if "device_ambiguous" in warns
                    else "device_not_found"
                ),
                "device_id_input": (filter_serial or "").strip(),
                "warnings": warns,
                "flows": [],
            }
            rows = summarize_inventory_candidates(candidates)
            if rows:
                err["candidates"] = rows
            return err
        if serial != (filter_serial or "").strip():
            resolution = {
                "device_id_input": (filter_serial or "").strip(),
                "device_id_resolved": serial,
            }
        filter_serial = serial
    flows = conn_get_flow_data(datadict, filter_serial, flow_index)
    out: dict = {"flows": flows}
    if resolution:
        out.update(resolution)
    return out


# ===================================================
# Studios / workspaces / designed-config (Phase 1 reads)
# ===================================================


@mcp.tool()
@tool_enabled("get_cvp_studios")
def get_cvp_studios() -> dict:
    """List CloudVision studios (ids, names, flags). Omits large template bodies."""
    datadict = get_env_vars()
    try:
        return studios_get_studios(datadict)
    except Exception as e:
        return client_error("studios_failed", log_exc=e, context="get_cvp_studios")


@mcp.tool()
@tool_enabled("get_cvp_studio")
def get_cvp_studio(
    studio_id: str, workspace_id: str | None = None, body: bool = False
) -> dict:
    """One studio by id. Default workspace is mainline (empty string). Set body=True for full Mako."""
    datadict = get_env_vars()
    try:
        return studios_get_studio(datadict, studio_id, workspace_id, body=body)
    except Exception as e:
        return client_error("studio_failed", log_exc=e, context="get_cvp_studio")


@mcp.tool()
@tool_enabled("get_cvp_studio_inputs")
def get_cvp_studio_inputs(studio_id: str, workspace_id: str | None = None) -> dict:
    """Current studio input document(s) for a studio/workspace (mainline default)."""
    datadict = get_env_vars()
    try:
        return studios_get_studio_inputs(datadict, studio_id, workspace_id)
    except Exception as e:
        return client_error(
            "studio_inputs_failed", log_exc=e, context="get_cvp_studio_inputs"
        )


@mcp.tool()
@tool_enabled("search_cvp_studio_templates")
def search_cvp_studio_templates(
    pattern: str, include_input_schema: bool = True, max_hits: int = 100
) -> dict:
    """Search studio templates/schemas for a literal substring; returns JSON paths of hits."""
    datadict = get_env_vars()
    try:
        return studios_search_templates(
            datadict,
            pattern,
            include_input_schema=include_input_schema,
            max_hits=max_hits,
        )
    except Exception as e:
        return client_error(
            "studio_search_failed", log_exc=e, context="search_cvp_studio_templates"
        )


@mcp.tool()
@tool_enabled("get_cvp_workspaces")
def get_cvp_workspaces() -> dict:
    """List CloudVision workspaces (state, cc ids, build/response ids)."""
    datadict = get_env_vars()
    try:
        return studios_get_workspaces(datadict)
    except Exception as e:
        return client_error(
            "workspaces_failed", log_exc=e, context="get_cvp_workspaces"
        )


@mcp.tool()
@tool_enabled("get_cvp_workspace")
def get_cvp_workspace(workspace_id: str) -> dict:
    """One workspace by id, including responses map for build/submit polling."""
    datadict = get_env_vars()
    try:
        return studios_get_workspace(datadict, workspace_id)
    except Exception as e:
        return client_error("workspace_failed", log_exc=e, context="get_cvp_workspace")


@mcp.tool()
@tool_enabled("get_cvp_workspace_build")
def get_cvp_workspace_build(workspace_id: str, build_id: str) -> dict:
    """Workspace build status (BUILD_STATE_*) for poll-after-build workflows."""
    datadict = get_env_vars()
    try:
        return studios_get_workspace_build(datadict, workspace_id, build_id)
    except Exception as e:
        return client_error(
            "workspace_build_failed", log_exc=e, context="get_cvp_workspace_build"
        )


@mcp.tool()
@tool_enabled("get_cvp_designed_config")
def get_cvp_designed_config(device_id: str) -> dict:
    """Designed-config studio sources for a device (compliance GetConfig DESIGNED_CONFIG).

    Prefer device serial; hostname is resolved via inventory when possible.
    """
    datadict = get_env_vars()
    try:
        return studios_get_designed_config(datadict, device_id)
    except Exception as e:
        return client_error(
            "designed_config_failed", log_exc=e, context="get_cvp_designed_config"
        )


@mcp.tool()
@tool_enabled("get_cvp_studio_assigned_tags")
def get_cvp_studio_assigned_tags(
    studio_id: str, workspace_id: str | None = None
) -> dict:
    """Tag query assigned to a studio. Default workspace is mainline (empty string)."""
    datadict = get_env_vars()
    try:
        return studio_tags_get_assigned(datadict, studio_id, workspace_id)
    except Exception as e:
        return client_error(
            "studio_assigned_tags_failed",
            log_exc=e,
            context="get_cvp_studio_assigned_tags",
        )


# ===================================================
# Studios Phase 2.0 writes (registered only when ALLOW_WRITES=1)
# ===================================================


if writes_enabled():

    @mcp.tool()
    @tool_enabled("create_cvp_workspace")
    def create_cvp_workspace(
        workspace_id: str,
        display_name: str,
        description: str = "",
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Create a draft workspace. Dry-run unless confirm=True and preview_token matches."""
        datadict = get_env_vars()
        try:
            return studios_create_workspace(
                datadict,
                workspace_id,
                display_name,
                description=description,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "create_workspace_failed",
                log_exc=e,
                context="create_cvp_workspace",
            )

    @mcp.tool()
    @tool_enabled("delete_cvp_workspace")
    def delete_cvp_workspace(
        workspace_id: str,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Delete a pending draft workspace. Dry-run unless confirm=True and preview_token matches."""
        datadict = get_env_vars()
        try:
            return studios_delete_workspace(
                datadict,
                workspace_id,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "delete_workspace_failed",
                log_exc=e,
                context="delete_cvp_workspace",
            )

    @mcp.tool()
    @tool_enabled("build_cvp_workspace")
    def build_cvp_workspace(
        workspace_id: str,
        request_id: str | None = None,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Start a workspace build (REQUEST_START_BUILD). Poll with get_cvp_workspace_build."""
        datadict = get_env_vars()
        try:
            return studios_build_workspace(
                datadict,
                workspace_id,
                request_id=request_id,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "build_workspace_failed",
                log_exc=e,
                context="build_cvp_workspace",
            )

    @mcp.tool()
    @tool_enabled("set_cvp_access_interface_description")
    def set_cvp_access_interface_description(
        workspace_id: str,
        device_id: str,
        interface: str,
        expected_current_description: str,
        new_description: str,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Compare-and-set one access-studio port description. Does not submit or shut ports."""
        datadict = get_env_vars()
        try:
            return studios_set_access_description(
                datadict,
                workspace_id,
                device_id,
                interface,
                expected_current_description,
                new_description,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "set_access_description_failed",
                log_exc=e,
                context="set_cvp_access_interface_description",
            )

    @mcp.tool()
    @tool_enabled("assign_cvp_studio_tags")
    def assign_cvp_studio_tags(
        studio_id: str,
        workspace_id: str,
        query: str,
        expected_current_query: str,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Compare-and-set a studio's tag query. Does not submit the workspace."""
        datadict = get_env_vars()
        try:
            return studio_tags_assign(
                datadict,
                studio_id,
                workspace_id,
                query,
                expected_current_query,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "assign_studio_tags_failed",
                log_exc=e,
                context="assign_cvp_studio_tags",
            )

    @mcp.tool()
    @tool_enabled("set_cvp_studio_inputs")
    def set_cvp_studio_inputs(
        studio_id: str,
        workspace_id: str,
        path_values: list[str],
        inputs: Any,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Set studio inputs at a path. Dry-run unless confirm=True and preview_token matches."""
        datadict = get_env_vars()
        try:
            return studio_inputs_set(
                datadict,
                studio_id,
                workspace_id,
                path_values,
                inputs,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "set_studio_inputs_failed",
                log_exc=e,
                context="set_cvp_studio_inputs",
            )

    @mcp.tool()
    @tool_enabled("create_cvp_studio")
    def create_cvp_studio(
        workspace_id: str,
        studio_id: str,
        display_name: str,
        template_body: str = "",
        description: str = "",
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Create a studio in a workspace. Dry-run unless confirm=True and preview_token matches."""
        datadict = get_env_vars()
        try:
            return studio_crud_create(
                datadict,
                workspace_id,
                studio_id,
                display_name,
                template_body=template_body,
                description=description,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "create_studio_failed",
                log_exc=e,
                context="create_cvp_studio",
            )

    @mcp.tool()
    @tool_enabled("delete_cvp_studio")
    def delete_cvp_studio(
        workspace_id: str,
        studio_id: str,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Delete a studio in a workspace. Dry-run unless confirm=True and preview_token matches."""
        datadict = get_env_vars()
        try:
            return studio_crud_delete(
                datadict,
                workspace_id,
                studio_id,
                confirm=confirm,
                preview_token_value=preview_token,
            )
        except Exception as e:
            return client_error(
                "delete_studio_failed",
                log_exc=e,
                context="delete_cvp_studio",
            )


def main(args):
    """Entry point for the direct execution server."""
    global CVP_TRANSPORT

    if args.debug:
        logging.info("Setting server logging to DEBUG")
        logging.getLogger().setLevel(logging.DEBUG)
    mcp_transport = args.transport
    mcp_port = args.port
    mcp_cvp = args.cvp
    CVP_TRANSPORT = mcp_cvp

    logging.info(f"Starting MCP server via {mcp_transport}")
    logging.info(f"Server connection to CVP via {mcp_cvp}")
    # Adding check as HTTP connection to CVP is currently not supported
    if mcp_cvp == "http":
        logging.warning("HTTP connections to CVP are currently not supported")
        sys.exit(1)
    if mcp_transport == "http":
        mcp.settings.port = mcp_port
        mcp.settings.host = args.host
        if args.host == "0.0.0.0":
            logging.warning(
                "HTTP bound to all interfaces (0.0.0.0). "
                "Place an authenticated reverse proxy in front for remote access."
            )
        logging.info(f"Streamable HTTP Server listening on {args.host}:{mcp_port}")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t",
        "--transport",
        type=str,
        help="MCP Transport method",
        default="stdio",
        choices=["http", "stdio"],
        required=False,
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        help="Port to run the Streamable HTTP Server",
        default=8000,
        required=False,
    )
    parser.add_argument(
        "--host",
        type=str,
        help="Bind address for Streamable HTTP (default 127.0.0.1; use 0.0.0.0 only behind auth proxy)",
        default="127.0.0.1",
        required=False,
    )
    parser.add_argument(
        "-c",
        "--cvp",
        type=str,
        help="CVP Connection protocol",
        choices=["grpc", "http"],
        default="grpc",
        required=False,
    )
    parser.add_argument(
        "-d", "--debug", help="Enable debug logging", action="store_true"
    )
    args = parser.parse_args()
    main(args)
