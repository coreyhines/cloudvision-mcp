# tests/test_endpoint_seed.py
from unittest.mock import MagicMock, patch

from cvp_mcp.grpc.endpoint_seed import (
    _is_eligible_switch,
    extract_endpoint_search_keys,
    normalize_endpoint_search_key,
    seed_endpoint_search_keys,
)


def test_normalize_strips_and_lowercases_hostname():
    assert normalize_endpoint_search_key("  Pi5.FreeBlizz.com ") == "pi5.freeblizz.com"


def test_normalize_mac_to_colon_lowercase():
    assert normalize_endpoint_search_key("2CCF67E1DAFC") == "2c:cf:67:e1:da:fc"
    assert normalize_endpoint_search_key("2c-cf-67-e1-da-fc") == "2c:cf:67:e1:da:fc"


def test_normalize_rejects_empty():
    assert normalize_endpoint_search_key("") is None
    assert normalize_endpoint_search_key("   ") is None


def test_extract_prefers_ip_then_mac_then_name_and_dedupes():
    rows = [
        {
            "management_addresses": ["10.0.2.2", "10.0.2.2"],
            "remote_chassis_id": "2c:cf:67:e1:da:fc",
            "system_name": "pi5",
        },
        {
            "management_address": "10.0.3.2",
            "eth_addr": "38:05:25:30:6f:05",
            "system_name_str": "strongpod",
        },
        {
            "chassis_id_str": "2C:CF:67:E1:DA:FC",  # dup mac of row0
            "system_name": "pi5",  # dup name
        },
    ]
    keys = extract_endpoint_search_keys(rows)
    assert keys == [
        "10.0.2.2",
        "10.0.3.2",
        "2c:cf:67:e1:da:fc",
        "38:05:25:30:6f:05",
        "pi5",
        "strongpod",
    ]


def test_seed_endpoint_search_keys_from_lldp_inventory():
    datadict = {"cvp": "x:443", "cvtoken": "t"}
    channel = MagicMock()
    active = [
        {
            "serial_number": "SN1",
            "hostname": "720xp-24",
            "model": "CCS-720XP-24ZY4",
            "streaming_status": "Active",
            "device_type": "EOS",
        }
    ]
    lldp = {
        "items": [
            {
                "management_address": "10.0.2.2",
                "remote_chassis_id": "2c:cf:67:e1:da:fc",
                "system_name": "pi5",
            }
        ],
        "warnings": [],
    }
    with patch(
        "cvp_mcp.grpc.endpoint_seed.grpc_all_inventory",
        return_value=(active, []),
    ):
        with patch(
            "cvp_mcp.grpc.endpoint_seed.grpc_get_lldp_neighbors",
            return_value=lldp,
        ) as lldp_fn:
            result = seed_endpoint_search_keys(datadict, channel)

    lldp_fn.assert_called_once()
    assert result["search_keys"][0] == "10.0.2.2"
    assert result["seed_stats"]["switches_scanned"] == 1
    assert result["seed_stats"]["lldp_neighbor_rows"] == 1
    assert result["seed_stats"]["unique_search_keys"] == 3


def test_is_eligible_switch_virtual_eos_respects_include_lab_devices_flag():
    virtual = {
        "streaming_status": "Active",
        "device_type": "Virtual EOS",
    }
    assert _is_eligible_switch(virtual, include_lab_devices=False) is False
    assert _is_eligible_switch(virtual, include_lab_devices=True) is True


def test_is_eligible_switch_rejects_inactive_ap_and_other_types():
    active_eos = {"streaming_status": "Active", "device_type": "EOS"}
    assert _is_eligible_switch(active_eos, include_lab_devices=False) is True

    inactive_eos = {"streaming_status": "Inactive", "device_type": "EOS"}
    assert _is_eligible_switch(inactive_eos, include_lab_devices=True) is False

    ap = {"streaming_status": "Active", "device_type": "Access Point"}
    assert _is_eligible_switch(ap, include_lab_devices=True) is False


def test_seed_respects_device_serials_allowlist():
    datadict = {"cvp": "x:443", "cvtoken": "t"}
    channel = MagicMock()
    with patch(
        "cvp_mcp.grpc.endpoint_seed.grpc_all_inventory",
        return_value=(
            [
                {
                    "serial_number": "SN1",
                    "streaming_status": "Active",
                    "device_type": "EOS",
                    "model": "X",
                },
                {
                    "serial_number": "SN2",
                    "streaming_status": "Active",
                    "device_type": "EOS",
                    "model": "Y",
                },
            ],
            [],
        ),
    ):
        with patch(
            "cvp_mcp.grpc.endpoint_seed.grpc_get_lldp_neighbors",
            return_value={"items": [], "warnings": []},
        ) as lldp_fn:
            seed_endpoint_search_keys(datadict, channel, device_serials=["SN2"])
    assert lldp_fn.call_count == 1
    assert lldp_fn.call_args.args[1] == "SN2"
