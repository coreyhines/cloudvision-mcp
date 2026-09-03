from cvp_mcp.grouped_tool import GroupedTool, MemberSpec


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
