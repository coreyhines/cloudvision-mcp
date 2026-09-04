"""Tests for the meta probe_path diagnostic action."""

from unittest.mock import MagicMock, patch

import pytest
from cloudvision.Connector.codec import Wildcard

from cvp_mcp.grpc import path_probe
from cvp_mcp.members import meta


def test_parse_probe_path_splits_on_slash():
    assert path_probe.parse_probe_path("Sysdb/environment") == ["Sysdb", "environment"]


def test_parse_probe_path_converts_star_to_wildcard():
    elts = path_probe.parse_probe_path("Sysdb/environment/*")
    assert elts[:2] == ["Sysdb", "environment"]
    assert isinstance(elts[2], Wildcard)


def test_parse_probe_path_ignores_surrounding_and_repeated_slashes():
    assert path_probe.parse_probe_path("/Sysdb//environment/") == [
        "Sysdb",
        "environment",
    ]


def test_parse_probe_path_rejects_empty_path():
    with pytest.raises(ValueError):
        path_probe.parse_probe_path("   ")


def test_probe_path_reports_returned_keys_and_count():
    datadict = {"cvp": "cv.example.com", "cvtoken": "tok"}
    returned = {"fan1": {"speed": 40}, "fan2": {"speed": 41}}

    with patch.object(path_probe, "GRPCClient", MagicMock()):
        with patch.object(path_probe, "get_device_path", return_value=returned):
            result = path_probe.probe_device_path(
                datadict, "SN1", "Sysdb/environment/*"
            )

    assert result["object"]["key_count"] == 2
    assert result["object"]["keys"] == ["fan1", "fan2"]
    assert result["object"]["path_elements"] == ["Sysdb", "environment", "*"]
    assert result["coverage"] == "full"


def test_probe_path_marks_empty_result_as_none_coverage():
    datadict = {"cvp": "cv.example.com", "cvtoken": "tok"}

    with patch.object(path_probe, "GRPCClient", MagicMock()):
        with patch.object(path_probe, "get_device_path", return_value={}):
            result = path_probe.probe_device_path(
                datadict, "SN1", "Sysdb/environment/*"
            )

    assert result["object"]["key_count"] == 0
    assert result["coverage"] == "none"
    assert "no_data_at_path" in result["warnings"]


def test_probe_path_requires_device_id():
    result = path_probe.probe_device_path({}, "", "Sysdb/environment/*")
    assert "missing_device_id" in result["warnings"]


def test_probe_path_reports_invalid_path_as_warning():
    result = path_probe.probe_device_path({}, "SN1", "  ")
    assert "invalid_path" in result["warnings"]


def test_meta_exposes_probe_path_member():
    members = meta.members()
    assert "probe_path" in members
    assert set(members["probe_path"].required) == {"device_id", "path"}


def test_probe_path_does_not_prepend_device_id_to_path():
    """The Connector Query already scopes the dataset, so the serial must not repeat."""
    datadict = {"cvp": "cv.example.com", "cvtoken": "tok"}
    seen = {}

    def fake_get(_client, dataset, path_elts):
        seen["dataset"] = dataset
        seen["path"] = list(path_elts)
        return {"fan1": {}}

    with patch.object(path_probe, "GRPCClient", MagicMock()):
        with patch.object(path_probe, "get_device_path", side_effect=fake_get):
            path_probe.probe_device_path(datadict, "SN1", "Sysdb/environment/*")

    assert seen["dataset"] == "SN1"
    assert seen["path"][0] == "Sysdb"
    assert "SN1" not in [p for p in seen["path"] if isinstance(p, str)]


def test_meta_probe_path_resolves_hostname_to_serial():
    """A hostname must be resolved; the Connector dataset is the serial."""
    seen = {}

    def fake_probe(_datadict, device_id, path):
        seen["device_id"] = device_id
        seen["path"] = path
        return {"device_id": device_id, "warnings": [], "object": {}}

    with patch.object(meta, "env_datadict_from_os", return_value={}):
        with patch.object(
            meta,
            "resolve_device_to_serial",
            return_value=("HBG254804R6", {}, [], []),
        ):
            with patch.object(meta, "probe_device_path", side_effect=fake_probe):
                meta.meta_probe_path("720xp-48", "Sysdb/environment/*")

    assert seen["device_id"] == "HBG254804R6"
