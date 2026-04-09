"""Inventory device_type labels vs. features (must match `convert_response_to_switch` in utils)."""

from __future__ import annotations

# WiFi APs (e.g. C-330) do not expose EOS `show running-config` via compliance.
_NO_RUNNING_CONFIG_DEVICE_TYPES = frozenset({"Access Point"})


def device_type_supports_running_config(device_type: str | None) -> bool:
    """False for types that never return EOS-style running config; True if unknown."""
    if not (device_type or "").strip():
        return True
    return device_type.strip() not in _NO_RUNNING_CONFIG_DEVICE_TYPES
