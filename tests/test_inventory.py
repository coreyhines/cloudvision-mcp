"""Tests for inventory gRPC helpers."""

from unittest.mock import MagicMock, patch

import grpc
import pytest

from cvp_mcp.grpc import inventory


class _FakeNotFound(grpc.RpcError):
    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.NOT_FOUND


class _FakeUnavailable(grpc.RpcError):
    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.UNAVAILABLE


def test_grpc_one_inventory_serial_not_found_returns_none():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetOne.side_effect = _FakeNotFound()

    with patch.object(inventory.services, "DeviceServiceStub", return_value=stub):
        result = inventory.grpc_one_inventory_serial(channel, "MISSING")
    assert result is None


def test_grpc_one_inventory_serial_success():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetOne.return_value = MagicMock()
    expected = {"serial_number": "ABC123", "hostname": "sw1"}

    with patch.object(inventory.services, "DeviceServiceStub", return_value=stub):
        with patch.object(
            inventory, "convert_response_to_switch", return_value=expected
        ):
            result = inventory.grpc_one_inventory_serial(channel, "ABC123")
    assert result == expected


def test_grpc_one_inventory_serial_rpc_error_reraises():
    channel = MagicMock()
    stub = MagicMock()
    stub.GetOne.side_effect = _FakeUnavailable()

    with patch.object(inventory.services, "DeviceServiceStub", return_value=stub):
        with pytest.raises(grpc.RpcError):
            inventory.grpc_one_inventory_serial(channel, "ABC123")
