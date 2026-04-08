"""Tests for CVP Events helper logic (no live CVP)."""

from __future__ import annotations

from arista.event.v1 import models as em

from cvp_mcp.grpc.events import _cap_limit, _parse_severity


def test_cap_limit():
    assert _cap_limit(None, 100, 500) == 100
    assert _cap_limit(9999, 100, 200) == 200


def test_parse_severity_aliases():
    assert _parse_severity("ERROR") == em.EVENT_SEVERITY_ERROR
    assert _parse_severity("EVENT_SEVERITY_CRITICAL") == em.EVENT_SEVERITY_CRITICAL
    assert _parse_severity("not_a_severity") is None
    assert _parse_severity(None) is None
