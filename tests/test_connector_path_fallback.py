"""Tests for serial-prefix tolerant device path queries.

The Connector Query already scopes dataset.name to the device serial, so a
leading serial repeated in pathElts returns zero keys on this tenant. lldp.py
worked only because it carried its own no-serial fallback.
"""

from unittest.mock import MagicMock, patch

from cvp_mcp.grpc import connector


def test_tries_path_without_leading_serial_first():
    calls = []

    def fake_get(_client, _dataset, path_elts):
        calls.append(list(path_elts))
        return {"Ethernet1": {"x": 1}}

    with patch.object(connector, "get_device_path", side_effect=fake_get):
        result = connector.get_device_path_either(
            MagicMock(), "SN1", ["SN1", "Sysdb", "environment"]
        )

    assert result == {"Ethernet1": {"x": 1}}
    assert calls == [["Sysdb", "environment"]]


def test_falls_back_to_serial_prefixed_path_when_stripped_is_empty():
    calls = []

    def fake_get(_client, _dataset, path_elts):
        calls.append(list(path_elts))
        return {} if path_elts[0] != "SN1" else {"got": "data"}

    with patch.object(connector, "get_device_path", side_effect=fake_get):
        result = connector.get_device_path_either(
            MagicMock(), "SN1", ["SN1", "Sysdb", "environment"]
        )

    assert result == {"got": "data"}
    assert calls == [["Sysdb", "environment"], ["SN1", "Sysdb", "environment"]]


def test_path_without_leading_serial_is_queried_once_as_given():
    calls = []

    def fake_get(_client, _dataset, path_elts):
        calls.append(list(path_elts))
        return {"ok": 1}

    with patch.object(connector, "get_device_path", side_effect=fake_get):
        connector.get_device_path_either(MagicMock(), "SN1", ["Sysdb", "environment"])

    assert calls == [["Sysdb", "environment"]]


def _batch(*notifs):
    return [{"notifications": list(notifs)}]


def test_keyed_get_preserves_interface_identity_per_notification():
    """Merging notifications loses the path; 61 interfaces collapsed into 39 attrs."""
    client = MagicMock()
    client.get.return_value = _batch(
        {
            "path_elements": ["Sysdb", "interface", "intfConfig", "Ethernet1"],
            "updates": {"description": "ds1815 po1234", "mtu": 9214},
        },
        {
            "path_elements": ["Sysdb", "interface", "intfConfig", "Ethernet2"],
            "updates": {"description": "uplink", "mtu": 1500},
        },
    )

    out = connector.get_device_path_keyed(client, "SN1", ["Sysdb", "interface"])

    assert set(out) == {"Ethernet1", "Ethernet2"}
    assert out["Ethernet1"]["description"] == "ds1815 po1234"
    assert out["Ethernet2"]["mtu"] == 1500


def test_keyed_get_falls_back_to_merge_when_notification_has_no_path():
    client = MagicMock()
    client.get.return_value = _batch({"path_elements": [], "updates": {"mtu": 1500}})

    out = connector.get_device_path_keyed(client, "SN1", ["Sysdb"])

    assert out == {"mtu": 1500}
