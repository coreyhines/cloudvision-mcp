"""Tests for LLDP network map helpers (no live CVP)."""

from __future__ import annotations

import json
from unittest.mock import patch

import yaml

from cvp_mcp.grpc.network_map import (
    _canonical_link_key,
    _check_edge_mismatches,
    _directed_edge_to_side,
    _filter_simple_ethernet_ports_by_cap,
    _merge_bidirectional_edges,
    _remote_device_id,
    build_topology_nodes_and_links,
    format_topology_containerlab,
    format_topology_mermaid,
    format_topology_table,
    grpc_map_network_topology,
    infer_ethernet_port_count,
    scan_lldp_topology_edges,
)


def test_infer_ethernet_port_count_models():
    assert infer_ethernet_port_count("CCS-720XP-48TXH-2C-S") == 48
    assert infer_ethernet_port_count("CCS-720XP-24ZY4") == 24
    assert infer_ethernet_port_count("CCS-710P-16P") == 16
    assert infer_ethernet_port_count("AWE-5310") == 32


def test_format_table_and_mermaid():
    edges = [
        {
            "side_a": {
                "serial": "S1",
                "hostname": "sw1",
                "model": "",
                "port": "Ethernet6",
                "remote_system_name": "host-a",
                "remote_chassis_id": "",
                "remote_eth_addr": "aa:bb:cc:dd:ee:01",
                "remote_port_id": "eth0",
                "remote_management_address": "10.0.0.8",
                "remote_management_addresses": ["10.0.0.8"],
                "remote_system_description": "host",
                "remote_system_capabilities": ["bridge"],
                "remote_enabled_system_capabilities": ["bridge"],
                "remote_pvid": "120",
                "remote_vlans": ["120"],
                "remote_lldp_med": {"lldpMedPolicy": ["voice"]},
                "neighbor_source": "",
            },
            "side_b": None,
            "mismatches": [],
            "link_verified": False,
            "link_direction": "undirected",
        }
    ]
    table = format_topology_table(edges)
    assert "sw1" in table
    assert "Ethernet6" in table
    assert "host-a" in table
    mmd = format_topology_mermaid(
        [
            {"id": "S1", "label": "sw1"},
            {"id": "name:host-a", "label": "host-a"},
        ],
        [
            {
                "source_id": "S1",
                "target_id": "name:host-a",
                "local_port": "Ethernet6",
                "remote_port_id": "eth0",
                "link_verified": False,
                "mismatches": [],
            }
        ],
    )
    assert "Ethernet6" in mmd


def test_containerlab_yaml_roundtrip():
    nodes = [
        {
            "id": "A",
            "label": "sw-a",
            "kind": "inventory",
            "device_type": "EOS",
            "model": "",
            "serial_number": "A",
            "system_mac": "",
        },
        {
            "id": "name:pi",
            "label": "pi",
            "kind": "lldp_external",
            "device_type": "",
            "model": "",
            "serial_number": "",
            "system_mac": "",
        },
    ]
    links = [
        {
            "source_id": "A",
            "target_id": "name:pi",
            "local_port": "Ethernet6",
            "remote_port_id": "Management1/1",
            "remote_system_name": "pi",
            "matched_inventory": False,
            "link_verified": False,
            "mismatches": [],
        }
    ]
    yml = format_topology_containerlab(nodes, links, topology_name="t1")
    doc = yaml.safe_load(yml)
    assert doc["name"] == "t1"
    assert "sw-a" in doc["topology"]["nodes"]
    assert doc["topology"]["links"]


@patch(
    "cvp_mcp.grpc.network_map._collect_inventory",
    return_value=([], []),
)
@patch(
    "cvp_mcp.grpc.network_map.scan_lldp_topology_edges",
    return_value=(
        [],
        {
            "devices_scanned": 0,
            "port_probes": 0,
            "edges_found": 0,
            "extra_neighbor_index_probes": 0,
            "devices_port_source_oper_up": 0,
            "devices_port_source_ethernet_range": 0,
            "devices_skipped_no_oper_up_ports": 0,
            "unidirectional_links": 0,
            "mismatched_links": 0,
            "lldp_port_source": "auto",
            "inventory_warnings": [],
        },
    ),
)
def test_grpc_map_network_topology_invalid_format_falls_back(
    _mock_scan: object,
    _mock_inv: object,
) -> None:
    out = grpc_map_network_topology(
        {"cvp": "x:443", "cvtoken": "t"},
        output_format="not-a-format",
    )
    assert out["output_format"] == "json"
    assert out["warnings"]


def test_build_topology_connected_scope_excludes_orphan_inventory():
    inv = [
        {
            "hostname": "lonely",
            "serial_number": "SN99",
            "system_mac": "aa:aa:aa:aa:aa:aa",
            "model": "m",
            "device_type": "EOS",
        },
        {
            "hostname": "core",
            "serial_number": "SN1",
            "system_mac": "bb:bb:bb:bb:bb:bb",
            "model": "m",
            "device_type": "EOS",
        },
    ]
    edges = [
        {
            "side_a": {
                "serial": "SN1",
                "hostname": "core",
                "model": "",
                "port": "Ethernet1",
                "remote_system_name": "other",
                "remote_chassis_id": "",
                "remote_eth_addr": "cc:cc:cc:cc:cc:cc",
                "remote_port_id": "e1",
                "remote_management_address": "",
                "remote_management_addresses": [],
                "remote_system_description": "",
                "remote_system_capabilities": [],
                "remote_enabled_system_capabilities": [],
                "remote_pvid": "",
                "remote_vlans": [],
                "remote_lldp_med": {},
                "neighbor_source": "",
            },
            "side_b": None,
            "mismatches": ["unidirectional_lldp"],
            "link_verified": False,
            "link_direction": "undirected",
        }
    ]
    nodes, _ = build_topology_nodes_and_links(edges, inv, node_scope="connected")
    ids = {n["id"] for n in nodes}
    assert "SN99" not in ids
    assert "SN1" in ids


def test_filter_simple_ethernet_ports_by_cap():
    assert _filter_simple_ethernet_ports_by_cap(
        ["Ethernet1", "Ethernet99", "Management1"], cap=48
    ) == ["Ethernet1", "Management1"]


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
def test_scan_lldp_uses_oper_up_port_list(
    mock_oper_up: object,
    mock_lldp: object,
    mock_inv: object,
) -> None:
    mock_inv.return_value = (
        [
            {
                "serial_number": "SN1",
                "hostname": "sw1",
                "model": "CCS-720XP-48TXH-2C-S",
            }
        ],
        [],
    )
    mock_oper_up.return_value = (["Ethernet1", "Ethernet2"], [])
    mock_lldp.return_value = {"items": []}
    _edges, stats = scan_lldp_topology_edges(
        {"cvp": "h.example:443", "cvtoken": "t"},
        lldp_port_source="auto",
    )
    assert stats["port_probes"] == 2
    assert stats["devices_port_source_oper_up"] == 1
    assert stats["devices_port_source_ethernet_range"] == 0
    assert "unidirectional_links" in stats
    assert "mismatched_links" in stats
    assert mock_lldp.call_count == 2


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
def test_scan_lldp_full_range_skips_oper_up_query(
    mock_oper_up: object,
    mock_lldp: object,
    mock_inv: object,
) -> None:
    mock_inv.return_value = (
        [{"serial_number": "SN1", "hostname": "sw1", "model": "CCS-710P-16P"}],
        [],
    )
    mock_lldp.return_value = {"items": []}
    _edges, stats = scan_lldp_topology_edges(
        {"cvp": "h.example:443", "cvtoken": "t"},
        max_ethernet_ports=4,
        lldp_port_source="full_range",
    )
    mock_oper_up.assert_not_called()
    assert stats["port_probes"] == 4
    assert stats["devices_port_source_ethernet_range"] == 1
    assert "unidirectional_links" in stats
    assert "mismatched_links" in stats


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
def test_scan_lldp_oper_up_only_skips_fallback_range(
    mock_oper_up: object,
    mock_lldp: object,
    mock_inv: object,
) -> None:
    mock_inv.return_value = (
        [{"serial_number": "SN1", "hostname": "sw1", "model": "CCS-710P-16P"}],
        [],
    )
    mock_oper_up.return_value = ([], [])
    _edges, stats = scan_lldp_topology_edges(
        {"cvp": "h.example:443", "cvtoken": "t"},
        max_ethernet_ports=4,
        lldp_port_source="oper_up_only",
    )
    mock_lldp.assert_not_called()
    assert stats["port_probes"] == 0
    assert stats["devices_port_source_ethernet_range"] == 0
    assert stats["devices_skipped_no_oper_up_ports"] == 1
    assert "unidirectional_links" in stats
    assert "mismatched_links" in stats


@patch("cvp_mcp.grpc.interfaces._connector_device_config")
def test_grpc_list_oper_up_physical_ports_for_lldp(mock_cfg: object) -> None:
    from cvp_mcp.grpc.interfaces import grpc_list_oper_up_physical_ports_for_lldp

    mock_cfg.return_value = {
        "intfConfig": {"Ethernet1": {}, "Ethernet2": {}},
        "intfStatus": {
            "Ethernet1": {"operStatus": {"Name": "intfOperUp"}},
            "Ethernet2": {"operStatus": {"Name": "intfOperDown"}},
        },
    }
    ports, warnings = grpc_list_oper_up_physical_ports_for_lldp(
        {"cvp": "h:443", "cvtoken": "x"},
        "SERIAL1",
    )
    assert ports == ["Ethernet1"]
    assert not warnings


def test_build_topology_matches_inventory_mac():
    inv = [
        {
            "hostname": "core",
            "serial_number": "SN1",
            "system_mac": "ec:8a:48:04:30:c0",
            "model": "m",
            "device_type": "EOS",
        }
    ]
    edges = [
        {
            "side_a": {
                "serial": "SN2",
                "hostname": "leaf",
                "model": "",
                "port": "Ethernet1",
                "remote_system_name": "core",
                "remote_chassis_id": "ec:8a:48:04:30:c0",
                "remote_eth_addr": "",
                "remote_port_id": "Eth1",
                "remote_management_address": "",
                "remote_management_addresses": [],
                "remote_system_description": "",
                "remote_system_capabilities": [],
                "remote_enabled_system_capabilities": [],
                "remote_pvid": "",
                "remote_vlans": [],
                "remote_lldp_med": {},
                "neighbor_source": "",
            },
            "side_b": None,
            "mismatches": ["unidirectional_lldp"],
            "link_verified": False,
            "link_direction": "undirected",
        }
    ]
    nodes, links = build_topology_nodes_and_links(edges, inv)
    assert any(n["serial_number"] == "SN1" for n in nodes)
    assert links[0].get("matched_inventory") is True
    assert json.dumps({"nodes": nodes, "links": links})


def test_build_topology_links_carry_rich_remote_metadata():
    inv: list[dict[str, str]] = []
    edges = [
        {
            "side_a": {
                "serial": "SN2",
                "hostname": "leaf",
                "model": "",
                "port": "Ethernet1",
                "remote_system_name": "downstream",
                "remote_chassis_id": "",
                "remote_eth_addr": "00:11:22:33:44:55",
                "remote_port_id": "Eth24",
                "remote_management_address": "10.2.2.2",
                "remote_management_addresses": ["10.2.2.2"],
                "remote_system_description": "switch edge",
                "remote_system_capabilities": ["bridge", "router"],
                "remote_enabled_system_capabilities": ["bridge"],
                "remote_pvid": "120",
                "remote_vlans": ["120", "121"],
                "remote_lldp_med": {"lldpMedPolicy": ["voice"]},
                "neighbor_source": "remoteSystem",
            },
            "side_b": None,
            "mismatches": ["unidirectional_lldp"],
            "link_verified": False,
            "link_direction": "undirected",
        }
    ]
    _nodes, links = build_topology_nodes_and_links(edges, inv)
    assert links[0]["remote_management_address"] == "10.2.2.2"
    assert links[0]["remote_system_description"] == "switch edge"
    assert links[0]["remote_system_capabilities"] == ["bridge", "router"]
    assert links[0]["remote_vlans"] == ["120", "121"]


def test_remote_device_id_prefers_remote_eth_addr():
    edge = {
        "local_serial": "SN1",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_system_name": "spine-01",
    }
    assert _remote_device_id(edge) == "mac:aa:bb:cc:dd:ee:01"


def test_remote_device_id_falls_back_to_chassis_id():
    edge = {
        "local_serial": "SN1",
        "remote_eth_addr": "",
        "remote_chassis_id": "aa:bb:cc:dd:ee:02",
        "remote_system_name": "spine-01",
    }
    assert _remote_device_id(edge) == "mac:aa:bb:cc:dd:ee:02"


def test_remote_device_id_falls_back_to_system_name():
    edge = {
        "local_serial": "SN1",
        "remote_eth_addr": "",
        "remote_chassis_id": "",
        "remote_system_name": "spine-01",
    }
    assert _remote_device_id(edge) == "name:spine-01"


def test_canonical_link_key_uses_serial_to_mac():
    serial_to_mac = {"SN1": "cc:dd:ee:ff:00:01", "SN2": "aa:bb:cc:dd:ee:01"}
    edge_a = {
        "local_serial": "SN1",
        "local_port": "Ethernet5",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_system_name": "spine-01",
    }
    edge_b = {
        "local_serial": "SN2",
        "local_port": "Ethernet3",
        "remote_eth_addr": "cc:dd:ee:ff:00:01",
        "remote_port_id": "Ethernet5",
        "remote_system_name": "leaf-01",
    }
    key_a = _canonical_link_key(edge_a, serial_to_mac)
    key_b = _canonical_link_key(edge_b, serial_to_mac)
    assert key_a == key_b


def test_canonical_link_key_different_ports_different_keys():
    serial_to_mac = {"SN1": "cc:dd:ee:ff:00:01"}
    edge_a = {
        "local_serial": "SN1",
        "local_port": "Ethernet5",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_system_name": "spine-01",
    }
    edge_c = {
        "local_serial": "SN1",
        "local_port": "Ethernet6",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet4",
        "remote_system_name": "spine-01",
    }
    assert _canonical_link_key(edge_a, serial_to_mac) != _canonical_link_key(
        edge_c, serial_to_mac
    )


def test_directed_edge_to_side_extracts_local_side():
    edge = {
        "local_serial": "SN1",
        "local_hostname": "leaf-01",
        "local_model": "7050X3",
        "local_port": "Ethernet5",
        "remote_system_name": "spine-01",
        "remote_chassis_id": "aa:bb:cc:dd:ee:02",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_management_address": "10.0.0.2",
        "remote_management_addresses": ["10.0.0.2"],
        "remote_system_description": "Arista EOS",
        "remote_system_capabilities": ["bridge", "router"],
        "remote_enabled_system_capabilities": ["bridge"],
        "remote_pvid": "100",
        "remote_vlans": ["100", "200"],
        "remote_lldp_med": {"lldpMedPolicy": ["voice"]},
        "neighbor_source": "remoteSystem",
    }
    side = _directed_edge_to_side(edge)
    assert side["serial"] == "SN1"
    assert side["hostname"] == "leaf-01"
    assert side["port"] == "Ethernet5"
    assert side["remote_system_name"] == "spine-01"
    assert side["remote_eth_addr"] == "aa:bb:cc:dd:ee:01"
    assert side["remote_port_id"] == "Ethernet3"
    assert side["remote_pvid"] == "100"
    assert side["remote_vlans"] == ["100", "200"]


def test_check_edge_mismatches_detects_pvid_mismatch():
    side_a = {
        "serial": "SN1",
        "hostname": "a",
        "port": "Ethernet5",
        "remote_system_name": "b",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet3",
        "remote_pvid": "100",
        "remote_vlans": ["100"],
    }
    side_b = {
        "serial": "SN2",
        "hostname": "b",
        "port": "Ethernet3",
        "remote_system_name": "a",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet5",
        "remote_pvid": "200",
        "remote_vlans": ["200"],
    }
    mismatches = _check_edge_mismatches(side_a, side_b)
    assert any("pvid_mismatch" in m for m in mismatches)


def test_check_edge_mismatches_detects_vlan_mismatch():
    side_a = {
        "serial": "SN1",
        "hostname": "a",
        "port": "Ethernet5",
        "remote_system_name": "b",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet3",
        "remote_pvid": "100",
        "remote_vlans": ["100", "200"],
    }
    side_b = {
        "serial": "SN2",
        "hostname": "b",
        "port": "Ethernet3",
        "remote_system_name": "a",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet5",
        "remote_pvid": "100",
        "remote_vlans": ["100", "300"],
    }
    mismatches = _check_edge_mismatches(side_a, side_b)
    assert any("vlan_mismatch" in m for m in mismatches)


def test_check_edge_mismatches_detects_port_crossref_mismatch():
    side_a = {
        "serial": "SN1",
        "hostname": "a",
        "port": "Ethernet5",
        "remote_system_name": "b",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet3",
        "remote_pvid": "",
        "remote_vlans": [],
    }
    side_b = {
        "serial": "SN2",
        "hostname": "b",
        "port": "Ethernet3",
        "remote_system_name": "a",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet9",
        "remote_pvid": "",
        "remote_vlans": [],
    }
    mismatches = _check_edge_mismatches(side_a, side_b)
    assert any("port_crossref_mismatch" in m for m in mismatches)


def test_check_edge_mismatches_clean_when_matching():
    side_a = {
        "serial": "SN1",
        "hostname": "a",
        "port": "Ethernet5",
        "remote_system_name": "b",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet3",
        "remote_pvid": "100",
        "remote_vlans": ["100"],
    }
    side_b = {
        "serial": "SN2",
        "hostname": "b",
        "port": "Ethernet3",
        "remote_system_name": "a",
        "remote_eth_addr": "",
        "remote_port_id": "Ethernet5",
        "remote_pvid": "100",
        "remote_vlans": ["100"],
    }
    assert _check_edge_mismatches(side_a, side_b) == []


def test_merge_bidirectional_edges_merges_pair():
    serial_to_mac = {"SN1": "cc:dd:ee:ff:00:01", "SN2": "aa:bb:cc:dd:ee:01"}
    edge_a = {
        "local_serial": "SN1",
        "local_hostname": "leaf-01",
        "local_model": "7050X3",
        "local_port": "Ethernet5",
        "remote_system_name": "spine-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_management_address": "10.0.0.2",
        "remote_management_addresses": ["10.0.0.2"],
        "remote_system_description": "Arista EOS",
        "remote_system_capabilities": ["bridge", "router"],
        "remote_enabled_system_capabilities": ["bridge"],
        "remote_pvid": "100",
        "remote_vlans": ["100", "200"],
        "remote_lldp_med": {},
        "neighbor_source": "remoteSystem",
    }
    edge_b = {
        "local_serial": "SN2",
        "local_hostname": "spine-01",
        "local_model": "7280R3",
        "local_port": "Ethernet3",
        "remote_system_name": "leaf-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "cc:dd:ee:ff:00:01",
        "remote_port_id": "Ethernet5",
        "remote_management_address": "10.0.0.1",
        "remote_management_addresses": ["10.0.0.1"],
        "remote_system_description": "Arista EOS",
        "remote_system_capabilities": ["bridge", "router"],
        "remote_enabled_system_capabilities": ["bridge", "router"],
        "remote_pvid": "100",
        "remote_vlans": ["100", "200"],
        "remote_lldp_med": {},
        "neighbor_source": "remoteSystem",
    }
    merged = _merge_bidirectional_edges([edge_a, edge_b], serial_to_mac)
    assert len(merged) == 1
    e = merged[0]
    assert e["link_verified"] is True
    assert e["side_a"]["serial"] == "SN1"
    assert e["side_a"]["port"] == "Ethernet5"
    assert e["side_b"]["serial"] == "SN2"
    assert e["side_b"]["port"] == "Ethernet3"
    assert e["mismatches"] == []
    assert e["link_direction"] == "undirected"


def test_merge_bidirectional_edges_unidirectional_edge():
    serial_to_mac = {"SN1": "cc:dd:ee:ff:00:01"}
    edge_a = {
        "local_serial": "SN1",
        "local_hostname": "leaf-01",
        "local_model": "",
        "local_port": "Ethernet5",
        "remote_system_name": "spine-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_management_address": "",
        "remote_management_addresses": [],
        "remote_system_description": "",
        "remote_system_capabilities": [],
        "remote_enabled_system_capabilities": [],
        "remote_pvid": "",
        "remote_vlans": [],
        "remote_lldp_med": {},
        "neighbor_source": "",
    }
    merged = _merge_bidirectional_edges([edge_a], serial_to_mac)
    assert len(merged) == 1
    e = merged[0]
    assert e["link_verified"] is False
    assert e["side_a"]["serial"] == "SN1"
    assert e["side_b"] is None
    assert "unidirectional_lldp" in e["mismatches"]


def test_merge_bidirectional_edges_with_mismatch():
    serial_to_mac = {"SN1": "cc:dd:ee:ff:00:01", "SN2": "aa:bb:cc:dd:ee:01"}
    edge_a = {
        "local_serial": "SN1",
        "local_hostname": "leaf-01",
        "local_model": "",
        "local_port": "Ethernet5",
        "remote_system_name": "spine-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_management_address": "",
        "remote_management_addresses": [],
        "remote_system_description": "",
        "remote_system_capabilities": [],
        "remote_enabled_system_capabilities": [],
        "remote_pvid": "100",
        "remote_vlans": ["100"],
        "remote_lldp_med": {},
        "neighbor_source": "",
    }
    edge_b = {
        "local_serial": "SN2",
        "local_hostname": "spine-01",
        "local_model": "",
        "local_port": "Ethernet3",
        "remote_system_name": "leaf-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "cc:dd:ee:ff:00:01",
        "remote_port_id": "Ethernet5",
        "remote_management_address": "",
        "remote_management_addresses": [],
        "remote_system_description": "",
        "remote_system_capabilities": [],
        "remote_enabled_system_capabilities": [],
        "remote_pvid": "200",
        "remote_vlans": ["200"],
        "remote_lldp_med": {},
        "neighbor_source": "",
    }
    merged = _merge_bidirectional_edges([edge_a, edge_b], serial_to_mac)
    assert len(merged) == 1
    e = merged[0]
    assert e["link_verified"] is True
    assert any("pvid_mismatch" in m for m in e["mismatches"])
    assert any("vlan_mismatch" in m for m in e["mismatches"])


def test_merge_bidirectional_edges_multiple_links():
    serial_to_mac = {"SN1": "cc:dd:ee:ff:00:01", "SN2": "aa:bb:cc:dd:ee:01"}
    edge_a1 = {
        "local_serial": "SN1",
        "local_hostname": "leaf-01",
        "local_model": "",
        "local_port": "Ethernet5",
        "remote_system_name": "spine-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet3",
        "remote_management_address": "",
        "remote_management_addresses": [],
        "remote_system_description": "",
        "remote_system_capabilities": [],
        "remote_enabled_system_capabilities": [],
        "remote_pvid": "",
        "remote_vlans": [],
        "remote_lldp_med": {},
        "neighbor_source": "",
    }
    edge_a2 = {
        "local_serial": "SN1",
        "local_hostname": "leaf-01",
        "local_model": "",
        "local_port": "Ethernet6",
        "remote_system_name": "spine-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "aa:bb:cc:dd:ee:01",
        "remote_port_id": "Ethernet4",
        "remote_management_address": "",
        "remote_management_addresses": [],
        "remote_system_description": "",
        "remote_system_capabilities": [],
        "remote_enabled_system_capabilities": [],
        "remote_pvid": "",
        "remote_vlans": [],
        "remote_lldp_med": {},
        "neighbor_source": "",
    }
    edge_b1 = {
        "local_serial": "SN2",
        "local_hostname": "spine-01",
        "local_model": "",
        "local_port": "Ethernet3",
        "remote_system_name": "leaf-01",
        "remote_chassis_id": "",
        "remote_eth_addr": "cc:dd:ee:ff:00:01",
        "remote_port_id": "Ethernet5",
        "remote_management_address": "",
        "remote_management_addresses": [],
        "remote_system_description": "",
        "remote_system_capabilities": [],
        "remote_enabled_system_capabilities": [],
        "remote_pvid": "",
        "remote_vlans": [],
        "remote_lldp_med": {},
        "neighbor_source": "",
    }
    merged = _merge_bidirectional_edges([edge_a1, edge_a2, edge_b1], serial_to_mac)
    assert len(merged) == 2
    verified = [e for e in merged if e["link_verified"]]
    unidir = [e for e in merged if not e["link_verified"]]
    assert len(verified) == 1
    assert len(unidir) == 1


def test_build_topology_with_merged_edges():
    """Bidirectional merged edge with both sides: verify link_verified and ports."""
    inv: list[dict[str, str]] = []
    edges = [
        {
            "side_a": {
                "serial": "SN1",
                "hostname": "leaf-01",
                "model": "7050X3",
                "port": "Ethernet5",
                "remote_system_name": "spine-01",
                "remote_chassis_id": "",
                "remote_eth_addr": "aa:bb:cc:dd:ee:01",
                "remote_port_id": "Ethernet3",
                "remote_management_address": "10.0.0.2",
                "remote_management_addresses": ["10.0.0.2"],
                "remote_system_description": "Arista EOS",
                "remote_system_capabilities": ["bridge", "router"],
                "remote_enabled_system_capabilities": ["bridge"],
                "remote_pvid": "100",
                "remote_vlans": ["100", "200"],
                "remote_lldp_med": {},
                "neighbor_source": "remoteSystem",
            },
            "side_b": {
                "serial": "SN2",
                "hostname": "spine-01",
                "model": "7280R3",
                "port": "Ethernet3",
                "remote_system_name": "leaf-01",
                "remote_chassis_id": "",
                "remote_eth_addr": "cc:dd:ee:ff:00:01",
                "remote_port_id": "Ethernet5",
                "remote_management_address": "10.0.0.1",
                "remote_management_addresses": ["10.0.0.1"],
                "remote_system_description": "Arista EOS",
                "remote_system_capabilities": ["bridge", "router"],
                "remote_enabled_system_capabilities": ["bridge", "router"],
                "remote_pvid": "100",
                "remote_vlans": ["100", "200"],
                "remote_lldp_med": {},
                "neighbor_source": "remoteSystem",
            },
            "mismatches": [],
            "link_verified": True,
            "link_direction": "undirected",
        }
    ]
    nodes, links = build_topology_nodes_and_links(edges, inv)
    assert len(links) == 1
    lk = links[0]
    assert lk["link_verified"] is True
    assert lk["local_port"] == "Ethernet5"
    assert lk["remote_port_id"] == "Ethernet3"
    assert lk["source_id"] == "SN1"
    assert lk["target_id"] == "mac:aa:bb:cc:dd:ee:01"
    assert lk["mismatches"] == []


def test_build_topology_unidirectional_merged_edge():
    """Single-side edge: verify link_verified is False."""
    inv: list[dict[str, str]] = []
    edges = [
        {
            "side_a": {
                "serial": "SN1",
                "hostname": "leaf-01",
                "model": "",
                "port": "Ethernet5",
                "remote_system_name": "spine-01",
                "remote_chassis_id": "",
                "remote_eth_addr": "aa:bb:cc:dd:ee:01",
                "remote_port_id": "Ethernet3",
                "remote_management_address": "",
                "remote_management_addresses": [],
                "remote_system_description": "",
                "remote_system_capabilities": [],
                "remote_enabled_system_capabilities": [],
                "remote_pvid": "",
                "remote_vlans": [],
                "remote_lldp_med": {},
                "neighbor_source": "",
            },
            "side_b": None,
            "mismatches": ["unidirectional_lldp"],
            "link_verified": False,
            "link_direction": "undirected",
        }
    ]
    nodes, links = build_topology_nodes_and_links(edges, inv)
    assert len(links) == 1
    lk = links[0]
    assert lk["link_verified"] is False
    assert lk["local_port"] == "Ethernet5"
    assert lk["remote_port_id"] == "Ethernet3"
    assert lk["mismatches"] == ["unidirectional_lldp"]


def test_format_table_with_merged_edges():
    """Verify one row per link, both hostnames and ports present."""
    edges = [
        {
            "side_a": {
                "serial": "SN1",
                "hostname": "leaf-01",
                "model": "",
                "port": "Ethernet5",
                "remote_system_name": "spine-01",
                "remote_chassis_id": "",
                "remote_eth_addr": "aa:bb:cc:dd:ee:01",
                "remote_port_id": "Ethernet3",
                "remote_management_address": "",
                "remote_management_addresses": [],
                "remote_system_description": "",
                "remote_system_capabilities": [],
                "remote_enabled_system_capabilities": [],
                "remote_pvid": "",
                "remote_vlans": [],
                "remote_lldp_med": {},
                "neighbor_source": "",
            },
            "side_b": {
                "serial": "SN2",
                "hostname": "spine-01",
                "model": "",
                "port": "Ethernet3",
                "remote_system_name": "leaf-01",
                "remote_chassis_id": "",
                "remote_eth_addr": "cc:dd:ee:ff:00:01",
                "remote_port_id": "Ethernet5",
                "remote_management_address": "",
                "remote_management_addresses": [],
                "remote_system_description": "",
                "remote_system_capabilities": [],
                "remote_enabled_system_capabilities": [],
                "remote_pvid": "",
                "remote_vlans": [],
                "remote_lldp_med": {},
                "neighbor_source": "",
            },
            "mismatches": [],
            "link_verified": True,
            "link_direction": "undirected",
        }
    ]
    table = format_topology_table(edges)
    assert "leaf-01" in table
    assert "Ethernet5" in table
    assert "spine-01" in table
    assert "Ethernet3" in table
    # Count data rows (exclude header + separator)
    all_rows = table.strip().split("\n")
    data_rows = all_rows[2:]  # skip header + separator
    assert len(data_rows) == 1


def test_format_table_unidirectional_edge():
    """Verify unidirectional edge in table uses side_a's remote fields for Host B / Port B."""
    edges = [
        {
            "side_a": {
                "serial": "SN1",
                "hostname": "leaf-01",
                "model": "",
                "port": "Ethernet5",
                "remote_system_name": "spine-01",
                "remote_chassis_id": "",
                "remote_eth_addr": "aa:bb:cc:dd:ee:01",
                "remote_port_id": "Ethernet3",
                "remote_management_address": "",
                "remote_management_addresses": [],
                "remote_system_description": "",
                "remote_system_capabilities": [],
                "remote_enabled_system_capabilities": [],
                "remote_pvid": "",
                "remote_vlans": [],
                "remote_lldp_med": {},
                "neighbor_source": "",
            },
            "side_b": None,
            "mismatches": ["unidirectional_lldp"],
            "link_verified": False,
            "link_direction": "undirected",
        }
    ]
    table = format_topology_table(edges)
    assert "leaf-01" in table
    assert "spine-01" in table
    assert "Ethernet5" in table
    assert "Ethernet3" in table
    assert "unidirectional_lldp" in table


def test_format_mermaid_merged_edge_shows_both_ports():
    """Verified bidirectional link: --- syntax with both ports."""
    nodes = [
        {"id": "SN1", "label": "leaf-01"},
        {"id": "mac:aa:bb:cc:dd:ee:01", "label": "spine-01"},
    ]
    links = [
        {
            "source_id": "SN1",
            "target_id": "mac:aa:bb:cc:dd:ee:01",
            "local_port": "Ethernet5",
            "remote_port_id": "Ethernet3",
            "link_verified": True,
            "mismatches": [],
        }
    ]
    mmd = format_topology_mermaid(nodes, links)
    assert "---" in mmd
    assert "Ethernet5" in mmd
    assert "Ethernet3" in mmd
    assert "-->" not in mmd


def test_format_mermaid_unidirectional_edge():
    """Unidirectional link: -.-> dotted syntax."""
    nodes = [
        {"id": "SN1", "label": "leaf-01"},
        {"id": "mac:aa:bb:cc:dd:ee:01", "label": "spine-01"},
    ]
    links = [
        {
            "source_id": "SN1",
            "target_id": "mac:aa:bb:cc:dd:ee:01",
            "local_port": "Ethernet5",
            "remote_port_id": "Ethernet3",
            "link_verified": False,
            "mismatches": ["unidirectional_lldp"],
        }
    ]
    mmd = format_topology_mermaid(nodes, links)
    assert "-.->" in mmd
    assert "Ethernet5" in mmd


@patch(
    "cvp_mcp.grpc.network_map._collect_inventory",
    return_value=([], []),
)
@patch(
    "cvp_mcp.grpc.network_map.scan_lldp_topology_edges",
    return_value=(
        [],
        {
            "devices_scanned": 0,
            "port_probes": 0,
            "edges_found": 0,
            "extra_neighbor_index_probes": 0,
            "devices_port_source_oper_up": 0,
            "devices_port_source_ethernet_range": 0,
            "devices_skipped_no_oper_up_ports": 0,
            "unidirectional_links": 0,
            "mismatched_links": 0,
            "lldp_port_source": "auto",
            "inventory_warnings": [],
        },
    ),
)
def test_grpc_map_network_topology_stats_include_new_fields(
    _mock_scan: object,
    _mock_inv: object,
) -> None:
    out = grpc_map_network_topology(
        {"cvp": "x:443", "cvtoken": "t"},
    )
    stats = out["topology"]["stats"]
    assert "unidirectional_links" in stats
    assert "mismatched_links" in stats
