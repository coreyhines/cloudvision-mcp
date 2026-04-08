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


def test_reconstruct_protobuf_text_wrapper():
    pad = "x" * 30
    blob = {
        "a": {"text": {"value": f"! line0\n{pad}"}, "next": "b", "previous": ""},
        "b": {"text": {"value": f"hostname sw1\n{pad}"}, "next": "", "previous": "a"},
    }
    text = _reconstruct_running_config_lines(blob)
    assert text is not None
    assert "! line0" in text
    assert "hostname sw1" in text


def test_reconstruct_single_key_wrapper():
    pad = "y" * 30
    inner = {
        "a": {"text": f"! hdr\n{pad}", "next": "b", "previous": ""},
        "b": {"text": f"hostname z\n{pad}", "next": "", "previous": "a"},
    }
    text = _reconstruct_running_config_lines({"wrapper": inner})
    assert text is not None
    assert "! hdr" in text
    assert "hostname z" in text
