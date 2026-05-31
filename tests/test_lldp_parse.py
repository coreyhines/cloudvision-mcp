"""Tests for LLDP Connector helper and envelope."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import grpc

from cvp_mcp.grpc.lldp import (
    _dedupe_lldp_items,
    _grpc_get_lldp_neighbors_bulk,
    _infer_ethernet_port_count,
    _is_expected_probe_not_found,
    _lldp_l2discovery_literal_local_paths,
    _lldp_paths_for_port_name,
    _lldp_paths_sysdb_first_no_serial,
    _local_interface_from_path_hint,
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


def test_parse_lldp_extracts_rich_neighbor_fields():
    raw = {
        "neighborStatus": {
            "Ethernet10": {
                "1": {
                    "systemName": "edge-a",
                    "sysDesc": {"value": {"value": "downstream switch"}},
                    "managementAddress": "10.10.10.2",
                    "systemCapabilities": ["bridge", "router"],
                    "enabledCapabilities": ["bridge"],
                    "pvid": 120,
                    "vlans": [120, 121],
                    "lldpMedPolicy": {"voice": {"vlanId": 121}},
                }
            }
        }
    }
    items = parse_lldp_flat_to_items(raw)
    assert len(items) == 1
    row = items[0]
    assert row["management_address"] == "10.10.10.2"
    assert row["management_addresses"] == ["10.10.10.2"]
    assert row["system_description"] == "downstream switch"
    assert row["system_capabilities"] == ["bridge", "router"]
    assert row["enabled_system_capabilities"] == ["bridge"]
    assert row["pvid"] == "120"
    assert row["vlans"] == ["120", "121"]
    assert "lldp_med" in row


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


class _FakeNotFound(grpc.RpcError):
    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.NOT_FOUND


def test_expected_probe_not_found_detects_grpc_code():
    assert _is_expected_probe_not_found(_FakeNotFound())


@patch("cvp_mcp.grpc.lldp._get_path")
def test_grpc_get_lldp_neighbors_not_found_logs_debug_not_warning(
    mock_get_path: object, caplog: object
) -> None:
    mock_get_path.side_effect = _FakeNotFound()
    with caplog.at_level(logging.DEBUG):
        out = grpc_get_lldp_neighbors(
            {"cvp": "x:443", "cvtoken": "t"}, "SN1", port_name="Ethernet1"
        )
    assert out["coverage"] == "none"
    assert "no_lldp_data_at_known_paths" in out["warnings"]
    assert any("probe miss" in rec.message for rec in caplog.records)
    assert not any(
        rec.levelno >= logging.WARNING and "lldp connector:" in rec.message
        for rec in caplog.records
    )


@patch("cvp_mcp.grpc.lldp._get_path")
def test_grpc_get_lldp_neighbors_unexpected_error_still_warns(
    mock_get_path: object, caplog: object
) -> None:
    mock_get_path.side_effect = RuntimeError("boom")
    with caplog.at_level(logging.WARNING):
        grpc_get_lldp_neighbors(
            {"cvp": "x:443", "cvtoken": "t"}, "SN1", port_name="Ethernet1"
        )
    assert any(
        rec.levelno >= logging.WARNING and "lldp connector:" in rec.message
        for rec in caplog.records
    )


def test_infer_ethernet_port_count_720xp24():
    assert _infer_ethernet_port_count("CCS-720XP-24ZY4") == 24


def test_local_interface_from_path_hint():
    hint = (
        "SN/Sysdb/l2discovery/lldp/status/local/1/portStatus/Ethernet19/remoteSystem/*"
    )
    assert _local_interface_from_path_hint(hint) == "Ethernet19"


def test_dedupe_lldp_items_preserves_distinct_ports():
    rows = [
        {"local_interface": "Ethernet6", "neighbor_key": "1", "system_name": "pi5"},
        {
            "local_interface": "Ethernet19",
            "neighbor_key": "1",
            "system_name": "720xp-48",
        },
        {"local_interface": "Ethernet6", "neighbor_key": "1", "system_name": "pi5"},
    ]
    out = _dedupe_lldp_items(rows)
    assert len(out) == 2
    assert {r["local_interface"] for r in out} == {"Ethernet6", "Ethernet19"}


@patch("cvp_mcp.grpc.lldp._grpc_get_lldp_neighbors_probe")
@patch("cvp_mcp.grpc.lldp._fetch_lldp_snapshot")
@patch("cvp_mcp.grpc.lldp._lldp_bulk_port_names")
def test_bulk_lldp_sweep_merges_neighbors_from_multiple_ports(
    mock_ports: object,
    mock_fetch: object,
    mock_probe: object,
) -> None:
    mock_fetch.return_value = ({}, "")
    mock_ports.return_value = (["Ethernet6", "Ethernet19"], [])

    def _probe_side_effect(
        datadict: dict[str, object],
        device_id: str,
        port_name: str = "",
        remote_neighbor_key: str = "",
        *,
        _shared_client: object = None,
    ) -> dict[str, object]:
        if port_name == "Ethernet6":
            return {
                "items": [
                    {
                        "neighbor_key": "1",
                        "system_name": "pi5",
                        "neighbor_source": "remoteLeaf",
                    }
                ],
                "object": {"path_hint": "…/Ethernet6/remoteSystem/*"},
            }
        if port_name == "Ethernet19":
            return {
                "items": [
                    {
                        "neighbor_key": "1",
                        "system_name": "720xp-48",
                        "neighbor_source": "remoteSystem",
                    }
                ],
                "object": {"path_hint": "…/Ethernet19/remoteSystem/*"},
            }
        return {"items": [], "object": {}}

    mock_probe.side_effect = _probe_side_effect
    out = _grpc_get_lldp_neighbors_bulk(
        {"cvp": "x:443", "cvtoken": "t"},
        "JPE19151499",
        device_model="CCS-720XP-24ZY4",
    )
    assert out["coverage"] == "full"
    assert len(out["items"]) == 2
    by_port = {r["local_interface"]: r["system_name"] for r in out["items"]}
    assert by_port["Ethernet6"] == "pi5"
    assert by_port["Ethernet19"] == "720xp-48"
    assert out["object"]["bulk_sweep"] is True
    assert out["object"]["ports_probed"] == 2
    assert out["object"]["ports_with_neighbors"] == 2


@patch("cvp_mcp.grpc.lldp._grpc_get_lldp_neighbors_bulk")
def test_grpc_get_lldp_neighbors_uses_bulk_when_no_port(mock_bulk: object) -> None:
    mock_bulk.return_value = {"coverage": "full", "items": []}
    grpc_get_lldp_neighbors({"cvp": "x:443", "cvtoken": "t"}, "SN1")
    mock_bulk.assert_called_once()


@patch("cvp_mcp.grpc.lldp._grpc_get_lldp_neighbors_probe")
def test_grpc_get_lldp_neighbors_uses_probe_when_port_set(mock_probe: object) -> None:
    mock_probe.return_value = {"coverage": "full", "items": []}
    grpc_get_lldp_neighbors(
        {"cvp": "x:443", "cvtoken": "t"}, "SN1", port_name="Ethernet6"
    )
    mock_probe.assert_called_once()
