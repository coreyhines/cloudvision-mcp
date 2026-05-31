"""Tests for rate limiting and tool access."""

from unittest.mock import patch

from cvp_mcp.rate_limit import _buckets, _TokenBucket, rate_limited_tool
from cvp_mcp.tool_access import disabled_tools, tool_enabled


def test_disabled_tools_from_env(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "get_cvp_routes, get_cvp_bgp_status")
    assert disabled_tools() == {"get_cvp_routes", "get_cvp_bgp_status"}


def test_tool_enabled_blocks_disabled_tool(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "my_tool")

    @tool_enabled("my_tool")
    def fn():
        return {"ok": True}

    assert fn() == {"error": "tool_disabled", "tool": "my_tool"}


def test_rate_limited_tool_rejects_when_exceeded():
    calls = {"n": 0}

    @rate_limited_tool("test_tool_burst")
    def fn():
        calls["n"] += 1
        return {"ok": True}

    with patch.dict(
        "cvp_mcp.rate_limit._EXPENSIVE_TOOL_LIMITS",
        {"test_tool_burst": (2, 60.0)},
        clear=False,
    ):
        _buckets["test_tool_burst"] = _TokenBucket(2, 60.0)
        assert fn() == {"ok": True}
        assert fn() == {"ok": True}
        assert fn() == {"error": "rate_limit_exceeded", "tool": "test_tool_burst"}
