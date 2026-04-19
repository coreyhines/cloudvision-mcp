#!/usr/bin/python3

import argparse
import json
import logging
import sys

import grpc
from mcp.server.fastmcp import FastMCP

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.grpc.bugs import grpc_all_bug_exposure
from cvp_mcp.grpc.capability import probe_arista_v1_packages
from cvp_mcp.grpc.config import grpc_get_device_config
from cvp_mcp.grpc.connector import conn_get_info_bugs
from cvp_mcp.grpc.endpoint import (
    grpc_all_endpoint_locations,
    grpc_endpoints_by_filter,
    grpc_one_endpoint_location,
)
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
from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors
from cvp_mcp.grpc.monitor import grpc_all_probe_status, grpc_one_probe_status
from cvp_mcp.grpc.network_map import grpc_map_network_topology
from cvp_mcp.grpc.overlay import (
    grpc_get_evpn,
    grpc_get_features,
    grpc_get_system_health,
    grpc_get_vxlan,
)
from cvp_mcp.grpc.routing import grpc_get_bgp_status, grpc_get_routes
from cvp_mcp.grpc.utils import createConnection

CVP_TRANSPORT = "grpc"

logging.basicConfig(
    level=logging.INFO,  # Minimum log level
    format="%(asctime)s - %(levelname)s - %(message)s",  # Log message format
)

logging.info("Starting the FastMCP server...")

# Initialize FastMCP server
mcp = FastMCP(name="CVP MCP Server", host="0.0.0.0", stateless_http=True)


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
    """
    datadict = get_env_vars()
    logging.debug(f"CVP Get One Device Tool - {device_id}")
    try:
        match CVP_TRANSPORT:
            case "grpc":
                connCreds = createConnection(datadict)
                with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                    device = grpc_one_inventory_serial(channel, device_id)
            case "http":
                device = ""
    except Exception as e:
        logging.error(f"Error fetching device {device_id}: {e}")
        return '{"error": "Device fetch failed"}'
    logging.debug(json.dumps(device, indent=2))
    return json.dumps(device, indent=2)


@mcp.tool()
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
            logging.info("CVP HTTP Request for all devices")
            all_devices = {}
    logging.debug(json.dumps(all_devices))
    # return(json.dumps(all_devices, indent=2))
    return all_devices


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
def get_cvp_all_endpoint_locations() -> dict:
    """Streams all endpoint locations from CVP. Returns all known endpoints
    with MAC, IP, hostname, and their switch attachment locations (device + interface + VLAN).
    """
    datadict = get_env_vars()
    all_devices = {}
    all_data = {}
    logging.info("CVP Get All Endpoint Locations")
    all_endpoints = []
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                all_endpoints = grpc_all_endpoint_locations(channel)
                for _endpoint in all_endpoints:
                    for _device in _endpoint["location_list"]:
                        serial_number = _device["device_id"]["value"]
                        if serial_number not in all_devices:
                            all_devices[serial_number] = grpc_one_inventory_serial(
                                channel, serial_number
                            )
        case "http":
            logging.info("CVP HTTP Request for all devices")
            all_devices = ""
    all_data["devices"] = all_devices
    all_data["endpoints"] = all_endpoints
    return all_data


@mcp.tool()
def get_cvp_endpoint_locations_filtered(
    device_id: str | None = None,
    interface: str | None = None,
    vlan_id: int | None = None,
) -> dict:
    """Filters endpoint locations by switch serial number, interface name (e.g. 'Ethernet1'),
    or VLAN ID. Provide at least one filter. Filtering is applied client-side."""
    datadict = get_env_vars()
    all_devices = {}
    all_data = {}
    logging.info(
        f"CVP Get Filtered Endpoint Locations: device={device_id} intf={interface} vlan={vlan_id}"
    )
    if device_id is None and interface is None and vlan_id is None:
        logging.warning(
            "get_cvp_endpoint_locations_filtered called with no filters; "
            "this may return a large result set."
        )
    all_endpoints = []
    match CVP_TRANSPORT:
        case "grpc":
            connCreds = createConnection(datadict)
            with grpc.secure_channel(datadict["cvp"], connCreds) as channel:
                all_endpoints = grpc_endpoints_by_filter(
                    channel, device_id, interface, vlan_id
                )
                for _endpoint in all_endpoints:
                    for _device in _endpoint["location_list"]:
                        serial_number = _device["device_id"]["value"]
                        if serial_number not in all_devices:
                            all_devices[serial_number] = grpc_one_inventory_serial(
                                channel, serial_number
                            )
        case "http":
            logging.info("CVP HTTP Request for all devices")
            all_devices = ""
    all_data["devices"] = all_devices
    all_data["endpoints"] = all_endpoints
    return all_data


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
def get_cvp_device_config(device_id: str, include_running_config: bool = False) -> dict:
    """Device config summary (URIs, sync metadata) from configstatus API; optional running-config text via URI fetch."""
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
        logging.error("get_cvp_device_config: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_interfaces(device_id: str) -> dict:
    """Interface catalog (admin/oper, speed, MTU, description, counters) via Sysdb paths on the device dataset."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_interfaces(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_interfaces: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_vlans(device_id: str) -> dict:
    """VLAN and switchport-related rows from Sysdb bridging paths (best-effort across EOS versions)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_vlans(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_vlans: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_ip_interfaces(device_id: str) -> dict:
    """L3 addresses per interface from Sysdb IP paths (best-effort parse)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_ip_interfaces(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_ip_interfaces: %s", e)
        return {"error": str(e)}


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
        logging.error("get_cvp_events: %s", e)
        return {"error": str(e)}


@mcp.tool()
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
        logging.error("search_cvp_events: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_bgp_status(device_id: str) -> dict:
    """BGP operational snapshot from Sysdb/Smash routing paths (best-effort)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_bgp_status(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_bgp_status: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_routes(device_id: str, vrf: str = "default") -> dict:
    """Active route-like RIB entries from Sysdb routing status (best-effort; vrf labels path selection)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_routes(datadict, device_id, vrf=vrf)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_routes: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_lldp_neighbors(
    device_id: str,
    port_name: str = "",
    remote_neighbor_key: str = "",
) -> dict:
    """LLDP neighbor table from EOS Sysdb via Connector (best-effort; requires LLDP enabled on device).

    If Telemetry Browser shows a path like ``…/portStatus/Ethernet6/remoteSystem/1`` but wildcard
    queries return nothing, pass ``port_name`` (e.g. ``Ethernet6``) and optionally ``remote_neighbor_key`` (e.g. ``1``).
    """
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_lldp_neighbors(
                    datadict,
                    device_id,
                    port_name=port_name,
                    remote_neighbor_key=remote_neighbor_key,
                )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_lldp_neighbors: %s", e)
        return {"error": str(e)}


@mcp.tool()
def map_cvp_network_topology(
    output_format: str = "json",
    include_inactive_devices: bool = False,
    max_ethernet_ports: int | None = None,
    device_serial_allowlist: str = "",
    topology_name: str = "cvp-lldp",
    topology_node_scope: str = "full_inventory",
    lldp_port_source: str = "auto",
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
                )
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("map_cvp_network_topology: %s", e)
        return {"error": str(e)}


# ===================================================
# Features / overlay / system health (Connector)
# ===================================================


@mcp.tool()
def get_cvp_features(device_id: str) -> dict:
    """Enabled-feature-related Sysdb snapshots (best-effort; coverage often partial)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_features(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_features: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_evpn(device_id: str) -> dict:
    """EVPN-related Sysdb subtree (best-effort)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_evpn(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_evpn: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_vxlan(device_id: str) -> dict:
    """VxLAN-related Sysdb subtrees (best-effort)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_vxlan(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_vxlan: %s", e)
        return {"error": str(e)}


@mcp.tool()
def get_cvp_system_health(device_id: str) -> dict:
    """System version/status and environment/platform sensors (best-effort)."""
    datadict = get_env_vars()
    try:
        match CVP_TRANSPORT:
            case "grpc":
                return grpc_get_system_health(datadict, device_id)
            case "http":
                return {"error": "grpc_only"}
    except Exception as e:
        logging.error("get_cvp_system_health: %s", e)
        return {"error": str(e)}


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
    device_id: optional switch serial; keeps only records whose node matches that device.
    Returns flow records with src/dst IPs, ports, protocol, bytes/packets, and interfaces.
    """
    datadict = get_env_vars()
    logging.info(f"CVP Get Flow Data: device={device_id} flow_index={flow_index}")
    flows = conn_get_flow_data(datadict, device_id, flow_index)
    return {"flows": flows}


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
        logging.info(f"Streamable HTTP Server listening on port {mcp_port}")
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
        default="http",
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
