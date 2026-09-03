"""The public MCP catalog contains grouped action tools only."""

import asyncio
import importlib
import json

import pytest

from cvp_mcp.tool_groups import ALWAYS_ON_GROUPS, build_groups
from cvp_mcp.write_access import WRITES_ENV

FLAT_PREFIXES = (
    "assign_cvp_",
    "build_cvp_",
    "create_cvp_",
    "delete_cvp_",
    "get_cvp_",
    "map_cvp_",
    "search_cvp_",
    "set_cvp_",
)


def _reload(monkeypatch: pytest.MonkeyPatch, env_value: str | None):
    if env_value is None:
        monkeypatch.delenv(WRITES_ENV, raising=False)
    else:
        monkeypatch.setenv(WRITES_ENV, env_value)
    import cloudvision_mcp

    return importlib.reload(cloudvision_mcp)


def _tool_names(module) -> set[str]:
    return {tool.name for tool in asyncio.run(module.mcp.list_tools())}


@pytest.fixture(autouse=True)
def _restore_module():
    yield
    import cloudvision_mcp

    importlib.reload(cloudvision_mcp)


def test_writes_off_has_exact_grouped_surface(monkeypatch):
    module = _reload(monkeypatch, None)
    names = _tool_names(module)

    assert names == ALWAYS_ON_GROUPS
    assert len(names) == 12
    assert not any(name.startswith(FLAT_PREFIXES) for name in names)
    assert "submit_cvp_workspace" not in names


def test_writes_on_adds_only_grouped_write_tool(monkeypatch):
    module = _reload(monkeypatch, "1")
    names = _tool_names(module)

    assert names == ALWAYS_ON_GROUPS | {"studios_write"}
    assert not any(name.startswith(FLAT_PREFIXES) for name in names)
    assert "submit_cvp_workspace" not in names


def test_every_group_help_lists_all_members(monkeypatch):
    module = _reload(monkeypatch, None)
    expected = {group.name: set(group.members) for group in build_groups()}

    for name in _tool_names(module):
        result = asyncio.run(module.mcp.call_tool(name, {"action": "help"}))
        payload = json.loads(result[0].text)
        actions = {action["action"] for action in payload["actions"]}
        assert actions == expected[name]
