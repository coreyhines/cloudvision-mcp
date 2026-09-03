"""Smoke tests for extracted grouped-tool members."""

from unittest.mock import MagicMock

import pytest

from cvp_mcp.tool_groups import build_groups


def test_inventory_get_returns_dict_not_str(monkeypatch):
    """Inventory get returns its structured device object directly."""
    from cvp_mcp.members import inventory

    channel = MagicMock()
    channel_context = MagicMock()
    channel_context.__enter__.return_value = channel
    monkeypatch.setattr(
        inventory, "env_datadict_from_os", lambda: {"cvp": "cvp.example"}
    )
    monkeypatch.setattr(inventory, "createConnection", lambda _env: MagicMock())
    monkeypatch.setattr(
        inventory.grpc, "secure_channel", lambda _target, _creds: channel_context
    )
    monkeypatch.setattr(
        inventory,
        "resolve_device_to_serial",
        lambda _env, _device_id, channel=None: (
            "SERIAL1",
            {"serial_number": "SERIAL1"},
            [],
            [],
        ),
    )

    out = inventory.inventory_get("SERIAL1")

    assert isinstance(out, dict)
    assert out == {"serial_number": "SERIAL1"}


@pytest.mark.parametrize(
    ("group_name", "action", "params"),
    [
        ("inventory", "get", {"device_id": "SERIAL1"}),
        ("endpoints", "get", {"search_term": "10.0.0.1"}),
        ("device", "config", {"device_id": "SERIAL1"}),
        ("overlay", "evpn", {"device_id": "SERIAL1"}),
        ("routing", "bgp", {"device_id": "SERIAL1"}),
    ],
)
def test_batch_a_group_dispatch(group_name, action, params):
    """Each batch A group dispatches an action to its extracted member."""
    group = next(group for group in build_groups() if group.name == group_name)
    call = MagicMock(return_value={"group": group_name, "action": action})
    group.members[action].call = call

    result = group.execute({"action": action, **params})

    assert result == {"group": group_name, "action": action}
    call.assert_called_once_with(**params)


def test_inventory_list_uses_group_rate_limit_key():
    """Inventory list is rate-limited under its grouped action key."""
    group = next(group for group in build_groups() if group.name == "inventory")

    assert group.members["list"].rate_limit_key == "inventory.list"


def test_batch_a_group_actions_match_frozen_catalog():
    """Extracted groups expose exactly their catalog actions."""
    groups = {group.name: set(group.members) for group in build_groups()}

    assert groups == {
        "inventory": {"get", "list", "search"},
        "endpoints": {"get", "list", "filter"},
        "device": {
            "config",
            "interfaces",
            "vlans",
            "ip_interfaces",
            "features",
            "health",
        },
        "overlay": {"evpn", "vxlan"},
        "routing": {"bgp", "routes"},
    }
