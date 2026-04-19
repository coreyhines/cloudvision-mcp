"""Tests for LLDP Connector helper and envelope."""

from __future__ import annotations

import json
from pathlib import Path

from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors, parse_lldp_flat_to_items


def test_parse_lldp_flat_to_items_from_fixture():
    raw = json.loads(
        Path(__file__)
        .resolve()
        .parent.joinpath("fixtures/lldp_flat_sample.json")
        .read_text()
    )
    items = parse_lldp_flat_to_items(raw)
    assert len(items) >= 1
    assert items[0]["local_interface"] == "Ethernet1"
    assert items[0].get("system_name") == "remote-switch.example.com"


def test_parse_lldp_neighbor_status_wrapper():
    wrapped = {
        "neighborStatus": {
            "Ethernet2": {
                "aa:bb:cc:dd:ee:ff": {
                    "systemName": "other.example.com",
                }
            }
        }
    }
    items = parse_lldp_flat_to_items(wrapped)
    assert len(items) == 1
    assert items[0]["local_interface"] == "Ethernet2"


def test_parse_lldp_neighbor_status_nested_wrapper():
    wrapped = {
        "status": {
            "neighborStatus": {
                "Ethernet3": {
                    "11:22:33:44:55:66": {
                        "systemName": "nested.example.com",
                    }
                }
            }
        }
    }
    items = parse_lldp_flat_to_items(wrapped)
    assert len(items) == 1
    assert items[0]["local_interface"] == "Ethernet3"
    assert items[0].get("system_name") == "nested.example.com"


def test_parse_l2discovery_lldp_fixture():
    raw = json.loads(
        Path(__file__)
        .resolve()
        .parent.joinpath("fixtures/lldp_l2discovery_sample.json")
        .read_text()
    )
    items = parse_lldp_flat_to_items(raw)
    assert len(items) == 2
    by_intf = {r["local_interface"]: r for r in items}
    row5 = by_intf["Ethernet5"]
    assert row5["neighbor_key"] == "1"
    assert row5["neighbor_source"] == "remoteSystem"
    assert row5["system_name"] == "ztx-7230.freeblizz.com"
    assert row5["remote_chassis_id"] == "ec:8a:48:04:30:c0"
    assert row5["remote_port_id"] == "Management1/1"
    assert row5["ttl_sec"] == 120
    row6 = by_intf["Ethernet6"]
    assert row6["neighbor_source"] == "remoteSystemByMsap"
    assert row6["system_name"] == "rpi4-0"
    assert row6["eth_addr"] == "aa:bb:cc:dd:ee:ff"


def test_parse_l2discovery_remote_leaf_only():
    raw = {
        "index": 1,
        "sysName": {
            "isSet": True,
            "value": {"value": "leaf-only.example.com"},
        },
        "msap": {
            "portIdentifier": {"portId": "Ethernet1"},
            "chassisIdentifier": {"chassisId": "aa:bb:cc:dd:ee:ff"},
        },
    }
    items = parse_lldp_flat_to_items(raw)
    assert len(items) == 1
    assert items[0]["system_name"] == "leaf-only.example.com"
    assert items[0]["local_interface"] == ""
    assert items[0]["neighbor_source"] == "remoteLeaf"


def test_grpc_get_lldp_neighbors_missing_id():
    out = grpc_get_lldp_neighbors({"cvp": "x:443", "cvtoken": "t"}, "")
    assert out["coverage"] == "none"
    assert out["warnings"] == ["missing_device_id"]
    assert out["data_source"] == "connector:device:Sysdb/l2discovery/lldp"
