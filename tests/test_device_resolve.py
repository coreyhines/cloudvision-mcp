"""Tests for centralized device_id -> serial resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc

from cvp_mcp.grpc import device_resolve


class _FakeNotFound(grpc.RpcError):
    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.NOT_FOUND


_PHYSICAL = {
    "hostname": "720xp-24",
    "model": "DCS-720XP-24YC2",
    "serial_number": "JPE19151499",
    "system_mac": "aa:bb:cc:dd:ee:01",
    "version": "4.35.0F",
    "streaming_status": "Active",
    "device_type": "EOS",
    "hardware_revision": "01.00",
    "fqdn": "720xp-24.example.com",
    "domain_name": "example.com",
}


def test_resolve_passthrough_serial():
    channel = MagicMock()
    with patch.object(
        device_resolve, "grpc_one_inventory_serial", return_value=_PHYSICAL
    ) as get_one:
        serial, info, warns = device_resolve.resolve_device_to_serial(
            {"cvp": "cvp.example:443", "cvtoken": "tok"},
            "JPE19151499",
            channel=channel,
        )
    get_one.assert_called_once_with(channel, "JPE19151499")
    assert serial == "JPE19151499"
    assert info == _PHYSICAL
    assert warns == []


def test_resolve_hostname_to_serial():
    channel = MagicMock()
    with patch.object(device_resolve, "grpc_one_inventory_serial", return_value=None):
        with patch.object(
            device_resolve,
            "grpc_one_device_by_hostname",
            return_value=_PHYSICAL,
        ) as by_host:
            serial, info, warns = device_resolve.resolve_device_to_serial(
                {"cvp": "cvp.example:443", "cvtoken": "tok"},
                "720xp-24",
                channel=channel,
            )
    by_host.assert_called()
    assert serial == "JPE19151499"
    assert info == _PHYSICAL
    assert warns == []


def test_resolve_fqdn_uses_short_hostname():
    channel = MagicMock()
    with patch.object(device_resolve, "grpc_one_inventory_serial", return_value=None):
        with patch.object(
            device_resolve,
            "grpc_one_device_by_hostname",
            side_effect=[None, _PHYSICAL],
        ) as by_host:
            serial, info, _warns = device_resolve.resolve_device_to_serial(
                {"cvp": "cvp.example:443", "cvtoken": "tok"},
                "720xp-24.example.com",
                channel=channel,
            )
    assert by_host.call_count >= 2
    assert serial == "JPE19151499"
    assert info == _PHYSICAL


def test_resolve_unknown_device():
    channel = MagicMock()
    with patch.object(device_resolve, "grpc_one_inventory_serial", return_value=None):
        with patch.object(
            device_resolve, "grpc_one_device_by_hostname", return_value=None
        ):
            with patch.object(
                device_resolve,
                "_inventory_lookup_device",
                return_value=({}, "not_found"),
            ):
                with patch.object(
                    device_resolve,
                    "grpc_all_inventory",
                    return_value=([], []),
                ):
                    serial, info, warns = device_resolve.resolve_device_to_serial(
                        {"cvp": "cvp.example:443", "cvtoken": "tok"},
                        "missing-switch",
                        channel=channel,
                    )
    assert serial is None
    assert info is None
    assert warns == []


def test_resolve_empty_device_id():
    serial, info, warns = device_resolve.resolve_device_to_serial({}, "")
    assert serial is None
    assert info is None
    assert warns == ["missing_device_id"]


def test_resolve_inventory_scan_by_mac():
    channel = MagicMock()
    with patch.object(device_resolve, "grpc_one_inventory_serial", return_value=None):
        with patch.object(
            device_resolve, "grpc_one_device_by_hostname", return_value=None
        ):
            with patch.object(
                device_resolve,
                "_inventory_lookup_device",
                return_value=({}, "not_found"),
            ):
                with patch.object(
                    device_resolve,
                    "grpc_all_inventory",
                    return_value=([_PHYSICAL], []),
                ):
                    serial, info, _warns = device_resolve.resolve_device_to_serial(
                        {"cvp": "cvp.example:443", "cvtoken": "tok"},
                        "aa:bb:cc:dd:ee:01",
                        channel=channel,
                    )
    assert serial == "JPE19151499"
    assert info == _PHYSICAL


@patch("cloudvision_mcp._resolve_device_serial")
@patch("cloudvision_mcp.grpc_get_lldp_neighbors")
@patch("cloudvision_mcp.grpc.secure_channel")
@patch("cloudvision_mcp.createConnection")
@patch("cloudvision_mcp.get_env_vars")
def test_lldp_tool_resolves_hostname_before_connector(
    mock_env: MagicMock,
    mock_conn: MagicMock,
    mock_channel: MagicMock,
    mock_lldp: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    from cloudvision_mcp import get_cvp_lldp_neighbors

    mock_env.return_value = {"cvp": "test.example:443", "cvtoken": "tok"}
    mock_conn.return_value = MagicMock()
    ctx = MagicMock()
    mock_channel.return_value.__enter__ = MagicMock(return_value=ctx)
    mock_channel.return_value.__exit__ = MagicMock(return_value=False)
    mock_resolve.return_value = ("JPE19151499", _PHYSICAL, [])
    mock_lldp.return_value = {
        "device_id": "JPE19151499",
        "coverage": "full",
        "items": [{"local_interface": "Ethernet1"}],
        "warnings": [],
        "object": {},
    }

    result = get_cvp_lldp_neighbors("720xp-24")

    mock_lldp.assert_called_once()
    assert mock_lldp.call_args[0][1] == "JPE19151499"
    assert result["device_id"] == "JPE19151499"
    assert result["object"]["device_id_input"] == "720xp-24"
    assert result["coverage"] == "full"


@patch("cloudvision_mcp._resolve_device_serial")
@patch("cloudvision_mcp.grpc.secure_channel")
@patch("cloudvision_mcp.createConnection")
@patch("cloudvision_mcp.get_env_vars")
def test_lldp_tool_unknown_device(
    mock_env: MagicMock,
    mock_conn: MagicMock,
    mock_channel: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    from cloudvision_mcp import get_cvp_lldp_neighbors

    mock_env.return_value = {"cvp": "test.example:443", "cvtoken": "tok"}
    mock_conn.return_value = MagicMock()
    ctx = MagicMock()
    mock_channel.return_value.__enter__ = MagicMock(return_value=ctx)
    mock_channel.return_value.__exit__ = MagicMock(return_value=False)
    mock_resolve.return_value = (None, None, [])

    result = get_cvp_lldp_neighbors("unknown-host")

    assert "device_not_found" in result.get("warnings", [])
    assert result["coverage"] == "none"
