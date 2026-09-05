"""Health must return sensor readings, not pointers to them.

Probing 720xp-48 showed Sysdb/environment/* stops on Path pointers.
The readings sit deeper, and because keyed results preserve identity a
trailing wildcard at the right depth collects them all in one query:
  power/status/powerSupply/*        -> PowerSupply1, PowerSupply2
  temperature/status/system/*       -> TempSensor12..43, DomTemperatureSensor*
  cooling/status/*                  -> Fan1/1, Fan2/1, Fan3/1, FanP1/1, FanP2/1
"""

from cvp_mcp.grpc import overlay


def _rendered(device_id="SN1"):
    return {
        label: "/".join("*" if not isinstance(p, str) else p for p in path)
        for label, path in overlay.health_paths(device_id)
    }


def test_health_targets_power_supplies_at_reading_depth():
    r = _rendered()
    assert r["power_supplies"].endswith("environment/archer/power/status/powerSupply/*")


def test_health_targets_temperature_sensors_at_reading_depth():
    r = _rendered()
    assert r["temperature"].endswith("environment/archer/temperature/status/system/*")


def test_health_targets_fans_at_reading_depth():
    r = _rendered()
    assert r["fans"].endswith("environment/archer/cooling/status/*")


def test_health_no_longer_stops_at_the_pointer_level():
    r = _rendered()
    # Sysdb/environment/* returned only Path pointers, never readings.
    assert not any(v.endswith("Sysdb/environment/*") for v in r.values())


from unittest.mock import MagicMock, patch  # noqa: E402

from cloudvision.Connector.codec import Wildcard  # noqa: E402


def test_wildcard_paths_use_keyed_fetch_so_sensors_stay_distinct():
    """Two PSUs must not collapse onto one another."""
    keyed = {
        "PowerSupply1": {"name": "PowerSupply1", "outputPower": 40.0},
        "PowerSupply2": {"name": "PowerSupply2", "outputPower": 38.5},
    }
    with patch.object(overlay, "GRPCClient", MagicMock()):
        with patch.object(overlay, "get_device_path_keyed", return_value=keyed):
            label, data = overlay._fetch(
                {"cvp": "cv:443"},
                "SN1",
                ["SN1", "Sysdb", "environment", "archer", "cooling", Wildcard()],
                "fans",
            )
    assert label == "fans"
    assert set(data) == {"PowerSupply1", "PowerSupply2"}
    assert data["PowerSupply2"]["outputPower"] == 38.5
