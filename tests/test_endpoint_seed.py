# tests/test_endpoint_seed.py
from cvp_mcp.grpc.endpoint_seed import (
    extract_endpoint_search_keys,
    normalize_endpoint_search_key,
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
