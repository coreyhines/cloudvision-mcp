"""Tests for lab/virtual device exclusion from LLDP tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# _is_lab_device helper
# ---------------------------------------------------------------------------


def test_is_lab_device_returns_true_for_virtual_eos():
    from cvp_mcp.grpc.utils import _is_lab_device

    assert _is_lab_device({"device_type": "Virtual EOS"}) is True


def test_is_lab_device_returns_false_for_eos():
    from cvp_mcp.grpc.utils import _is_lab_device

    assert _is_lab_device({"device_type": "EOS"}) is False


def test_is_lab_device_returns_false_for_third_party():
    from cvp_mcp.grpc.utils import _is_lab_device

    assert _is_lab_device({"device_type": "Third Party"}) is False


def test_is_lab_device_returns_false_for_missing_device_type():
    from cvp_mcp.grpc.utils import _is_lab_device

    assert _is_lab_device({}) is False


# ---------------------------------------------------------------------------
# scan_lldp_topology_edges — lab device filtering
# ---------------------------------------------------------------------------

_VIRTUAL_DEV = {
    "serial_number": "VEOS1",
    "hostname": "lab-sw1",
    "model": "cEOSLab",
    "device_type": "Virtual EOS",
}
_PHYSICAL_DEV = {
    "serial_number": "PHYS1",
    "hostname": "phys-sw1",
    "model": "DCS-7050CX3-32S",
    "device_type": "EOS",
}


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
def test_scan_lldp_skips_virtual_eos_by_default(
    mock_oper_up: MagicMock,
    mock_lldp: MagicMock,
    mock_inv: MagicMock,
) -> None:
    from cvp_mcp.grpc.network_map import scan_lldp_topology_edges

    mock_inv.return_value = ([_VIRTUAL_DEV, _PHYSICAL_DEV], [])
    mock_oper_up.return_value = (["Ethernet1"], [])
    mock_lldp.return_value = {"items": []}

    _edges, stats = scan_lldp_topology_edges(
        {"cvp": "h.example:443", "cvtoken": "t"},
        lldp_port_source="auto",
    )

    assert stats["devices_skipped_lab"] == 1
    assert stats["devices_scanned"] == 1  # only the physical device


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
def test_scan_lldp_includes_virtual_eos_when_flag_set(
    mock_oper_up: MagicMock,
    mock_lldp: MagicMock,
    mock_inv: MagicMock,
) -> None:
    from cvp_mcp.grpc.network_map import scan_lldp_topology_edges

    mock_inv.return_value = ([_VIRTUAL_DEV, _PHYSICAL_DEV], [])
    mock_oper_up.return_value = (["Ethernet1"], [])
    mock_lldp.return_value = {"items": []}

    _edges, stats = scan_lldp_topology_edges(
        {"cvp": "h.example:443", "cvtoken": "t"},
        lldp_port_source="auto",
        include_lab_devices=True,
    )

    assert stats.get("devices_skipped_lab", 0) == 0
    assert stats["devices_scanned"] == 2  # both devices probed


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
def test_scan_lldp_stats_key_present_even_with_no_lab_devices(
    mock_oper_up: MagicMock,
    mock_lldp: MagicMock,
    mock_inv: MagicMock,
) -> None:
    from cvp_mcp.grpc.network_map import scan_lldp_topology_edges

    mock_inv.return_value = ([_PHYSICAL_DEV], [])
    mock_oper_up.return_value = (["Ethernet1"], [])
    mock_lldp.return_value = {"items": []}

    _edges, stats = scan_lldp_topology_edges(
        {"cvp": "h.example:443", "cvtoken": "t"},
    )

    assert "devices_skipped_lab" in stats
    assert stats["devices_skipped_lab"] == 0


# ---------------------------------------------------------------------------
# get_cvp_lldp_neighbors — pre-flight lab and inactive checks
# ---------------------------------------------------------------------------


def _make_channel_mock() -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock())
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


@patch("cloudvision_mcp._resolve_device_serial")
@patch("cloudvision_mcp.grpc.secure_channel")
@patch("cloudvision_mcp.createConnection")
@patch("cloudvision_mcp.get_env_vars")
def test_lldp_blocks_virtual_eos_by_default(
    mock_env: MagicMock,
    mock_conn: MagicMock,
    mock_channel: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    from cloudvision_mcp import get_cvp_lldp_neighbors

    mock_env.return_value = {"cvp": "test.example:443", "cvtoken": "tok"}
    mock_conn.return_value = MagicMock()
    mock_channel.return_value = _make_channel_mock()
    device_info = {
        "device_type": "Virtual EOS",
        "streaming_status": "Active",
        "hostname": "lab-sw1",
        "serial_number": "VEOS1",
    }
    mock_resolve.return_value = ("VEOS1", device_info, [])

    result = get_cvp_lldp_neighbors("VEOS1")

    assert "device_excluded_lab_or_virtual" in result.get("warnings", [])
    assert result["coverage"] == "none"


@patch("cloudvision_mcp._resolve_device_serial")
@patch("cloudvision_mcp.grpc.secure_channel")
@patch("cloudvision_mcp.createConnection")
@patch("cloudvision_mcp.get_env_vars")
def test_lldp_allows_virtual_eos_when_flag_set(
    mock_env: MagicMock,
    mock_conn: MagicMock,
    mock_channel: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    from cloudvision_mcp import get_cvp_lldp_neighbors

    mock_env.return_value = {"cvp": "test.example:443", "cvtoken": "tok"}
    mock_conn.return_value = MagicMock()
    mock_channel.return_value = _make_channel_mock()
    device_info = {
        "device_type": "Virtual EOS",
        "streaming_status": "Active",
        "hostname": "lab-sw1",
        "serial_number": "VEOS1",
    }
    mock_resolve.return_value = ("VEOS1", device_info, [])

    with patch("cloudvision_mcp.grpc_get_lldp_neighbors") as mock_lldp:
        mock_lldp.return_value = {"coverage": "full", "items": [], "warnings": []}
        result = get_cvp_lldp_neighbors("VEOS1", include_lab_devices=True)

    assert "device_excluded_lab_or_virtual" not in result.get("warnings", [])
    mock_lldp.assert_called_once()


@patch("cloudvision_mcp._resolve_device_serial")
@patch("cloudvision_mcp.grpc.secure_channel")
@patch("cloudvision_mcp.createConnection")
@patch("cloudvision_mcp.get_env_vars")
def test_lldp_blocks_inactive_device(
    mock_env: MagicMock,
    mock_conn: MagicMock,
    mock_channel: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    from cloudvision_mcp import get_cvp_lldp_neighbors

    mock_env.return_value = {"cvp": "test.example:443", "cvtoken": "tok"}
    mock_conn.return_value = MagicMock()
    mock_channel.return_value = _make_channel_mock()
    device_info = {
        "device_type": "EOS",
        "streaming_status": "Inactive",
        "hostname": "phys-sw1",
        "serial_number": "PHYS1",
    }
    mock_resolve.return_value = ("PHYS1", device_info, [])

    result = get_cvp_lldp_neighbors("PHYS1")

    assert "device_inactive_not_streaming" in result.get("warnings", [])
    assert result["coverage"] == "none"


@patch("cloudvision_mcp._resolve_device_serial")
@patch("cloudvision_mcp.grpc.secure_channel")
@patch("cloudvision_mcp.createConnection")
@patch("cloudvision_mcp.get_env_vars")
def test_lldp_returns_not_found_when_resolution_fails(
    mock_env: MagicMock,
    mock_conn: MagicMock,
    mock_channel: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    from cloudvision_mcp import get_cvp_lldp_neighbors

    mock_env.return_value = {"cvp": "test.example:443", "cvtoken": "tok"}
    mock_conn.return_value = MagicMock()
    mock_channel.return_value = _make_channel_mock()
    mock_resolve.return_value = (None, None, [])

    with patch("cloudvision_mcp.grpc_get_lldp_neighbors") as mock_lldp:
        result = get_cvp_lldp_neighbors("UNKNOWN1")

    mock_lldp.assert_not_called()
    assert "device_not_found" in result.get("warnings", [])
