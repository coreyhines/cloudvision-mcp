# tests/test_endpoint_lookup.py
from unittest.mock import MagicMock, patch

import cloudvision_mcp as mcp_mod
from cvp_mcp.grpc import endpoint


def test_grpc_endpoints_for_search_keys_uses_getsome_not_getall():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetAll = MagicMock(side_effect=AssertionError("GetAll must not be called"))

    # One streamed GetSome response with value + empty error
    resp = MagicMock()
    resp.HasField.side_effect = lambda f: f == "value"
    resp.error = MagicMock()
    device = MagicMock()
    # _device_map_entries path: response.value has device_map
    # Simpler: patch _device_map_entries + convert
    stub.GetSome.return_value = [resp]

    converted = {
        "hostname": "pi5",
        "mac_address": "2c:cf:67:e1:da:fc",
        "ip_address": "10.0.2.2",
        "location_list": [
            {
                "device_id": {"value": "JPE19151499"},
                "interface": {"value": "Ethernet6"},
                "vlan_id": {"value": 2},
            }
        ],
    }

    with patch.object(
        endpoint.services, "EndpointLocationServiceStub", return_value=stub
    ):
        with patch.object(
            endpoint, "_device_map_entries", return_value=[("k", device)]
        ):
            with patch.object(
                endpoint,
                "convert_response_to_endpoint_location",
                return_value=converted,
            ):
                result = endpoint.grpc_endpoints_for_search_keys(channel, ["10.0.2.2"])

    stub.GetSome.assert_called_once()
    stub.GetAll.assert_not_called()
    assert result["method"] == "getsome"
    assert result["hits"] == 1
    assert result["endpoints"] == [converted]


def test_getsome_failure_falls_back_to_getone():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetSome.side_effect = RuntimeError(
        "GetSome of EndpointLocation is not allowed"
    )
    converted = {
        "hostname": "pi5",
        "mac_address": "2c:cf:67:e1:da:fc",
        "ip_address": "10.0.2.2",
        "location_list": [],
    }

    with patch.object(
        endpoint.services, "EndpointLocationServiceStub", return_value=stub
    ):
        with patch.object(
            endpoint, "grpc_one_endpoint_location", return_value=[converted]
        ) as one:
            result = endpoint.grpc_endpoints_for_search_keys(
                channel, ["10.0.2.2", "pi5"]
            )

    assert result["method"] == "getone"
    assert "getsome_failed" in ",".join(result["warnings"])
    assert one.call_count == 2
    assert result["hits"] == 2


def test_endpoint_location_matches_filters():
    ep = {
        "hostname": "pi5",
        "mac_address": "",
        "ip_address": "",
        "location_list": [
            {
                "device_id": {"value": "JPE19151499"},
                "interface": {"value": "Ethernet6"},
                "vlan_id": {"value": 2},
            }
        ],
    }
    assert endpoint.endpoint_location_matches_filters(
        ep, device_id="JPE19151499", interface=None, vlan_id=None
    )
    assert endpoint.endpoint_location_matches_filters(
        ep, device_id="JPE19151499", interface="Ethernet6", vlan_id=2
    )
    assert not endpoint.endpoint_location_matches_filters(
        ep, device_id="OTHER", interface=None, vlan_id=None
    )
    assert not endpoint.endpoint_location_matches_filters(
        ep, device_id=None, interface="Ethernet1", vlan_id=None
    )


def test_get_cvp_all_endpoint_locations_pipeline(monkeypatch):
    monkeypatch.setattr(mcp_mod, "CVP_TRANSPORT", "grpc")
    monkeypatch.setattr(
        mcp_mod, "get_env_vars", lambda: {"cvp": "h:443", "cvtoken": "t"}
    )
    monkeypatch.setattr(mcp_mod, "createConnection", lambda d: MagicMock())

    fake_channel = MagicMock()
    fake_channel.__enter__ = lambda s: fake_channel
    fake_channel.__exit__ = lambda *a: False

    with patch("cloudvision_mcp.grpc.secure_channel", return_value=fake_channel):
        with patch(
            "cloudvision_mcp.seed_endpoint_search_keys",
            return_value={
                "search_keys": ["10.0.2.2"],
                "seed_stats": {
                    "switches_scanned": 1,
                    "lldp_neighbor_rows": 1,
                    "unique_search_keys": 1,
                },
                "warnings": [],
            },
        ):
            with patch(
                "cloudvision_mcp.grpc_endpoints_for_search_keys",
                return_value={
                    "endpoints": [
                        {
                            "hostname": "pi5",
                            "mac_address": "2c:cf:67:e1:da:fc",
                            "ip_address": "10.0.2.2",
                            "location_list": [
                                {
                                    "device_id": {"value": "SN1"},
                                    "interface": {"value": "Ethernet6"},
                                    "vlan_id": {"value": 2},
                                }
                            ],
                        }
                    ],
                    "hits": 1,
                    "misses": 0,
                    "warnings": [],
                    "method": "getsome",
                },
            ):
                with patch(
                    "cloudvision_mcp.grpc_one_inventory_serial",
                    return_value={"serial_number": "SN1", "hostname": "720xp-24"},
                ):
                    out = mcp_mod.get_cvp_all_endpoint_locations()

    assert out["endpoints"][0]["hostname"] == "pi5"
    assert out["seed_stats"]["unique_search_keys"] == 1
    assert out["seed_stats"]["getsome_hits"] == 1
    assert "SN1" in out["devices"]
