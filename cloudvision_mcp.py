#!/usr/bin/python3

import argparse
import logging
import os
import re
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from cvp_mcp.env import env_datadict_from_os
from cvp_mcp.members.compliance import (
    compliance_bugs,
    compliance_designed_config,
    compliance_lifecycle,
)
from cvp_mcp.members.device import (
    device_config,
    device_features,
    device_health,
    device_interfaces,
    device_ip_interfaces,
    device_vlans,
)
from cvp_mcp.members.endpoints import endpoint_filter, endpoint_get, endpoint_list
from cvp_mcp.members.events import events_list, events_search
from cvp_mcp.members.flow import flow_get
from cvp_mcp.members.inventory import (
    inventory_get,
    inventory_list,
    inventory_search,
)
from cvp_mcp.members.meta import meta_probe_apis
from cvp_mcp.members.overlay import overlay_evpn, overlay_vxlan
from cvp_mcp.members.probes import probes_get, probes_list
from cvp_mcp.members.routing import routing_bgp, routing_routes
from cvp_mcp.members.studios import (
    studios_get,
    studios_get_build,
    studios_get_workspace,
    studios_inputs,
    studios_list,
    studios_list_workspaces,
    studios_search_templates,
    studios_tags,
)
from cvp_mcp.members.studios_write import (
    studios_write_assign_tags,
    studios_write_build,
    studios_write_create_studio,
    studios_write_create_workspace,
    studios_write_delete_studio,
    studios_write_delete_workspace,
    studios_write_set_description,
    studios_write_set_inputs,
    studios_write_set_mss_inputs,
)
from cvp_mcp.members.topology import topology_lldp, topology_map
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


# ===================================================
# Inventory Based Tools
# ===================================================


@mcp.tool()
def get_cvp_one_device(device_id) -> dict:
    """
    Prints out information about a single device in CVP
    For one switch it gets the serial number, system mac address,
    hostname, EOS version, streaming status, device type, hardware revision,
    FQDN, domain name, and model

    ``device_id``: Accepts CloudVision serial number (canonical for device datasets),
    hostname, FQDN, or system MAC; resolved to serial before querying.
    """
    return inventory_get(device_id)


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
    return inventory_list()


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
    return inventory_search(query)


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
    return compliance_bugs()


# ===================================================
# Connectivity Monitor Based Tools
# ===================================================


@mcp.tool()
def get_cvp_all_connectivity_probes() -> dict:
    """
    Gets all connectivity monitor probes from CVP
    Displays latency, jitter, http response time and packet loss
    """
    return probes_list()


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
    return probes_get(serial_number, endpoint, vrf, source_interface)


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
    return compliance_lifecycle()


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
    return endpoint_get(search_term)


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
    return endpoint_list(device_serial_allowlist, max_search_keys)


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
    return endpoint_filter(device_id, interface, vlan_id)


# ===================================================
# Capability (installed Resource API packages)
# ===================================================


@mcp.tool()
def get_cvp_probe_arista_apis() -> dict:
    """Lists installed ``arista.*.v1`` Python API packages (Resource API clients bundled with cloudvision)."""
    return meta_probe_apis()


# ===================================================
# Config / interfaces / VLAN / IP (hybrid Resource API + Connector)
# ===================================================


@mcp.tool()
@tool_enabled("get_cvp_device_config")
def get_cvp_device_config(device_id: str, include_running_config: bool = False) -> dict:
    """Device config summary (URIs, sync metadata) from configstatus API; optional running-config text via URI fetch.

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial internally).
    """
    return device_config(device_id, include_running_config)


@mcp.tool()
def get_cvp_interfaces(device_id: str) -> dict:
    """Interface catalog (admin/oper, speed, MTU, description, counters) via Sysdb paths on the device dataset.

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return device_interfaces(device_id)


@mcp.tool()
def get_cvp_vlans(device_id: str) -> dict:
    """VLAN and switchport-related rows from Sysdb bridging paths (best-effort across EOS versions).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return device_vlans(device_id)


@mcp.tool()
def get_cvp_ip_interfaces(device_id: str) -> dict:
    """L3 addresses per interface from Sysdb IP paths (best-effort parse).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return device_ip_interfaces(device_id)


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
    return events_list(
        severity=severity,
        event_type=event_type,
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


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
    return events_search(
        query,
        severity=severity,
        event_type=event_type,
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@mcp.tool()
@tool_enabled("get_cvp_bgp_status")
def get_cvp_bgp_status(device_id: str) -> dict:
    """BGP operational snapshot from Sysdb/Smash routing paths (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return routing_bgp(device_id)


@mcp.tool()
@tool_enabled("get_cvp_routes")
def get_cvp_routes(device_id: str, vrf: str = "default") -> dict:
    """Active route-like RIB entries from Sysdb routing status (best-effort; vrf labels path selection).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return routing_routes(device_id, vrf)


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
    return topology_lldp(
        device_id,
        port_name=port_name,
        remote_neighbor_key=remote_neighbor_key,
        include_lab_devices=include_lab_devices,
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
    return topology_map(
        output_format=output_format,
        include_inactive_devices=include_inactive_devices,
        max_ethernet_ports=max_ethernet_ports,
        device_serial_allowlist=device_serial_allowlist,
        topology_name=topology_name,
        topology_node_scope=topology_node_scope,
        lldp_port_source=lldp_port_source,
        include_lab_devices=include_lab_devices,
    )


# ===================================================
# Features / overlay / system health (Connector)
# ===================================================


@mcp.tool()
def get_cvp_features(device_id: str) -> dict:
    """Enabled-feature-related Sysdb snapshots (best-effort; coverage often partial).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return device_features(device_id)


@mcp.tool()
def get_cvp_evpn(device_id: str) -> dict:
    """EVPN-related Sysdb subtree (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return overlay_evpn(device_id)


@mcp.tool()
def get_cvp_vxlan(device_id: str) -> dict:
    """VxLAN-related Sysdb subtrees (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return overlay_vxlan(device_id)


@mcp.tool()
def get_cvp_system_health(device_id: str) -> dict:
    """System version/status and environment/platform sensors (best-effort).

    ``device_id``: serial, hostname, FQDN, or system MAC (resolved to serial before Connector queries).
    """
    return device_health(device_id)


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
    return flow_get(device_id, flow_index)


# ===================================================
# Studios / workspaces / designed-config (Phase 1 reads)
# ===================================================


@mcp.tool()
@tool_enabled("get_cvp_studios")
def get_cvp_studios() -> dict:
    """List CloudVision studios (ids, names, flags). Omits large template bodies."""
    return studios_list()


@mcp.tool()
@tool_enabled("get_cvp_studio")
def get_cvp_studio(
    studio_id: str, workspace_id: str | None = None, body: bool = False
) -> dict:
    """One studio by id. Default workspace is mainline (empty string). Set body=True for full Mako."""
    return studios_get(studio_id, workspace_id, body)


@mcp.tool()
@tool_enabled("get_cvp_studio_inputs")
def get_cvp_studio_inputs(studio_id: str, workspace_id: str | None = None) -> dict:
    """Current studio input document(s) for a studio/workspace (mainline default)."""
    return studios_inputs(studio_id, workspace_id)


@mcp.tool()
@tool_enabled("search_cvp_studio_templates")
def search_cvp_studio_templates(
    pattern: str, include_input_schema: bool = True, max_hits: int = 100
) -> dict:
    """Search studio templates/schemas for a literal substring; returns JSON paths of hits."""
    return studios_search_templates(pattern, include_input_schema, max_hits)


@mcp.tool()
@tool_enabled("get_cvp_workspaces")
def get_cvp_workspaces() -> dict:
    """List CloudVision workspaces (state, cc ids, build/response ids)."""
    return studios_list_workspaces()


@mcp.tool()
@tool_enabled("get_cvp_workspace")
def get_cvp_workspace(workspace_id: str) -> dict:
    """One workspace by id, including responses map for build polling."""
    return studios_get_workspace(workspace_id)


@mcp.tool()
@tool_enabled("get_cvp_workspace_build")
def get_cvp_workspace_build(workspace_id: str, build_id: str) -> dict:
    """Workspace build status (BUILD_STATE_*) for poll-after-build workflows."""
    return studios_get_build(workspace_id, build_id)


@mcp.tool()
@tool_enabled("get_cvp_designed_config")
def get_cvp_designed_config(device_id: str) -> dict:
    """Designed-config studio sources for a device (compliance GetConfig DESIGNED_CONFIG).

    Prefer device serial; hostname is resolved via inventory when possible.
    """
    return compliance_designed_config(device_id)


@mcp.tool()
@tool_enabled("get_cvp_studio_assigned_tags")
def get_cvp_studio_assigned_tags(
    studio_id: str, workspace_id: str | None = None
) -> dict:
    """Tag query assigned to a studio. Default workspace is mainline (empty string)."""
    return studios_tags(studio_id, workspace_id)


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
        return studios_write_create_workspace(
            workspace_id,
            display_name,
            description=description,
            confirm=confirm,
            preview_token=preview_token,
        )

    @mcp.tool()
    @tool_enabled("delete_cvp_workspace")
    def delete_cvp_workspace(
        workspace_id: str,
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Delete a pending draft workspace. Dry-run unless confirm=True and preview_token matches."""
        return studios_write_delete_workspace(
            workspace_id,
            confirm=confirm,
            preview_token=preview_token,
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
        return studios_write_build(
            workspace_id,
            request_id=request_id,
            confirm=confirm,
            preview_token=preview_token,
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
        return studios_write_set_description(
            workspace_id,
            device_id,
            interface,
            expected_current_description,
            new_description,
            confirm=confirm,
            preview_token=preview_token,
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
        return studios_write_assign_tags(
            studio_id,
            workspace_id,
            query,
            expected_current_query,
            confirm=confirm,
            preview_token=preview_token,
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
        return studios_write_set_inputs(
            studio_id,
            workspace_id,
            path_values,
            inputs,
            confirm=confirm,
            preview_token=preview_token,
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
        return studios_write_create_studio(
            workspace_id,
            studio_id,
            display_name,
            template_body=template_body,
            description=description,
            confirm=confirm,
            preview_token=preview_token,
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
        return studios_write_delete_studio(
            workspace_id,
            studio_id,
            confirm=confirm,
            preview_token=preview_token,
        )

    @mcp.tool()
    @tool_enabled("set_cvp_mss_policy_inputs")
    def set_cvp_mss_policy_inputs(
        workspace_id: str,
        expected_inputs_sha256: str,
        operations: list[dict],
        confirm: bool = False,
        preview_token: str | None = None,
    ) -> dict:
        """Compare-and-set MSS Service groups/services/rules/policy order. Does not submit.

        operations: list of {"op": "upsert", "collection", "entry"},
        {"op": "remove", "collection", "name"} or {"op": "set_policy_rules",
        "policy", "policy_rules"}; collection is one of staticGroups, services,
        rules, policies. expected_inputs_sha256 comes from
        get_cvp_studio_inputs("studio-mss-service").items[].inputs_sha256.
        """
        return studios_write_set_mss_inputs(
            workspace_id,
            expected_inputs_sha256,
            operations,
            confirm=confirm,
            preview_token=preview_token,
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
