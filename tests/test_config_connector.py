"""Tests for connector running-config heuristics."""

from __future__ import annotations

from cvp_mcp.grpc import config_connector as cc


def test_looks_like_eos_running_config_comment_style():
    s = "! device: foo\nhostname sw1\n" + "!\n" * 20
    assert cc._looks_like_eos_running_config(s)


def test_looks_like_eos_running_config_no_bang():
    s = "hostname sw1\ninterface Ethernet1\n  switchport mode trunk\n" + ("x" * 30)
    assert cc._looks_like_eos_running_config(s)


def test_extract_finds_nested_key():
    blob = {"outer": {"runningConfig": "hostname a\n!\nvlan 10\n" + ("y" * 40)}}
    found = cc._extract_config_strings(blob)
    assert found
    assert "hostname a" in cc._best_config_candidate(found) or ""
