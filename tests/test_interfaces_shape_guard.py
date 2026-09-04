"""Guard against the 1.64 regression: attribute names served as interface names.

1.64 returned coverage="full" with interface_name values like "linkStatus",
"duplex" and "mtu" - the attributes of one interface, merged, because
connector.get dropped each notification's path. Confident fiction is worse
than an honest empty result, so these tests assert on real names.
"""

from unittest.mock import MagicMock, patch

from cvp_mcp.grpc import interfaces

# Attribute names seen in the bad 1.64 output; none is an interface.
_ATTRIBUTE_NAMES = {
    "linkStatus",
    "duplex",
    "fecEncoding",
    "mtu",
    "addr",
    "description",
    "operStatus",
    "speed",
}


def _fake_keyed(_client, _device_id, path_elts):
    tail = [p for p in path_elts if isinstance(p, str)]
    if "status" in tail:
        return {
            "Ethernet1": {"operStatus": "intfOperUp", "speedEnum": "speed10Gbps"},
            "Ethernet2": {"operStatus": "intfOperDown"},
        }
    return {
        "Ethernet1": {"description": "ds1815 po1234", "mtu": 9214},
        "Ethernet2": {"description": "uplink", "mtu": 1500},
    }


def test_interfaces_report_real_interface_names():
    with patch.object(interfaces, "GRPCClient", MagicMock()):
        with patch.object(interfaces, "get_device_path_keyed", side_effect=_fake_keyed):
            result = interfaces.grpc_get_interfaces({"cvp": "cv:443"}, "SN1")

    names = [row["interface_name"] for row in result["items"]]
    assert names == ["Ethernet1", "Ethernet2"]
    assert result["coverage"] == "full"


def test_interfaces_never_report_attribute_names_as_interfaces():
    with patch.object(interfaces, "GRPCClient", MagicMock()):
        with patch.object(interfaces, "get_device_path_keyed", side_effect=_fake_keyed):
            result = interfaces.grpc_get_interfaces({"cvp": "cv:443"}, "SN1")

    leaked = _ATTRIBUTE_NAMES & {row["interface_name"] for row in result["items"]}
    assert not leaked, f"attribute names served as interfaces: {sorted(leaked)}"


def test_interfaces_carry_their_own_description_not_a_neighbours():
    with patch.object(interfaces, "GRPCClient", MagicMock()):
        with patch.object(interfaces, "get_device_path_keyed", side_effect=_fake_keyed):
            result = interfaces.grpc_get_interfaces({"cvp": "cv:443"}, "SN1")

    by_name = {row["interface_name"]: row for row in result["items"]}
    assert by_name["Ethernet1"]["description"] == "ds1815 po1234"
    assert by_name["Ethernet2"]["description"] == "uplink"
