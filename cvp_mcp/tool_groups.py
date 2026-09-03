"""Grouped MCP tool catalog — 46 operations behind 12 names (13 with writes).

Frozen member map and group names for the consolidated tool surface.
``build_groups()`` returns registered ``GroupedTool`` instances (empty until
Task 5+ wires members).
"""

from __future__ import annotations

from collections.abc import Iterator

from cvp_mcp.grouped_tool import GroupedTool
from cvp_mcp.members import device, endpoints, inventory, overlay, routing

DOCSTRING_SURFACE = "46 operations behind 12 names (13 with writes)"

ALWAYS_ON_GROUPS: frozenset[str] = frozenset(
    {
        "inventory",
        "endpoints",
        "device",
        "overlay",
        "routing",
        "topology",
        "events",
        "flow",
        "probes",
        "compliance",
        "meta",
        "studios",
    }
)

LEGACY_FLAT_TO_ACTION: dict[str, str] = {
    "get_cvp_one_device": "inventory.get",
    "get_cvp_all_inventory": "inventory.list",
    "search_cvp_inventory": "inventory.search",
    "get_cvp_endpoint_location": "endpoints.get",
    "get_cvp_all_endpoint_locations": "endpoints.list",
    "get_cvp_endpoint_locations_filtered": "endpoints.filter",
    "get_cvp_device_config": "device.config",
    "get_cvp_interfaces": "device.interfaces",
    "get_cvp_vlans": "device.vlans",
    "get_cvp_ip_interfaces": "device.ip_interfaces",
    "get_cvp_features": "device.features",
    "get_cvp_system_health": "device.health",
    "get_cvp_evpn": "overlay.evpn",
    "get_cvp_vxlan": "overlay.vxlan",
    "get_cvp_bgp_status": "routing.bgp",
    "get_cvp_routes": "routing.routes",
    "get_cvp_lldp_neighbors": "topology.lldp",
    "map_cvp_network_topology": "topology.map",
    "get_cvp_events": "events.list",
    "search_cvp_events": "events.search",
    "get_cvp_flow_data": "flow.get",
    "get_cvp_all_connectivity_probes": "probes.list",
    "get_cvp_one_connectivity_probe": "probes.get",
    "get_cvp_all_bugs": "compliance.bugs",
    "get_cvp_all_device_lifecycle": "compliance.lifecycle",
    "get_cvp_designed_config": "compliance.designed_config",
    "get_cvp_probe_arista_apis": "meta.probe_apis",
    "get_cvp_studios": "studios.list",
    "get_cvp_studio": "studios.get",
    "get_cvp_studio_inputs": "studios.inputs",
    "search_cvp_studio_templates": "studios.search_templates",
    "get_cvp_workspaces": "studios.list_workspaces",
    "get_cvp_workspace": "studios.get_workspace",
    "get_cvp_workspace_build": "studios.get_build",
    "get_cvp_studio_assigned_tags": "studios.tags",
    "create_cvp_workspace": "studios_write.create_workspace",
    "delete_cvp_workspace": "studios_write.delete_workspace",
    "build_cvp_workspace": "studios_write.build",
    "set_cvp_access_interface_description": "studios_write.set_description",
    "set_cvp_studio_inputs": "studios_write.set_inputs",
    "assign_cvp_studio_tags": "studios_write.assign_tags",
    "create_cvp_studio": "studios_write.create_studio",
    "delete_cvp_studio": "studios_write.delete_studio",
    "set_cvp_mss_policy_inputs": "studios_write.set_mss_inputs",
}

_STATUS_ACTIONS: frozenset[str] = frozenset(
    {
        "compliance.config_status",
        "compliance.image_status",
    }
)

MEMBER_ACTIONS: frozenset[str] = (
    frozenset(LEGACY_FLAT_TO_ACTION.values()) | _STATUS_ACTIONS
)


def iter_member_actions() -> Iterator[str]:
    """Yield every ``group.action`` member slot (sorted)."""
    yield from sorted(MEMBER_ACTIONS)


def build_groups() -> list[GroupedTool]:
    """Build grouped tool definitions whose members have been extracted."""
    return [
        GroupedTool(
            name="inventory",
            description="Get, list, or search CloudVision inventory devices.",
            members=inventory.members(),
        ),
        GroupedTool(
            name="endpoints",
            description="Get, list, or filter endpoint locations.",
            members=endpoints.members(),
        ),
        GroupedTool(
            name="device",
            description="Read device configuration and operational state.",
            members=device.members(),
        ),
        GroupedTool(
            name="overlay",
            description="Read device EVPN and VxLAN operational state.",
            members=overlay.members(),
        ),
        GroupedTool(
            name="routing",
            description="Read device BGP and route operational state.",
            members=routing.members(),
        ),
    ]
