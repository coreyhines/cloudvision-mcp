"""Linked-list reconstruction for Config/running/lines (Telemetry Browser)."""

from __future__ import annotations

from cvp_mcp.grpc.config_connector import _reconstruct_running_config_lines


def test_reconstruct_doubly_linked_lines():
    blob = {
        "a": {"text": "! line0", "next": "b", "previous": ""},
        "b": {
            "text": "hostname sw1",
            "next": "c",
            "previous": {"value": "a"},
        },
        "c": {"text": "interface Ethernet1", "next": "", "previous": "b"},
    }
    text = _reconstruct_running_config_lines(blob)
    assert text is not None
    lines = text.split("\n")
    assert lines[0] == "! line0"
    assert "hostname sw1" in lines
    assert "interface Ethernet1" in lines
