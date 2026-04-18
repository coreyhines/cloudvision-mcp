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


def test_grpc_get_lldp_neighbors_missing_id():
    out = grpc_get_lldp_neighbors({"cvp": "x:443", "cvtoken": "t"}, "")
    assert out["coverage"] == "none"
    assert out["warnings"] == ["missing_device_id"]
