from cvp_mcp.grouped_tool import GroupedTool, MemberSpec
from cvp_mcp.rate_limit import reset_rate_limit_buckets


def _echo(**kwargs):
    return {"echo": kwargs}


def _group() -> GroupedTool:
    return GroupedTool(
        name="inventory",
        description="Inventory ops",
        members={
            "get": MemberSpec(
                action="get",
                description="One device",
                required=["device_id"],
                properties={
                    "device_id": {"type": "string", "description": "Serial or hostname"}
                },
                call=_echo,
            ),
            "search": MemberSpec(
                action="search",
                description="Search",
                required=["query"],
                properties={"query": {"type": "string", "description": "Substring"}},
                call=_echo,
            ),
        },
    )


def test_schema_requires_only_action_and_enums_members_plus_help():
    g = _group()
    assert g.input_schema["required"] == ["action"]
    assert set(g.input_schema["properties"]["action"]["enum"]) == {
        "get",
        "search",
        "help",
    }


def test_shared_field_prefers_member_description():
    g = _group()

    assert (
        g.input_schema["properties"]["device_id"]["description"] == "Serial or hostname"
    )


def test_help_lists_required_and_optional():
    help_out = _group().execute({"action": "help"})
    actions = {row["action"]: row for row in help_out["actions"]}
    assert actions["get"]["required"] == ["device_id"]
    assert "query" in actions["search"]["required"]


def test_unknown_action_envelope():
    out = _group().execute({"action": "nope"})
    assert out["error"] == "action_unknown"
    assert out["tool"] == "inventory"
    assert out["hint"] == "help"


def test_missing_required_envelope():
    out = _group().execute({"action": "get"})
    assert out["error"] == "action_args_invalid"
    assert out["required"] == ["device_id"]


def test_strips_wrong_action_fields_and_omits_none():
    out = _group().execute(
        {"action": "get", "device_id": "ABC", "query": "should-strip", "noise": 1}
    )
    assert out == {"echo": {"device_id": "ABC"}}


def test_empty_string_required_is_missing():
    out = _group().execute({"action": "get", "device_id": "  "})
    assert out["error"] == "action_args_invalid"


def test_disable_whole_group(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "inventory")
    out = _group().execute({"action": "get", "device_id": "x"})
    assert out["error"] == "tool_disabled"
    assert out["tool"] == "inventory"


def test_disable_whole_group_also_disables_help(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "inventory")
    out = _group().execute({"action": "help"})
    assert out["error"] == "tool_disabled"
    assert out["tool"] == "inventory"


def test_disable_one_action(monkeypatch):
    monkeypatch.setenv("CVP_MCP_DISABLED_TOOLS", "inventory.search")
    assert (
        _group().execute({"action": "get", "device_id": "x"})["echo"]["device_id"]
        == "x"
    )
    assert (
        _group().execute({"action": "search", "query": "abc"})["error"]
        == "tool_disabled"
    )
    assert (
        _group().execute({"action": "search", "query": "abc"})["tool"]
        == "inventory.search"
    )
    assert "actions" in _group().execute({"action": "help"})


def test_rate_limit_exceeded_on_member():
    reset_rate_limit_buckets()
    group = GroupedTool(
        name="inventory",
        description="Inventory ops",
        members={
            "list": MemberSpec(
                action="list",
                description="List all",
                required=[],
                properties={},
                call=_echo,
                rate_limit_key="inventory.list",
            ),
        },
    )
    for _ in range(6):
        assert group.execute({"action": "list"}) == {"echo": {}}
    out = group.execute({"action": "list"})
    assert out == {"error": "rate_limit_exceeded", "tool": "inventory.list"}
