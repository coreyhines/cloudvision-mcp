"""device.lag_members: Port-Channel -> physical member ports, without config text."""

from unittest.mock import MagicMock, patch

from cloudvision.Connector.codec import Path

from cvp_mcp.grpc import lag
from cvp_mcp.grpc.lag import parse_lag_membership, port_channel_name

_PO8_PTR = Path(
    ["Sysdb", "interface", "config", "eth", "lag", "intfConfig", "Port-Channel8"]
)


def test_port_channel_name_accepts_path_list_dict_and_text():
    assert port_channel_name(_PO8_PTR) == "Port-Channel8"
    assert port_channel_name(["Sysdb", "x", "Port-Channel10"]) == "Port-Channel10"
    assert (
        port_channel_name({"ptr": ["Sysdb", "x", "Port-Channel1"]}) == "Port-Channel1"
    )
    assert port_channel_name("lag -> Port-Channel12") == "Port-Channel12"
    assert port_channel_name(None) is None
    assert port_channel_name(["Sysdb", "lag", "input"]) is None


def test_parse_groups_configured_and_live_members_per_port_channel():
    phy_cfg = {
        "Ethernet8/1": {"lag": _PO8_PTR, "mode": {"Name": "active", "Value": 1}},
        "Ethernet27/1": {"lag": ["Sysdb", "Port-Channel1"], "mode": "active"},
        "Ethernet28/1": {"lag": ["Sysdb", "Port-Channel1"], "mode": "active"},
        "Ethernet5/1": {"description": "no lag here"},
        "memberStatusLogEnabled": True,  # scalar sibling, must be ignored
    }
    live = {
        "Port-Channel1": {"Ethernet27/1": {}, "Ethernet28/1": {}},
        "Port-Channel8": {"Ethernet8/1": {}},
    }
    rows = parse_lag_membership(phy_cfg, live)
    assert [r["port_channel"] for r in rows] == ["Port-Channel1", "Port-Channel8"]
    po8 = rows[1]
    assert po8["configured_members"] == ["Ethernet8/1"]
    assert po8["active_members"] == ["Ethernet8/1"]
    assert po8["lacp_mode"] == "active"
    assert rows[0]["configured_members"] == ["Ethernet27/1", "Ethernet28/1"]


def _fake_keyed(_client, _device_id, _path_elts):
    return {
        "Ethernet8/1": {
            "lag": _PO8_PTR,
            "mode": {"Name": "lacpModeActive", "Value": 1},
        },
        "Ethernet27/1": {"lag": ["Sysdb", "Port-Channel1"]},
        "memberStatusLogEnabled": True,
    }


def _fake_live(_client, _device_id):
    return {"Port-Channel8": {"Ethernet8/1": {}}, "Port-Channel1": {"Ethernet27/1": {}}}


def _patched():
    return (
        patch.object(lag, "GRPCClient", MagicMock()),
        patch.object(lag, "get_device_path_keyed", side_effect=_fake_keyed),
        patch.object(lag, "_live_members_by_port_channel", side_effect=_fake_live),
    )


def test_lag_members_resolves_port_channel_to_physical_port():
    a, b, c = _patched()
    with a, b, c:
        result = lag.grpc_get_lag_members({"cvp": "cv:443"}, "SN1", "Po8")
    assert result["coverage"] == "full"
    assert result["object"]["port_channel_filter"] == "Port-Channel8"
    assert result["items"] == [
        {
            "port_channel": "Port-Channel8",
            "configured_members": ["Ethernet8/1"],
            "active_members": ["Ethernet8/1"],
            "lacp_mode": "active",
        }
    ]


def test_lag_members_unknown_port_channel_is_a_warning_not_an_error():
    a, b, c = _patched()
    with a, b, c:
        result = lag.grpc_get_lag_members({"cvp": "cv:443"}, "SN1", "Port-Channel99")
    assert result["coverage"] == "none"
    assert result["items"] == []
    assert "port_channel_not_found" in result["warnings"]


def test_lag_members_without_filter_lists_every_port_channel():
    a, b, c = _patched()
    with a, b, c:
        result = lag.grpc_get_lag_members({"cvp": "cv:443"}, "SN1")
    assert [r["port_channel"] for r in result["items"]] == [
        "Port-Channel1",
        "Port-Channel8",
    ]


def test_lag_members_connector_failure_returns_envelope():
    with patch.object(lag, "GRPCClient", MagicMock(side_effect=RuntimeError("boom"))):
        result = lag.grpc_get_lag_members({"cvp": "cv:443"}, "SN1")
    assert result["coverage"] == "none"
    assert result["warnings"] == ["boom"]


def test_device_group_exposes_lag_members_action():
    from cvp_mcp.members import device

    spec = device.members()["lag_members"]
    assert spec.required == ["device_id"]
    assert set(spec.properties) == {"device_id", "port_channel"}


def test_live_members_are_keyed_by_port_channel_not_collapsed():
    """Every notification ends in the literal "member"; keying by the last path
    element would keep only one Port-Channel. Key by the element before it."""
    client = MagicMock()
    client.get.return_value = [
        {
            "notifications": [
                {
                    "path_elements": [
                        "Sysdb",
                        "lag",
                        "input",
                        "interface",
                        "lag",
                        "intfStatus",
                        "Port-Channel1",
                        "member",
                    ],
                    "updates": {"Ethernet27/1": {}, "Ethernet28/1": {}},
                },
                {
                    "path_elements": [
                        "Sysdb",
                        "lag",
                        "input",
                        "interface",
                        "lag",
                        "intfStatus",
                        "Port-Channel8",
                        "member",
                    ],
                    "updates": {"Ethernet8/1": {}},
                },
                {
                    "path_elements": [
                        "Sysdb",
                        "lag",
                        "input",
                        "interface",
                        "lag",
                        "intfStatus",
                    ],
                    "updates": {"Port-Channel1": {}},
                },
            ]
        }
    ]
    with patch.object(lag, "create_query", lambda *_a, **_k: "q"):
        live = lag._live_members_by_port_channel(client, "SN1")
    assert live == {
        "Port-Channel1": {"Ethernet27/1": {}, "Ethernet28/1": {}},
        "Port-Channel8": {"Ethernet8/1": {}},
    }


def test_lacp_mode_drops_the_eos_enum_prefix():
    rows = parse_lag_membership(
        {"Ethernet1": {"lag": _PO8_PTR, "mode": {"Name": "lacpModeActive"}}}, {}
    )
    assert rows[0]["lacp_mode"] == "active"
    rows = parse_lag_membership({"Ethernet1": {"lag": _PO8_PTR, "mode": "on"}}, {})
    assert rows[0]["lacp_mode"] == "on"
