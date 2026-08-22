"""Tests for the allowlisted Resource API write helper.

Every refusal path asserts ``urlopen`` was never called: a rejected write must
not reach CloudVision at all.
"""

import json
import sys
from unittest.mock import patch

import pytest

from cvp_mcp import write_access
from cvp_mcp.grpc import resource_write

BASE = "https://cvp.example.com"
CVP = "cvp.example.com"
TOKEN = "test-token"

WORKSPACE_PATH = "/api/resources/workspace/v1/WorkspaceConfig"
INPUTS_PATH = "/api/resources/studio/v1/InputsConfig"
STUDIO_PATH = "/api/resources/studio/v1/StudioConfig"
TAGS_PATH = "/api/resources/studio/v1/AssignedTagsConfig"


def _key(workspace_id="ws-mcp-1"):
    return {"key": {"workspaceId": workspace_id}}


def _post(path, body, **kwargs):
    """POST through the helper with urlopen mocked; returns (result, mock)."""
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps({"value": {"ok": True}}).encode()
        result = resource_write.post_resource_config(
            BASE, path, body, TOKEN, cvp_endpoint=CVP, **kwargs
        )
    return result, mock_open


def _delete(path, params, **kwargs):
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = b"{}"
        result = resource_write.delete_resource_config(
            BASE, path, params, TOKEN, cvp_endpoint=CVP, **kwargs
        )
    return result, mock_open


# --- path allowlist ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/resources/changecontrol/v1/ChangeControlConfig",
        "/api/resources/configlet/v1/ConfigletConfig",
        # Allowlisted prefix but with a query string: not an exact match.
        "/api/resources/workspace/v1/WorkspaceConfig?key.workspaceId=ws-mcp-1",
        "/api/resources/workspace/v1/WorkspaceConfigX",
    ],
)
def test_post_bad_path_never_calls_urlopen(path):
    (obj, err), mock_open = _post(path, _key())
    assert obj is None
    assert err == "path_not_allowed"
    mock_open.assert_not_called()


def test_post_allows_each_allowlisted_path():
    for path in (WORKSPACE_PATH, INPUTS_PATH, TAGS_PATH, STUDIO_PATH):
        (obj, err), mock_open = _post(path, _key())
        assert err is None, path
        assert obj == {"value": {"ok": True}}
        assert mock_open.call_count == 1


def test_post_assigned_tags_config_2_1_reaches_urlopen():
    """Spec 2.1 assign_cvp_studio_tags POSTs the replacement tag query."""
    body = {
        "key": {"workspaceId": "ws-mcp-1", "studioId": "studio-1"},
        "query": "device:leaf1",
    }
    (obj, err), mock_open = _post(TAGS_PATH, body)
    assert err is None
    assert obj == {"value": {"ok": True}}
    assert mock_open.call_count == 1
    req = mock_open.call_args[0][0]
    assert req.method == "POST"
    assert req.full_url == f"{BASE}{TAGS_PATH}"
    assert json.loads(req.data.decode()) == body


def test_post_studio_config_2_2_reaches_urlopen():
    """Spec 2.2 create_cvp_studio / delete_cvp_studio POST StudioConfig."""
    body = {
        "key": {"workspaceId": "ws-mcp-1", "studioId": "studio-1"},
        "displayName": "MCP studio",
    }
    (obj, err), mock_open = _post(STUDIO_PATH, body)
    assert err is None
    assert obj == {"value": {"ok": True}}
    assert mock_open.call_count == 1
    req = mock_open.call_args[0][0]
    assert req.method == "POST"
    assert req.full_url == f"{BASE}{STUDIO_PATH}"
    assert json.loads(req.data.decode()) == body


def test_post_studio_config_remove_2_2_reaches_urlopen():
    body = {
        "key": {"workspaceId": "ws-mcp-1", "studioId": "studio-1"},
        "remove": True,
    }
    (obj, err), mock_open = _post(STUDIO_PATH, body)
    assert err is None
    assert mock_open.call_count == 1


def test_post_change_control_config_never_calls_urlopen():
    """ChangeControlConfig is forbidden at every slice, 2.1 and 2.2 included."""
    body = {"key": {"id": "cc-1"}, "change": {"name": "cc"}}
    (obj, err), mock_open = _post(
        "/api/resources/changecontrol/v1/ChangeControlConfig", body
    )
    assert obj is None
    assert err == "path_not_allowed"
    mock_open.assert_not_called()


# --- request enum allowlist -------------------------------------------------


@pytest.mark.parametrize(
    "request_value",
    ["REQUEST_ROLLBACK", "REQUEST_SUBMIT_FORCE", "REQUEST_UNSPECIFIED", "", "start"],
)
def test_post_unknown_request_never_calls_urlopen(request_value):
    body = dict(_key(), request=request_value)
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert obj is None
    assert err == "request_not_allowed"
    mock_open.assert_not_called()


def test_post_non_string_request_rejected():
    body = dict(_key(), request={"nested": "REQUEST_START_BUILD"})
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert err == "request_not_allowed"
    mock_open.assert_not_called()


def test_post_capitalized_request_key_also_checked():
    body = dict(_key(), Request="REQUEST_ROLLBACK")
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert err == "request_not_allowed"
    mock_open.assert_not_called()


def test_post_start_build_allowed():
    body = dict(_key(), request=resource_write.REQUEST_START_BUILD)
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert err is None
    assert mock_open.call_count == 1


# --- submit gate ------------------------------------------------------------


def test_post_submit_disabled_when_write_access_missing(monkeypatch):
    """Fail-close: an unimportable gate module must refuse, not POST."""
    monkeypatch.setitem(sys.modules, "cvp_mcp.write_access", None)
    body = dict(_key(), request=resource_write.REQUEST_SUBMIT)
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert obj is None
    assert err == "submit_disabled"
    mock_open.assert_not_called()


def test_post_submit_disabled_when_gate_false():
    with patch.object(resource_write, "_submit_allowed", return_value=False):
        body = dict(_key(), request=resource_write.REQUEST_SUBMIT)
        (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert err == "submit_disabled"
    mock_open.assert_not_called()


def test_post_submit_allowed_when_gate_true():
    with patch.object(resource_write, "_submit_allowed", return_value=True):
        body = dict(_key(), request=resource_write.REQUEST_SUBMIT)
        (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert err is None
    assert mock_open.call_count == 1


def test_post_submit_refused_through_real_env_gate(monkeypatch):
    """No patching of ``_submit_allowed``: the real env gate must refuse."""
    monkeypatch.delenv(write_access.WRITES_ENV, raising=False)
    monkeypatch.delenv(write_access.SUBMIT_ENV, raising=False)
    body = dict(_key(), request=resource_write.REQUEST_SUBMIT)
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert obj is None
    assert err == "submit_disabled"
    mock_open.assert_not_called()


def test_post_submit_refused_when_staleness_field_unregistered(monkeypatch):
    """Both env vars on is not enough: submit stays 2.1-unregistered."""
    monkeypatch.setenv(write_access.WRITES_ENV, "1")
    monkeypatch.setenv(write_access.SUBMIT_ENV, "1")
    monkeypatch.setattr(write_access, "SUBMIT_STALENESS_FIELD", None)
    body = dict(_key(), request=resource_write.REQUEST_SUBMIT)
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert obj is None
    assert err == "submit_disabled"
    mock_open.assert_not_called()


# --- envelope key denylist --------------------------------------------------


@pytest.mark.parametrize("key_name", ["start", "Start", "schedule", "SCHEDULE"])
def test_post_workspace_start_or_schedule_rejected(key_name):
    body = dict(_key(), **{key_name: True})
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert obj is None
    assert err in ("forbidden_key:start", "forbidden_key:schedule")
    mock_open.assert_not_called()


def test_post_studio_request_params_start_rejected():
    body = dict(_key(), requestParams={"start": True})
    (obj, err), mock_open = _post(STUDIO_PATH, body)
    assert err == "forbidden_key:start"
    mock_open.assert_not_called()


def test_post_workspace_snake_request_params_schedule_rejected():
    body = dict(_key(), request_params={"schedule": "now"})
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert err == "forbidden_key:schedule"
    mock_open.assert_not_called()


def test_post_inputs_string_containing_start_is_allowed():
    """The denylist must not scan the InputsConfig ``inputs`` JSON string."""
    body = dict(_key(), inputs=json.dumps({"change": 1, "start": "Ethernet6 start"}))
    (obj, err), mock_open = _post(INPUTS_PATH, body)
    assert err is None
    assert mock_open.call_count == 1


def test_post_inputs_change_string_allowed():
    body = {"key": {"workspaceId": "ws-mcp-1"}, "inputs": '{"change":1}'}
    (obj, err), mock_open = _post(INPUTS_PATH, body)
    assert err is None
    assert mock_open.call_count == 1


def test_post_tags_config_top_level_start_not_denylisted():
    """Denylist covers WorkspaceConfig / StudioConfig only."""
    body = dict(_key(), start=True)
    (obj, err), mock_open = _post(TAGS_PATH, body)
    assert err is None
    assert mock_open.call_count == 1


# --- workspace id -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"key": {"workspaceId": ""}},
        {"key": {"workspaceId": "   "}},
        {"key": {}},
        {"key": "ws-mcp-1"},
        {},
    ],
)
def test_post_missing_workspace_id_never_calls_urlopen(body):
    (obj, err), mock_open = _post(WORKSPACE_PATH, body)
    assert obj is None
    assert err == "workspace_id_required"
    mock_open.assert_not_called()


def test_post_accepts_snake_case_workspace_id():
    (obj, err), mock_open = _post(INPUTS_PATH, {"key": {"workspace_id": "ws-mcp-1"}})
    assert err is None
    assert mock_open.call_count == 1


# --- host allowlist / preconditions ----------------------------------------


def test_post_blocks_non_cvp_host():
    with patch("urllib.request.urlopen") as mock_open:
        obj, err = resource_write.post_resource_config(
            "https://evil.example.net", WORKSPACE_PATH, _key(), TOKEN, cvp_endpoint=CVP
        )
    assert obj is None
    assert err == "uri_host_not_allowed"
    mock_open.assert_not_called()


def test_post_missing_token_never_calls_urlopen():
    with patch("urllib.request.urlopen") as mock_open:
        obj, err = resource_write.post_resource_config(
            BASE, WORKSPACE_PATH, _key(), "  ", cvp_endpoint=CVP
        )
    assert err == "missing_token"
    mock_open.assert_not_called()


def test_post_missing_base_url_never_calls_urlopen():
    with patch("urllib.request.urlopen") as mock_open:
        obj, err = resource_write.post_resource_config(
            "", WORKSPACE_PATH, _key(), TOKEN, cvp_endpoint=CVP
        )
    assert err == "missing_base_url"
    mock_open.assert_not_called()


def test_post_non_dict_body_rejected():
    (obj, err), mock_open = _post(WORKSPACE_PATH, ["not", "a", "dict"])
    assert err == "invalid_body"
    mock_open.assert_not_called()


def test_post_sends_bearer_and_json_body():
    body = dict(_key(), displayName="ws")
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = b'{"value":{"ok":true}}'
        obj, err = resource_write.post_resource_config(
            BASE, WORKSPACE_PATH, body, TOKEN, cvp_endpoint=CVP
        )
    assert err is None
    req = mock_open.call_args[0][0]
    assert req.method == "POST"
    assert req.full_url == f"{BASE}{WORKSPACE_PATH}"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(req.data.decode()) == body


def test_post_http_error_returns_code():
    import urllib.error

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.side_effect = urllib.error.HTTPError(
            f"{BASE}{WORKSPACE_PATH}", 403, "Forbidden", {}, None
        )
        obj, err = resource_write.post_resource_config(
            BASE, WORKSPACE_PATH, _key(), TOKEN, cvp_endpoint=CVP
        )
    assert obj is None
    assert err == "http_error:403"


# --- delete -----------------------------------------------------------------


def test_delete_bad_path_never_calls_urlopen():
    (obj, err), mock_open = _delete(INPUTS_PATH, {"key.workspaceId": "ws-mcp-1"})
    assert obj is None
    assert err == "path_not_allowed"
    mock_open.assert_not_called()


@pytest.mark.parametrize("bad_id", ["ws-mcp-1&key.foo=1", "ws?x", "ws#frag"])
def test_delete_query_separator_in_id_never_calls_urlopen(bad_id):
    (obj, err), mock_open = _delete(WORKSPACE_PATH, {"key.workspaceId": bad_id})
    assert obj is None
    assert err == "invalid_workspace_id"
    mock_open.assert_not_called()


@pytest.mark.parametrize("params", [{"key.workspaceId": ""}, {"key.workspaceId": " "}])
def test_delete_empty_workspace_id_never_calls_urlopen(params):
    (obj, err), mock_open = _delete(WORKSPACE_PATH, params)
    assert obj is None
    assert err == "workspace_id_required"
    mock_open.assert_not_called()


def test_delete_missing_key_param_never_calls_urlopen():
    (obj, err), mock_open = _delete(WORKSPACE_PATH, {"other": "x"})
    assert err == "invalid_params"
    mock_open.assert_not_called()


def test_delete_empty_params_rejected():
    (obj, err), mock_open = _delete(WORKSPACE_PATH, {})
    assert err == "invalid_params"
    mock_open.assert_not_called()


def test_delete_blocks_non_cvp_host():
    with patch("urllib.request.urlopen") as mock_open:
        obj, err = resource_write.delete_resource_config(
            "https://evil.example.net",
            WORKSPACE_PATH,
            {"key.workspaceId": "ws-mcp-1"},
            TOKEN,
            cvp_endpoint=CVP,
        )
    assert err == "uri_host_not_allowed"
    mock_open.assert_not_called()


def test_delete_encodes_params_and_sets_method():
    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = b""
        obj, err = resource_write.delete_resource_config(
            BASE,
            WORKSPACE_PATH,
            {"key.workspaceId": "ws mcp/1"},
            TOKEN,
            cvp_endpoint=CVP,
        )
    assert err is None
    assert obj == {}
    req = mock_open.call_args[0][0]
    assert req.method == "DELETE"
    assert req.full_url == f"{BASE}{WORKSPACE_PATH}?key.workspaceId=ws+mcp%2F1"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"


def test_delete_parses_json_response():
    (obj, err), mock_open = _delete(WORKSPACE_PATH, {"key.workspaceId": "ws-mcp-1"})
    assert err is None
    assert obj == {}
    assert mock_open.call_count == 1
