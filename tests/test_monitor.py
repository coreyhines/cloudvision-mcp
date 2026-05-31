"""Tests for connectivity monitor probe helpers."""

from unittest.mock import MagicMock, patch

from cvp_mcp.grpc import monitor


def test_grpc_one_probe_status_error_returns_empty_list():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetAll.side_effect = RuntimeError("backend down")

    with patch.object(monitor.services, "ProbeStatsServiceStub", return_value=stub):
        result = monitor.grpc_one_probe_status(channel, serial_number="SN1")
    assert result == []


def test_grpc_one_probe_status_success_returns_list():
    channel = MagicMock()
    stub = MagicMock()
    probe_msg = MagicMock()
    stub.GetAll.return_value = [probe_msg]
    expected = {"serial_number": "SN1", "host": "8.8.8.8"}

    with patch.object(monitor.services, "ProbeStatsServiceStub", return_value=stub):
        with patch.object(
            monitor, "convert_response_to_probe_stat", return_value=expected
        ):
            result = monitor.grpc_one_probe_status(channel, serial_number="SN1")
    assert result == [expected]
