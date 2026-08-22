"""Unit tests for write env gates and preview tokens. No HTTP."""

import cvp_mcp.write_access as wa
from cvp_mcp.write_access import (
    SUBMIT_ENV,
    WRITES_ENV,
    check_preview_token,
    preview_token,
    submit_enabled,
    validate_workspace_id,
    writes_enabled,
)


def test_writes_enabled_only_when_one(monkeypatch):
    for value in (None, "", "0", "true", "yes", "11", "01", "1.0"):
        if value is None:
            monkeypatch.delenv(WRITES_ENV, raising=False)
        else:
            monkeypatch.setenv(WRITES_ENV, value)
        assert writes_enabled() is False, value

    monkeypatch.setenv(WRITES_ENV, "1")
    assert writes_enabled() is True
    # Surrounding whitespace is stripped before comparison, so " 1 " is on.
    monkeypatch.setenv(WRITES_ENV, " 1 ")
    assert writes_enabled() is True


def test_submit_disabled_when_staleness_field_unset(monkeypatch):
    monkeypatch.setattr(wa, "SUBMIT_STALENESS_FIELD", None, raising=False)
    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.setenv(SUBMIT_ENV, "1")
    assert submit_enabled() is False


def test_submit_enabled_when_all_set(monkeypatch):
    monkeypatch.setattr(wa, "SUBMIT_STALENESS_FIELD", "lastModifiedAt", raising=False)
    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.setenv(SUBMIT_ENV, "1")
    assert submit_enabled() is True


def test_submit_disabled_when_writes_off(monkeypatch):
    monkeypatch.setattr(wa, "SUBMIT_STALENESS_FIELD", "lastModifiedAt", raising=False)
    monkeypatch.delenv(WRITES_ENV, raising=False)
    monkeypatch.setenv(SUBMIT_ENV, "1")
    assert submit_enabled() is False


def test_preview_token_stable_and_order_independent():
    a = {"studio_id": "s1", "workspace_id": "ws-mcp-x", "confirm": True}
    b = {"confirm": True, "workspace_id": "ws-mcp-x", "studio_id": "s1"}
    assert preview_token("set_cvp_access_interface_description", a) == preview_token(
        "set_cvp_access_interface_description", b
    )


def test_preview_token_distinct_tools():
    args = {"studio_id": "s1"}
    assert preview_token("tool_a", args) != preview_token("tool_b", args)


def test_check_preview_token_mismatch():
    args = {"studio_id": "s1"}
    token = preview_token("my_tool", args)
    assert check_preview_token("my_tool", args, token) is None
    assert check_preview_token("other_tool", args, token) == "preview_required"
    assert check_preview_token("my_tool", args, None) == "preview_required"
    assert check_preview_token("my_tool", args, "deadbeef") == "preview_required"


def test_preview_token_non_serializable_default_str():
    args = {"obj": object()}
    token = preview_token("my_tool", args)
    # Deterministic across calls despite unserializable object via default=str.
    assert token == preview_token("my_tool", args)


def test_validate_workspace_id_success():
    assert validate_workspace_id("ws-mcp-desc-20260822-aabbccdd") is None
    assert validate_workspace_id("  ws-mcp-desc-20260822-aabbccdd  ") is None


def test_validate_workspace_id_failures():
    assert validate_workspace_id("") == "workspace_id_required"
    assert validate_workspace_id("   ") == "workspace_id_required"
    assert validate_workspace_id("Builtin-x") == "builtin_workspace_forbidden"
    assert validate_workspace_id("builtin-foo") == "builtin_workspace_forbidden"
    assert validate_workspace_id("ws-other") == "invalid_workspace_id"
    assert validate_workspace_id("mainline") == "invalid_workspace_id"
