"""Smoke tests for extracted grouped-tool members."""

from unittest.mock import MagicMock

import pytest

from cvp_mcp.tool_groups import build_groups, build_write_group
from cvp_mcp.write_access import WRITES_ENV


def test_inventory_get_returns_dict_not_str(monkeypatch):
    """Inventory get returns its structured device object directly."""
    from cvp_mcp.members import inventory

    channel = MagicMock()
    channel_context = MagicMock()
    channel_context.__enter__.return_value = channel
    monkeypatch.setattr(
        inventory, "env_datadict_from_os", lambda: {"cvp": "cvp.example"}
    )
    monkeypatch.setattr(inventory, "createConnection", lambda _env: MagicMock())
    monkeypatch.setattr(
        inventory.grpc, "secure_channel", lambda _target, _creds: channel_context
    )
    monkeypatch.setattr(
        inventory,
        "resolve_device_to_serial",
        lambda _env, _device_id, channel=None: (
            "SERIAL1",
            {"serial_number": "SERIAL1"},
            [],
            [],
        ),
    )

    out = inventory.inventory_get("SERIAL1")

    assert isinstance(out, dict)
    assert out == {"serial_number": "SERIAL1"}


def test_inventory_get_missing_resolved_record_returns_error_dict(monkeypatch):
    """A resolved serial with no inventory record still returns a dictionary."""
    from cvp_mcp.members import inventory

    channel = MagicMock()
    channel_context = MagicMock()
    channel_context.__enter__.return_value = channel
    monkeypatch.setattr(
        inventory, "env_datadict_from_os", lambda: {"cvp": "cvp.example"}
    )
    monkeypatch.setattr(inventory, "createConnection", lambda _env: MagicMock())
    monkeypatch.setattr(
        inventory.grpc, "secure_channel", lambda _target, _creds: channel_context
    )
    monkeypatch.setattr(
        inventory,
        "resolve_device_to_serial",
        lambda _env, _device_id, channel=None: ("SERIAL1", None, [], []),
    )
    monkeypatch.setattr(
        inventory, "grpc_one_inventory_serial", lambda _channel, _serial: None
    )

    out = inventory.inventory_get("SERIAL1")

    assert isinstance(out, dict)
    assert "error" in out


@pytest.mark.parametrize(
    ("group_name", "action", "params"),
    [
        ("inventory", "get", {"device_id": "SERIAL1"}),
        ("endpoints", "get", {"search_term": "10.0.0.1"}),
        ("device", "config", {"device_id": "SERIAL1"}),
        ("overlay", "evpn", {"device_id": "SERIAL1"}),
        ("routing", "bgp", {"device_id": "SERIAL1"}),
    ],
)
def test_batch_a_group_dispatch(group_name, action, params):
    """Each batch A group dispatches an action to its extracted member."""
    group = next(group for group in build_groups() if group.name == group_name)
    call = MagicMock(return_value={"group": group_name, "action": action})
    group.members[action].call = call

    result = group.execute({"action": action, **params})

    assert result == {"group": group_name, "action": action}
    call.assert_called_once_with(**params)


def test_inventory_list_uses_group_rate_limit_key():
    """Inventory list is rate-limited under its grouped action key."""
    group = next(group for group in build_groups() if group.name == "inventory")

    assert group.members["list"].rate_limit_key == "inventory.list"


def test_device_not_found_hints_use_grouped_tool_names():
    """Device resolution guidance never advertises removed flat tools."""
    from cvp_mcp.members._device import device_not_found_envelope

    result = device_not_found_envelope("missing", "test")
    guidance = " ".join([result["object"]["hint"], result["object"]["next_step"]])

    assert not any(
        prefix in guidance for prefix in ("get_cvp_", "search_cvp_", "map_cvp_")
    )
    assert "inventory" in guidance
    assert "topology" in guidance


def test_inventory_search_next_step_uses_grouped_tool_name(monkeypatch):
    """Inventory search guidance never advertises removed flat tools."""
    from cvp_mcp.members import inventory

    channel_context = MagicMock()
    channel_context.__enter__.return_value = MagicMock()
    monkeypatch.setattr(
        inventory, "env_datadict_from_os", lambda: {"cvp": "cvp.example"}
    )
    monkeypatch.setattr(inventory, "createConnection", lambda _env: MagicMock())
    monkeypatch.setattr(
        inventory.grpc,
        "secure_channel",
        lambda _target, _creds: channel_context,
    )
    monkeypatch.setattr(
        inventory,
        "search_inventory_candidates",
        lambda _env, _query, channel=None: ([], []),
    )

    result = inventory.inventory_search("missing")
    next_step = result["object"]["next_step"]

    assert not any(
        prefix in next_step for prefix in ("get_cvp_", "search_cvp_", "map_cvp_")
    )
    assert "topology" in next_step


def test_batch_a_group_actions_match_frozen_catalog():
    """Extracted groups expose exactly their catalog actions."""
    groups = {group.name: set(group.members) for group in build_groups()}

    assert groups == {
        "inventory": {"get", "list", "search"},
        "endpoints": {"get", "list", "filter"},
        "device": {
            "config",
            "interfaces",
            "vlans",
            "ip_interfaces",
            "features",
            "health",
        },
        "overlay": {"evpn", "vxlan"},
        "routing": {"bgp", "routes"},
        "topology": {"lldp", "map"},
        "events": {"list", "search"},
        "flow": {"get"},
        "probes": {"list", "get"},
        "meta": {"probe_apis"},
        "compliance": {
            "bugs",
            "lifecycle",
            "designed_config",
            "config_status",
            "image_status",
        },
        "studios": {
            "list",
            "get",
            "inputs",
            "search_templates",
            "list_workspaces",
            "get_workspace",
            "get_build",
            "tags",
        },
    }


@pytest.mark.parametrize(
    ("group_name", "action", "params"),
    [
        ("topology", "lldp", {"device_id": "SERIAL1"}),
        ("events", "list", {}),
        ("flow", "get", {}),
        ("probes", "list", {}),
        ("meta", "probe_apis", {}),
        ("compliance", "bugs", {}),
        ("studios", "list", {}),
    ],
)
def test_batch_b_group_dispatch(group_name, action, params):
    """Each batch B group dispatches an action to its extracted member."""
    group = next(group for group in build_groups() if group.name == group_name)
    call = MagicMock(return_value={"group": group_name, "action": action})
    group.members[action].call = call

    result = group.execute({"action": action, **params})

    assert result == {"group": group_name, "action": action}
    call.assert_called_once_with(**params)


def test_batch_b_rate_limit_keys():
    """Expensive batch B operations use grouped rate-limit keys."""
    groups = {group.name: group for group in build_groups()}

    assert groups["topology"].members["map"].rate_limit_key == "topology.map"
    assert groups["events"].members["search"].rate_limit_key == "events.search"


def test_studios_description_cross_references_designed_config():
    """Studios help directs designed-config reads to compliance."""
    studios = next(group for group in build_groups() if group.name == "studios")

    assert "compliance" in studios.description
    assert "designed_config" in studios.description


def test_studios_write_group_catalog_and_confirm_refusal(monkeypatch):
    """Write dispatch preserves preview-token refusal without making a write."""
    from cvp_mcp.members import studios_write

    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.setattr(
        studios_write,
        "env_datadict_from_os",
        lambda: {"cvtoken": "token", "cvp": "cvp.example.com", "cert": None},
    )
    monkeypatch.setattr(
        "cvp_mcp.grpc.studios.get_json_with_bearer",
        lambda *_args, **_kwargs: (None, "http_error:404"),
    )
    urlopen = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    group = build_write_group()
    result = group.execute(
        {
            "action": "create_workspace",
            "workspace_id": "ws-mcp-dispatch-test",
            "display_name": "dispatch test",
            "confirm": True,
        }
    )

    assert group.name == "studios_write"
    assert set(group.members) == {
        "create_workspace",
        "delete_workspace",
        "build",
        "set_description",
        "set_inputs",
        "assign_tags",
        "create_studio",
        "delete_studio",
        "set_mss_inputs",
    }
    assert "never submits" in group.description
    assert "never approves/executes change controls" in group.description
    assert "dry-run unless confirm + matching preview_token" in group.description
    assert "drafts only ws-mcp-*" in group.description
    schema = group.input_schema["properties"]
    assert "used verbatim" in schema["device_id"]["description"]
    assert "hostname, FQDN, or MAC resolution" in schema["device_id"]["description"]
    assert "studios.inputs" in schema["expected_inputs_sha256"]["description"]
    assert "upsert|remove|set_policy_rules" in schema["operations"]["description"]
    assert result["object"]["error"]["code"] == "preview_required"
    urlopen.assert_not_called()


@pytest.mark.parametrize(
    ("action", "source", "warning"),
    [
        (
            "config_status",
            "resource_api:configstatus.v1.summary",
            "configstatus_forbidden",
        ),
        (
            "image_status",
            "resource_api:imagestatus.v1.summary",
            "imagestatus_forbidden",
        ),
    ],
)
def test_compliance_status_stubs_return_none_coverage(action, source, warning):
    """Unavailable compliance APIs return stable, non-fabricated envelopes."""
    compliance = next(group for group in build_groups() if group.name == "compliance")

    result = compliance.execute({"action": action, "device_id": "SERIAL1"})

    assert result["data_source"] == source
    assert result["coverage"] == "none"
    assert result["items"] == []
    assert result["warnings"] == [warning]
    assert result["object"] == {
        "device_id_input": "SERIAL1",
        "hint": "Resource API Summary returned 403 on this tenant",
    }


def test_designed_config_uses_compliance_member(monkeypatch):
    """Designed config remains callable through the compliance member."""
    from cvp_mcp.members import compliance

    expected = {"coverage": "full", "object": {"studio_keys": ["STUDIO1"]}}
    monkeypatch.setattr(
        compliance, "env_datadict_from_os", lambda: {"cvp": "cvp.example"}
    )
    call = MagicMock(return_value=expected)
    monkeypatch.setattr(compliance, "grpc_get_designed_config", call)

    result = compliance.compliance_designed_config("SERIAL1")

    assert result == expected
    call.assert_called_once_with({"cvp": "cvp.example"}, "SERIAL1")
