from __future__ import annotations

from cvp_mcp.grpc import device_capabilities as dc


def test_access_point_has_no_running_config():
    assert dc.device_type_supports_running_config("Access Point") is False


def test_eos_and_unknown_still_try():
    assert dc.device_type_supports_running_config("EOS") is True
    assert dc.device_type_supports_running_config("Virtual EOS") is True
    assert dc.device_type_supports_running_config(None) is True
    assert dc.device_type_supports_running_config("") is True
