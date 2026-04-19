"""Tests for LLDP Connector helper and envelope."""

from __future__ import annotations

import json
from pathlib import Path

from cvp_mcp.grpc.lldp import (
    _lldp_l2discovery_literal_local_paths,
    _lldp_paths_for_port_name,
    _lldp_paths_sysdb_first_no_serial,
    grpc_get_lldp_neighbors,
    parse_lldp_flat_to_items,
)


def test_explicit_port_paths_include_remote_system_one():
    paths = _lldp_paths_for_port_name("D1", "Ethernet6", "")
    assert any(len(p) >= 2 and p[-2:] == ["remoteSystem", "1"] for p in paths)


def test_parse_remote_leaf_ethaddr_and_index():
    raw = {"index": 1, "ethAddr": "dc:a6:32:02:73:43"}
    items = parse_lldp_flat_to_items(raw)
    assert len(items) == 1
    assert items[0]["eth_addr"] == "dc:a6:32:02:73:43"


def test_port_scoped_sysdb_first_skips_other_ports():
    paths = _lldp_paths_sysdb_first_no_serial(
        ports=("Ethernet3",), include_wildcard_portstatus=False
    )
    strs = [[x for x in p if isinstance(x, str)] for p in paths]
    assert all("Ethernet3" in s for s in strs)
    assert not any("Ethernet6" in s for s in strs)


def test_sysdb_first_paths_match_telemetry_shape_no_serial():
    paths = _lldp_paths_sysdb_first_no_serial()
    assert paths, "expected at least one candidate path"
    assert paths[0][0] == "Sysdb"
    eth6_remote1 = next(
        p
        for p in paths
        if "portStatus" in p
        and p[p.index("portStatus") + 1] == "Ethernet6"
        and p[-2:] == ["remoteSystem", "1"]
    )
    assert eth6_remote1[:8] == [
        "Sysdb",
        "l2discovery",
        "lldp",
        "status",
        "local",
        "1",
        "portStatus",
        "Ethernet6",
    ]


def test_l2discovery_literal_local_paths_use_instance_one_and_zero():
    """Match Aeris paths like …/local/1/portStatus/Ethernet6/remoteSystem/1/."""
    paths = _lldp_l2discovery_literal_local_paths("SN99")
    lids = {
        p[6] for p in paths if len(p) >= 8 and p[5] == "local" and p[7] == "portStatus"
    }
    assert lids == {"0", "1"}


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


def test_grpc_get_lldp_neighbors_missing_server_credentials():
    out = grpc_get_lldp_neighbors({"cvp": "", "cvtoken": ""}, "SN1")
    assert out["coverage"] == "none"
    assert "missing_CVP" in out["warnings"]
    assert "missing_CVPTOKEN" in out["warnings"]
    assert "mcp_server_missing_cloudvision_credentials" in out["warnings"]
    assert out.get("object", {}).get("hint", "")
