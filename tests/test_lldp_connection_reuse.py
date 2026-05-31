"""Tests for GRPCClient connection reuse in topology scan and LLDP neighbor probing.

Regression: 720xp-48 (48-port switch) was timing out because _get_path created a new
GRPCClient (TLS handshake) for every single path probe attempt. With 48 ports × ~5-8
path variants each, that's 240-380 channel creations per device. The fix creates ONE
GRPCClient per device and reuses it for all probes.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATADICT = {"cvp": "cvp.example:443", "cvtoken": "tok"}


def _make_mock_cm(return_value=None):
    """Return a mock context manager yielding return_value."""
    client = return_value or MagicMock()

    @contextmanager
    def _cm(*args, **kwargs):
        yield client

    return MagicMock(side_effect=_cm), client


# ---------------------------------------------------------------------------
# grpc_get_lldp_neighbors: _shared_client param bypasses _get_path
# ---------------------------------------------------------------------------


def test_lldp_neighbors_uses_shared_client_not_new_connection():
    """When _shared_client is provided, _get_path_with_client used, not _get_path."""
    from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors

    mock_client = MagicMock()
    fake_snap = {"sysName": {"value": "peer-sw"}, "ethAddr": "aa:bb:cc:dd:ee:ff"}

    with (
        patch("cvp_mcp.grpc.lldp._get_path") as mock_get_path,
        patch(
            "cvp_mcp.grpc.lldp._get_path_with_client", return_value=fake_snap
        ) as mock_gwc,
    ):
        grpc_get_lldp_neighbors(
            _DATADICT,
            "SERIAL1",
            port_name="Ethernet3",
            _shared_client=mock_client,
        )

    mock_get_path.assert_not_called()
    mock_gwc.assert_called()
    first_call = mock_gwc.call_args_list[0]
    assert first_call[0][0] is mock_client


def test_lldp_neighbors_without_shared_client_uses_get_path():
    """When no _shared_client, the original _get_path (creates its own client) is used."""
    from cvp_mcp.grpc.lldp import grpc_get_lldp_neighbors

    with (
        patch("cvp_mcp.grpc.lldp._get_path", return_value={}) as mock_get_path,
        patch("cvp_mcp.grpc.lldp._get_path_with_client") as mock_gwc,
    ):
        grpc_get_lldp_neighbors(_DATADICT, "SERIAL1", port_name="Ethernet3")

    mock_get_path.assert_called()
    mock_gwc.assert_not_called()


# ---------------------------------------------------------------------------
# grpc_list_oper_up_physical_ports_for_lldp: _shared_client param
# ---------------------------------------------------------------------------


def test_oper_up_uses_shared_client_when_provided():
    """grpc_list_oper_up_physical_ports_for_lldp uses provided client for device config."""
    from cvp_mcp.grpc.interfaces import grpc_list_oper_up_physical_ports_for_lldp

    mock_client = MagicMock()

    with (
        patch(
            "cvp_mcp.grpc.interfaces._connector_device_config_with_client",
            return_value={"intfConfig": {}, "intfStatus": {}},
        ) as mock_with_client,
        patch("cvp_mcp.grpc.interfaces._connector_device_config") as mock_without,
    ):
        grpc_list_oper_up_physical_ports_for_lldp(
            _DATADICT, "SERIAL1", _shared_client=mock_client
        )

    mock_with_client.assert_called_once()
    assert mock_with_client.call_args[0][0] is mock_client
    mock_without.assert_not_called()


def test_oper_up_without_shared_client_uses_connector_device_config():
    """Without _shared_client, original _connector_device_config is used."""
    from cvp_mcp.grpc.interfaces import grpc_list_oper_up_physical_ports_for_lldp

    with (
        patch(
            "cvp_mcp.grpc.interfaces._connector_device_config",
            return_value={"intfConfig": {}, "intfStatus": {}},
        ) as mock_without,
        patch(
            "cvp_mcp.grpc.interfaces._connector_device_config_with_client"
        ) as mock_with_client,
    ):
        grpc_list_oper_up_physical_ports_for_lldp(_DATADICT, "SERIAL1")

    mock_without.assert_called_once()
    mock_with_client.assert_not_called()


# ---------------------------------------------------------------------------
# scan_lldp_topology_edges: one GRPCClient per device, not per port
# ---------------------------------------------------------------------------


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
@patch("cvp_mcp.grpc.network_map._make_lldp_client")
def test_topology_scan_creates_one_client_per_device(
    mock_client_cm, mock_oper_up, mock_lldp, mock_inv
):
    """_make_lldp_client called once per device, not once per port."""
    from cvp_mcp.grpc.network_map import scan_lldp_topology_edges

    mock_inv.return_value = (
        [
            {"serial_number": "SN1", "hostname": "sw1", "model": "CCS-710P-16P"},
            {"serial_number": "SN2", "hostname": "sw2", "model": "CCS-710P-16P"},
            {"serial_number": "SN3", "hostname": "sw3", "model": "CCS-720XP-24ZY4"},
        ],
        [],
    )
    mock_oper_up.return_value = (["Ethernet1", "Ethernet2"], [])
    mock_lldp.return_value = {"items": []}

    # _make_lldp_client is a context manager
    inner_client = MagicMock()
    mock_client_cm.return_value.__enter__ = MagicMock(return_value=inner_client)
    mock_client_cm.return_value.__exit__ = MagicMock(return_value=False)

    _edges, stats = scan_lldp_topology_edges(_DATADICT, lldp_port_source="auto")

    # 3 devices → 3 client context managers (NOT 2 ports × 3 = 6)
    assert mock_client_cm.call_count == 3
    assert stats["devices_scanned"] == 3


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
@patch("cvp_mcp.grpc.network_map._make_lldp_client")
def test_shared_client_passed_to_lldp_and_oper_up(
    mock_client_cm, mock_oper_up, mock_lldp, mock_inv
):
    """The same client instance is passed to both oper-up and lldp-neighbor calls."""
    from cvp_mcp.grpc.network_map import scan_lldp_topology_edges

    mock_inv.return_value = (
        [{"serial_number": "SN1", "hostname": "sw1", "model": "CCS-710P-16P"}],
        [],
    )
    mock_oper_up.return_value = (["Ethernet1"], [])
    mock_lldp.return_value = {"items": []}

    inner_client = MagicMock()
    mock_client_cm.return_value.__enter__ = MagicMock(return_value=inner_client)
    mock_client_cm.return_value.__exit__ = MagicMock(return_value=False)

    scan_lldp_topology_edges(_DATADICT, lldp_port_source="auto")

    # oper-up call should have received the shared client
    _, oper_kwargs = mock_oper_up.call_args
    assert oper_kwargs.get("_shared_client") is inner_client

    # lldp call should have received the shared client
    _, lldp_kwargs = mock_lldp.call_args
    assert lldp_kwargs.get("_shared_client") is inner_client


# ---------------------------------------------------------------------------
# scan_lldp_topology_edges: per-device error isolation
# ---------------------------------------------------------------------------


@patch("cvp_mcp.grpc.network_map._collect_inventory")
@patch("cvp_mcp.grpc.network_map.grpc_get_lldp_neighbors")
@patch("cvp_mcp.grpc.network_map.grpc_list_oper_up_physical_ports_for_lldp")
@patch("cvp_mcp.grpc.network_map._make_lldp_client")
def test_device_client_error_increments_stat_and_continues(
    mock_client_cm, mock_oper_up, mock_lldp, mock_inv
):
    """If _make_lldp_client raises for one device, scan continues on remaining devices."""
    from cvp_mcp.grpc.network_map import scan_lldp_topology_edges

    mock_inv.return_value = (
        [
            {"serial_number": "SN1", "hostname": "sw1", "model": "CCS-710P-16P"},
            {"serial_number": "SN2", "hostname": "sw2", "model": "CCS-710P-16P"},
        ],
        [],
    )
    mock_oper_up.return_value = (["Ethernet1"], [])
    mock_lldp.return_value = {"items": []}

    inner_client = MagicMock()

    call_count = {"n": 0}

    @contextmanager
    def _client_cm_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("TLS handshake failed")
        yield inner_client

    mock_client_cm.side_effect = _client_cm_side_effect

    import logging

    with patch.object(logging, "warning") as mock_warn:
        _edges, stats = scan_lldp_topology_edges(_DATADICT, lldp_port_source="auto")

    # First device errored, second succeeded
    assert stats.get("devices_scan_errors", 0) == 1
    assert stats["devices_scanned"] == 2  # both were attempted
    # Second device's LLDP was still probed
    assert mock_lldp.call_count == 1

    # Warning was logged for the failed device
    warn_msgs = [str(c) for c in mock_warn.call_args_list]
    assert any("SN1" in m or "sw1" in m for m in warn_msgs)
