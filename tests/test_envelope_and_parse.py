"""Tests for envelope and Sysdb parse helpers."""

from __future__ import annotations

from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.sysdb_parse import (
    merge_intfcfg_and_status,
    parse_switchport_vlan_rows,
)


def test_tool_envelope_has_expected_keys():
    e = tool_envelope(
        device_id="SN123",
        data_source="test",
        coverage="partial",
        items=[{"a": 1}],
        warnings=["w"],
    )
    assert e["device_id"] == "SN123"
    assert e["data_source"] == "test"
    assert e["coverage"] == "partial"
    assert e["items"] == [{"a": 1}]
    assert e["warnings"] == ["w"]
    assert "collected_at" in e


def test_merge_interface_config_and_status():
    cfg = {
        "Ethernet1": {
            "adminEnabledStateLocal": {"Name": "unknownEnabledState"},
            "description": "uplink",
            "mtu": {"value": 9214},
        }
    }
    st = {
        "Ethernet1": {
            "operStatus": {"Name": "intfOperUp"},
            "speedEnum": {"Name": "SPEED_100GB"},
            "inOctets": 1,
        }
    }
    rows = merge_intfcfg_and_status(cfg, st)
    assert len(rows) == 1
    assert rows[0]["interface_name"] == "Ethernet1"
    assert rows[0]["oper_status"] == "intfOperUp"
    assert rows[0]["speed"] == "SPEED_100GB"


def test_parse_switchport_vlan_rows():
    raw = {
        "Ethernet1": {
            "switchportMode": {"Name": "SWITCHPORT_MODE_TRUNK"},
            "accessVlan": {"value": 1},
            "trunkAllowedVlans": "1-100",
        }
    }
    rows = parse_switchport_vlan_rows(raw)
    assert rows[0]["switchport_mode"] == "SWITCHPORT_MODE_TRUNK"
