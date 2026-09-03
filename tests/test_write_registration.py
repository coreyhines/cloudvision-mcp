"""The write tools exist as MCP tools only when the writes env is ``"1"``.

Replaces the registration guard that lived in the deleted submit suite.
``cloudvision_mcp`` registers at import time, so the module is reloaded under
each env state and once more at the end so later tests see the real env.
"""

import asyncio
import importlib

import pytest

from cvp_mcp.write_access import WRITES_ENV

WRITE_TOOLS = {
    "create_cvp_workspace",
    "delete_cvp_workspace",
    "build_cvp_workspace",
    "set_cvp_access_interface_description",
    "assign_cvp_studio_tags",
    "set_cvp_studio_inputs",
    "create_cvp_studio",
    "delete_cvp_studio",
    "set_cvp_mss_policy_inputs",
}


def _tool_names(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv(WRITES_ENV, raising=False)
    else:
        monkeypatch.setenv(WRITES_ENV, env_value)
    import cloudvision_mcp

    module = importlib.reload(cloudvision_mcp)
    return {tool.name for tool in asyncio.run(module.mcp.list_tools())}


@pytest.fixture(autouse=True)
def _restore_module():
    yield
    import cloudvision_mcp

    importlib.reload(cloudvision_mcp)


def test_writes_off_registers_no_write_tools(monkeypatch):
    names = _tool_names(monkeypatch, None)
    assert not (names & WRITE_TOOLS)
    assert "submit_cvp_workspace" not in names


def test_writes_on_registers_all_write_tools_but_never_submit(monkeypatch):
    names = _tool_names(monkeypatch, "1")
    assert WRITE_TOOLS <= names
    assert "submit_cvp_workspace" not in names
