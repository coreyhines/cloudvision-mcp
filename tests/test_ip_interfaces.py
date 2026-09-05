"""L3 addresses come from Sysdb/ip/config/ipIntfConfig.

The old code queried Sysdb/ip/config/addr/* and .../ifAddr/*, neither of
which exists; probing 720xp-48 showed Sysdb/ip/config has a single child,
ipIntfConfig, holding Vlan5, Loopback1, Loopback100 and Management1. The
address field is addrWithMask, not address/prefix/ipAddr.
"""

from unittest.mock import MagicMock, patch

from cvp_mcp.grpc import interfaces

# Shape confirmed by meta(action="probe_path") against 720xp-48.
_KEYED = {
    "Loopback1": {
        "name": "Loopback1",
        "addrWithMask": "172.16.0.2/32",
        "addrSource": {"Name": "configured", "Value": 1},
    },
    "Loopback100": {
        "name": "Loopback100",
        "addrWithMask": "81.81.81.81/32",
        "addrSource": {"Name": "configured", "Value": 1},
    },
    "Management1": {
        "name": "Management1",
        "addrWithMask": "10.0.10.45/24",
        "addrSource": {"Name": "dhcp", "Value": 2},
    },
    # Configured for DHCP with no lease yet: still an L3 interface.
    "Vlan5": {"name": "Vlan5", "addrWithMask": ""},
}


def _run():
    with patch.object(interfaces, "GRPCClient", MagicMock()):
        with patch.object(
            interfaces, "get_device_path_keyed", return_value=dict(_KEYED)
        ):
            return interfaces.grpc_get_ip_interfaces({"cvp": "cv:443"}, "SN1")


def test_ip_interface_paths_try_ipintfconfig_first():
    """The legacy addr/ifAddr paths stay as fallbacks for other platforms,
    but the one that actually exists here must be tried first."""
    rendered = [
        "/".join(interfaces.render_path(p))
        for p in interfaces.ip_interface_paths("SN1")
    ]
    assert rendered[0].endswith("Sysdb/ip/config/ipIntfConfig/*")


def test_reports_addresses_from_addr_with_mask():
    result = _run()
    by_intf = {r["interface"]: r for r in result["items"]}
    assert by_intf["Loopback1"]["address"] == "172.16.0.2/32"
    assert by_intf["Loopback100"]["address"] == "81.81.81.81/32"
    assert result["coverage"] == "full"


def test_infers_address_family():
    by_intf = {r["interface"]: r for r in _run()["items"]}
    assert by_intf["Loopback1"]["address_family"] == "ipv4"


def test_reports_address_origin():
    by_intf = {r["interface"]: r for r in _run()["items"]}
    assert by_intf["Management1"]["origin"] == "dhcp"
    assert by_intf["Loopback1"]["origin"] == "configured"


def test_keeps_l3_interface_with_no_address_yet():
    """A DHCP interface without a lease must not vanish."""
    by_intf = {r["interface"]: r for r in _run()["items"]}
    assert "Vlan5" in by_intf
    assert by_intf["Vlan5"]["address"] == ""


def test_interface_names_are_not_nested_paths():
    """The old walker emitted prefixes like 'addr/1/2'; keys are interfaces."""
    for row in _run()["items"]:
        assert "/" not in row["interface"]
