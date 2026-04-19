"""Tests for LLDP network map helpers (no live CVP)."""

from __future__ import annotations

import json
from unittest.mock import patch

import yaml

from cvp_mcp.grpc.network_map import (
    _filter_simple_ethernet_ports_by_cap,
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
            "local_hostname": "sw1",
            "local_port": "Ethernet6",
            "remote_system_name": "host-a",
            "remote_eth_addr": "aa:bb:cc:dd:ee:01",
            "remote_port_id": "eth0",
            "local_serial": "S1",
            "local_model": "",
            "remote_chassis_id": "",
            "neighbor_source": "",
        }
    ]
    assert "sw1" in format_topology_table(edges)
    mmd = format_topology_mermaid(
        [
            {"id": "S1", "label": "sw1"},
            {"id": "name:host-a", "label": "host-a"},
        ],
        [{"source_id": "S1", "target_id": "name:host-a", "local_port": "Ethernet6"}],
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
            "local_serial": "SN1",
            "local_hostname": "core",
            "local_model": "",
            "local_port": "Ethernet1",
            "remote_system_name": "other",
            "remote_chassis_id": "",
            "remote_eth_addr": "cc:cc:cc:cc:cc:cc",
            "remote_port_id": "e1",
            "neighbor_source": "",
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
            "local_serial": "SN2",
            "local_hostname": "leaf",
            "local_model": "",
            "local_port": "Ethernet1",
            "remote_system_name": "core",
            "remote_chassis_id": "ec:8a:48:04:30:c0",
            "remote_eth_addr": "",
            "remote_port_id": "Eth1",
            "neighbor_source": "",
        }
    ]
    nodes, links = build_topology_nodes_and_links(edges, inv)
    assert any(n["serial_number"] == "SN1" for n in nodes)
    assert links[0].get("matched_inventory") is True
    assert json.dumps({"nodes": nodes, "links": links})
