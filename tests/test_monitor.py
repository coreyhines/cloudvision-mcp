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


def test_grpc_one_probe_status_ands_filters_into_one_partial_eq_filter():
    """All supplied filters must intersect, not union.

    The Resource API ORs repeated ``partial_eq_filter`` entries, so every
    supplied field has to ride on a single ProbeStats message.
    """
    channel = MagicMock()
    stub = MagicMock()
    stub.GetAll.return_value = []

    with patch.object(monitor.services, "ProbeStatsServiceStub", return_value=stub):
        monitor.grpc_one_probe_status(
            channel,
            serial_number="SN1",
            host="NAS1",
            vrf="mgmt",
            sourceIntf="Management1",
        )

    request = stub.GetAll.call_args[0][0]
    assert len(request.partial_eq_filter) == 1
    key = request.partial_eq_filter[0].key
    assert key.device_id.value == "SN1"
    assert key.host.value == "NAS1"
    assert key.vrf.value == "mgmt"
    assert key.source_intf.value == "Management1"


def test_grpc_one_probe_status_omits_filter_when_no_criteria_given():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetAll.return_value = []

    with patch.object(monitor.services, "ProbeStatsServiceStub", return_value=stub):
        monitor.grpc_one_probe_status(channel)

    request = stub.GetAll.call_args[0][0]
    assert len(request.partial_eq_filter) == 0


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
