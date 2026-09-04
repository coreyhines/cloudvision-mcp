"""Lock grouped MCP catalog constants — no ``cloudvision_mcp`` import."""

from cvp_mcp import tool_groups
from cvp_mcp.tool_groups import (
    ALWAYS_ON_GROUPS,
    DOCSTRING_SURFACE,
    LEGACY_FLAT_TO_ACTION,
    MEMBER_ACTIONS,
    iter_member_actions,
)


def test_always_on_groups():
    assert len(ALWAYS_ON_GROUPS) == 12
    assert "studios_write" not in ALWAYS_ON_GROUPS
    assert ALWAYS_ON_GROUPS == frozenset(
        {
            "inventory",
            "endpoints",
            "device",
            "overlay",
            "routing",
            "topology",
            "events",
            "flow",
            "probes",
            "compliance",
            "meta",
            "studios",
        }
    )


def test_legacy_flat_to_action_bijection():
    assert len(LEGACY_FLAT_TO_ACTION) == 44
    assert len(set(LEGACY_FLAT_TO_ACTION.values())) == 44
    assert not any(name.startswith("__") for name in LEGACY_FLAT_TO_ACTION)


def test_member_actions_includes_status_and_legacy_values():
    assert len(MEMBER_ACTIONS) == 46
    assert "compliance.config_status" in MEMBER_ACTIONS
    assert "compliance.image_status" in MEMBER_ACTIONS
    assert set(LEGACY_FLAT_TO_ACTION.values()) <= MEMBER_ACTIONS


def test_member_bijection():
    assert set(iter_member_actions()) == MEMBER_ACTIONS
    assert len(MEMBER_ACTIONS) == 46


def test_docstring_count_locked():
    assert DOCSTRING_SURFACE in tool_groups.__doc__
