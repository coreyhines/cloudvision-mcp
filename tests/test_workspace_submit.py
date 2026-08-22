"""Tests for the unregistered workspace submit library (bucket 1c).

HTTP is mocked at the transport boundary: reads through the ``studios`` GET
helper, the write through ``urllib.request.urlopen``. Every refusal asserts
that no request of any kind was made.

``SUBMIT_STALENESS_FIELD`` is ``None`` in production and stays that way: the
tests that need submit enabled override it with ``monkeypatch.setattr``, which
is reverted after each test.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from cvp_mcp import write_access
from cvp_mcp.grpc import workspace_submit
from cvp_mcp.write_access import SUBMIT_ENV, WRITES_ENV, preview_token

WORKSPACE = "ws-mcp-test-20260822-abcd1234"
BUILD_ID = "build-20260822-01"
TOKEN = "2026-08-22T14:00:00Z"
BUILD_TIME = "2026-08-22T14:00:00Z"
REQUEST_ID = "11111111-2222-3333-4444-555555555555"

# Wire spelling of Workspace ``lastModifiedAt``. Local to this module: it is
# deliberately NOT written back into ``cvp_mcp.write_access``.
STALENESS_FIELD = "lastModifiedAt"

DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}


@pytest.fixture
def _writes_on(monkeypatch):
    """Writes on, submit still unregistered (production default)."""
    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.delenv(SUBMIT_ENV, raising=False)


@pytest.fixture
def _submit_on(monkeypatch):
    """Both env gates on plus a test-local staleness field and submit gate."""
    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.setenv(SUBMIT_ENV, "1")
    monkeypatch.setattr(write_access, "SUBMIT_STALENESS_FIELD", STALENESS_FIELD)
    monkeypatch.setattr(write_access, "submit_enabled", lambda: True)


# --- fakes ------------------------------------------------------------------


def _workspace_value(
    *,
    state="WORKSPACE_STATE_PENDING",
    last_modified=TOKEN,
    last_build_id=BUILD_ID,
    needs_build=None,
    responses=None,
):
    value = {
        "key": {"workspaceId": WORKSPACE},
        "displayName": "mcp submit test",
        "state": state,
        "lastBuildId": last_build_id,
        "lastModifiedAt": last_modified,
    }
    if needs_build is not None:
        value["needsBuild"] = needs_build
    if responses is not None:
        value["responses"] = responses
    return {"value": value, "time": last_modified}


def _build_value(*, state="BUILD_STATE_SUCCESS", error=""):
    return {
        "value": {
            "key": {"workspaceId": WORKSPACE, "buildId": BUILD_ID},
            "state": state,
            "error": error,
            "builtBy": "mcp",
        },
        "time": BUILD_TIME,
    }


def _as_result(spec, default):
    """Normalize a fixture spec into a ``get_json_with_bearer`` return pair."""
    if spec is None:
        spec = default
    if spec == ("missing",):
        return None, "http_error:404"
    if isinstance(spec, tuple) and spec and spec[0] == "error":
        return None, spec[1]
    return spec, None


@contextmanager
def _mocked(workspace=None, build=None, post_response=None):
    """Mock the Workspace / WorkspaceBuild GETs and the mutating urlopen.

    ``workspace`` may be a list of specs consumed one per GET (the last entry
    repeats), which is how the preview-then-confirm re-GET is made to disagree.
    """
    ws_specs = list(workspace) if isinstance(workspace, list) else [workspace]
    ws_results = [_as_result(spec, _workspace_value()) for spec in ws_specs]
    build_result = _as_result(build, _build_value())
    calls = {"workspace": 0, "build": 0}

    def _get(uri, _token, **_kwargs):
        if "WorkspaceBuild" in uri:
            calls["build"] += 1
            return build_result
        index = min(calls["workspace"], len(ws_results) - 1)
        calls["workspace"] += 1
        return ws_results[index]

    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer", side_effect=_get
        ) as get_json,
        patch("urllib.request.urlopen") as urlopen,
    ):
        resp = urlopen.return_value.__enter__.return_value
        resp.read.return_value = json.dumps(
            post_response
            if post_response is not None
            else {"value": {}, "time": "2026-08-22T14:05:00Z"}
        ).encode()
        yield {"get_json": get_json, "urlopen": urlopen, "calls": calls}


def _obj(envelope):
    return envelope["object"]


def _code(envelope):
    return _obj(envelope)["error"]["code"]


def _posted_body(urlopen):
    return json.loads(urlopen.call_args[0][0].data.decode())


def _preview(**kwargs):
    return workspace_submit.submit_cvp_workspace(
        DATADICT, WORKSPACE, BUILD_ID, TOKEN, **kwargs
    )


def _confirm(preview_env, **kwargs):
    args = {
        "request_id": _obj(preview_env)["request_id"],
        "confirm": True,
        "allow_submit": True,
        "preview_token_value": _obj(preview_env)["preview_token"],
    }
    args.update(kwargs)
    return workspace_submit.submit_cvp_workspace(
        DATADICT, WORKSPACE, BUILD_ID, TOKEN, **args
    )


# --- the field gate ---------------------------------------------------------


def test_production_staleness_field_is_still_none():
    """Registering submit is a separate, human-confirmed change."""
    assert write_access.SUBMIT_STALENESS_FIELD is None
    assert write_access.submit_enabled() is False


def test_field_none_refuses_without_any_http(_writes_on):
    with _mocked() as mocks:
        env = _preview()
    assert _code(env) == "submit_disabled"
    assert _obj(env)["dry_run"] is True
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_field_none_refuses_even_with_both_env_vars_on(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.setenv(SUBMIT_ENV, "1")
    assert write_access.SUBMIT_STALENESS_FIELD is None
    with _mocked() as mocks:
        env = workspace_submit.submit_cvp_workspace(
            DATADICT,
            WORKSPACE,
            BUILD_ID,
            TOKEN,
            request_id=REQUEST_ID,
            confirm=True,
            allow_submit=True,
            preview_token_value="anything",
        )
    assert _code(env) == "submit_disabled"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_field_set_to_unreadable_name_refuses_without_any_http(_submit_on, monkeypatch):
    monkeypatch.setattr(write_access, "SUBMIT_STALENESS_FIELD", "someOtherField")
    with _mocked() as mocks:
        env = _preview()
    assert _code(env) == "submit_disabled"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_writes_disabled_beats_submit_env(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    monkeypatch.setenv(SUBMIT_ENV, "1")
    monkeypatch.setattr(write_access, "SUBMIT_STALENESS_FIELD", STALENESS_FIELD)
    with _mocked() as mocks:
        env = _preview()
    assert _code(env) == "writes_disabled"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_submit_env_off_refuses_even_with_allow_submit(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")
    monkeypatch.delenv(SUBMIT_ENV, raising=False)
    monkeypatch.setattr(write_access, "SUBMIT_STALENESS_FIELD", STALENESS_FIELD)
    with _mocked() as mocks:
        env = _preview(request_id=REQUEST_ID, confirm=True, allow_submit=True)
    assert _code(env) == "submit_disabled"
    mocks["urlopen"].assert_not_called()


# --- argument gates ---------------------------------------------------------


@pytest.mark.parametrize(
    ("workspace_id", "code"),
    [
        ("", "workspace_id_required"),
        ("   ", "workspace_id_required"),
        ("builtin-studios", "builtin_workspace_forbidden"),
        ("ws-other-1", "invalid_workspace_id"),
    ],
)
def test_workspace_id_refusals_never_touch_http(_submit_on, workspace_id, code):
    with _mocked() as mocks:
        env = workspace_submit.submit_cvp_workspace(
            DATADICT, workspace_id, BUILD_ID, TOKEN
        )
    assert _code(env) == code
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("build_id", "staleness"),
    [("", TOKEN), ("   ", TOKEN), (BUILD_ID, ""), (BUILD_ID, "  "), ("", "")],
)
def test_empty_build_id_or_token_refuses_staleness_token_required(
    _submit_on, build_id, staleness
):
    with _mocked() as mocks:
        env = workspace_submit.submit_cvp_workspace(
            DATADICT, WORKSPACE, build_id, staleness
        )
    assert _code(env) == "staleness_token_required"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_blank_request_id_refused(_submit_on):
    with _mocked() as mocks:
        env = _preview(request_id="  ")
    assert _code(env) == "invalid_request_id"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_confirm_without_request_id_refuses(_submit_on):
    with _mocked() as mocks:
        env = _preview(confirm=True, allow_submit=True, preview_token_value="x")
    assert _code(env) == "invalid_request_id"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_missing_credentials_refuse_before_preflight(_submit_on):
    with _mocked() as mocks:
        env = workspace_submit.submit_cvp_workspace(
            {"cvtoken": "", "cvp": "cvp.example.com"}, WORKSPACE, BUILD_ID, TOKEN
        )
    assert _code(env) == "preflight_failed"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- confirm / allow_submit -------------------------------------------------


def test_confirm_without_allow_submit_refuses_without_http(_submit_on):
    with _mocked() as mocks:
        env = _preview(
            request_id=REQUEST_ID, confirm=True, preview_token_value="whatever"
        )
    assert _code(env) == "submit_not_allowed"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_preview_makes_no_post_and_returns_token(_submit_on):
    with _mocked() as mocks:
        env = _preview()
        assert mocks["calls"] == {"workspace": 1, "build": 1}
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["dry_run"] is True
    assert obj["done"] is False
    assert obj["request"] == "REQUEST_SUBMIT"
    assert obj["build_id"] == BUILD_ID
    assert obj["workspace_staleness_token"] == TOKEN
    assert obj["staleness"]["build_state"] == "BUILD_STATE_SUCCESS"
    assert obj["preview_token"] == preview_token(
        workspace_submit.TOOL_NAME,
        {
            "workspace_id": WORKSPACE,
            "build_id": BUILD_ID,
            "workspace_staleness_token": TOKEN,
            "request_id": obj["request_id"],
        },
    )
    mocks["urlopen"].assert_not_called()


def test_preview_with_allow_submit_still_only_previews(_submit_on):
    with _mocked() as mocks:
        env = _preview(allow_submit=True)
    assert _obj(env)["outcome"] == "preview"
    mocks["urlopen"].assert_not_called()


def test_confirm_without_preview_token_refuses(_submit_on):
    with _mocked() as mocks:
        env = _preview(request_id=REQUEST_ID, confirm=True, allow_submit=True)
    assert _code(env) == "preview_required"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_confirm_with_token_for_another_build_refuses(_submit_on):
    stale = preview_token(
        workspace_submit.TOOL_NAME,
        {
            "workspace_id": WORKSPACE,
            "build_id": "build-other",
            "workspace_staleness_token": TOKEN,
            "request_id": REQUEST_ID,
        },
    )
    with _mocked() as mocks:
        env = _preview(
            request_id=REQUEST_ID,
            confirm=True,
            allow_submit=True,
            preview_token_value=stale,
        )
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


# --- happy path -------------------------------------------------------------


def test_confirm_posts_request_submit_once_to_workspace_config(_submit_on):
    with _mocked() as mocks:
        preview = _preview()
        env = _confirm(preview)
        assert mocks["urlopen"].call_count == 1
        request = mocks["urlopen"].call_args[0][0]
        body = _posted_body(mocks["urlopen"])
        # One GET pair for the preview, one re-GET pair for the confirm.
        assert mocks["calls"] == {"workspace": 2, "build": 2}

    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["outcome"] != "succeeded"
    assert obj["dry_run"] is False
    assert obj["done"] is False
    assert obj["error"] is None
    assert obj["resource_time"] == "2026-08-22T14:05:00Z"
    assert obj["cc_ids"] is None
    assert request.method == "POST"
    assert request.full_url.endswith(workspace_submit.WORKSPACE_CONFIG_PATH)
    assert "ChangeControl" not in request.full_url
    assert body == {
        "key": {"workspaceId": WORKSPACE},
        "request": "REQUEST_SUBMIT",
        "requestParams": {"requestId": _obj(preview)["request_id"]},
    }


def test_confirm_reports_cc_ids_only_when_present(_submit_on):
    response = {
        "value": {"ccIds": {"values": ["cc-1"]}},
        "time": "2026-08-22T14:05:00Z",
    }
    with _mocked(post_response=response) as mocks:
        preview = _preview()
        env = _confirm(preview)
        assert mocks["urlopen"].call_count == 1
    assert _obj(env)["cc_ids"] == ["cc-1"]


def test_empty_cc_ids_reported_as_unknown(_submit_on):
    response = {"value": {"ccIds": {"values": []}}, "time": "2026-08-22T14:05:00Z"}
    with _mocked(post_response=response):
        preview = _preview()
        env = _confirm(preview)
    assert _obj(env)["cc_ids"] is None


def test_post_failure_reports_resource_write_failed(_submit_on):
    with _mocked() as mocks:
        mocks["urlopen"].side_effect = OSError("boom")
        preview = _preview()
        env = _confirm(preview)
    assert _code(env) == "resource_write_failed"


# --- staleness / build preflight -------------------------------------------


def test_token_mismatch_refuses_before_post(_submit_on):
    with _mocked(workspace=_workspace_value(last_modified="2026-08-22T15:00:00Z")) as m:
        env = _preview()
    assert _code(env) == "staleness_token_mismatch"
    assert _obj(env)["error"]["details"]["observed"] == "2026-08-22T15:00:00Z"
    m["urlopen"].assert_not_called()


def test_missing_last_modified_at_refuses(_submit_on):
    with _mocked(workspace=_workspace_value(last_modified="")) as mocks:
        env = _preview()
    assert _code(env) == "staleness_token_mismatch"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    "workspace",
    [
        _workspace_value(last_build_id="build-newer"),
        _workspace_value(last_build_id=""),
        _workspace_value(needs_build=True),
    ],
)
def test_edits_after_the_named_build_refuse(_submit_on, workspace):
    with _mocked(workspace=workspace) as mocks:
        env = _preview()
    assert _code(env) == "workspace_modified_after_build"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    "state", ["BUILD_STATE_FAIL", "BUILD_STATE_CANCELED", "BUILD_STATE_UNSPECIFIED", ""]
)
def test_only_successful_builds_may_be_submitted(_submit_on, state):
    with _mocked(build=_build_value(state=state)) as mocks:
        env = _preview()
    assert _code(env) == "build_not_successful"
    mocks["urlopen"].assert_not_called()


def test_missing_build_refuses(_submit_on):
    with _mocked(build=("missing",)) as mocks:
        env = _preview()
    assert _code(env) == "build_not_found"
    mocks["urlopen"].assert_not_called()


def test_missing_workspace_refuses(_submit_on):
    with _mocked(workspace=("missing",)) as mocks:
        env = _preview()
    assert _code(env) == "workspace_not_found"
    assert mocks["calls"]["build"] == 0
    mocks["urlopen"].assert_not_called()


def test_workspace_read_failure_refuses(_submit_on):
    with _mocked(workspace=("error", "http_error:503")) as mocks:
        env = _preview()
    assert _code(env) == "workspace_read_failed"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("WORKSPACE_STATE_SUBMITTED", "workspace_not_pending"),
        ("WORKSPACE_STATE_ABANDONED", "workspace_not_pending"),
        ("", "workspace_state_unknown"),
    ],
)
def test_only_pending_workspaces_may_be_submitted(_submit_on, state, code):
    with _mocked(workspace=_workspace_value(state=state)) as mocks:
        env = _preview()
    assert _code(env) == code
    mocks["urlopen"].assert_not_called()


def test_non_terminal_response_refuses(_submit_on):
    responses = {"values": {"req-1": {"status": "RESPONSE_STATUS_RUNNING"}}}
    with _mocked(workspace=_workspace_value(responses=responses)) as mocks:
        env = _preview()
    assert _code(env) == "build_in_progress"
    mocks["urlopen"].assert_not_called()


def test_workspace_edited_between_preview_and_confirm_refuses(_submit_on):
    fresh = _workspace_value()
    edited = _workspace_value(last_build_id="build-newer")
    with _mocked(workspace=[fresh, edited]) as mocks:
        preview = _preview()
        assert _obj(preview)["outcome"] == "preview"
        env = _confirm(preview)
    assert _code(env) == "workspace_modified_after_build"
    mocks["urlopen"].assert_not_called()


def test_token_changed_between_preview_and_confirm_refuses(_submit_on):
    """The confirm path re-GETs: an edit landing after the preview is caught."""
    fresh = _workspace_value()
    edited = _workspace_value(last_modified="2026-08-22T15:30:00Z")
    with _mocked(workspace=[fresh, edited]) as mocks:
        preview = _preview()
        env = _confirm(preview)
    assert _code(env) == "staleness_token_mismatch"
    mocks["urlopen"].assert_not_called()


def test_refusals_on_confirm_never_reach_the_get(_submit_on):
    """A bad preview_token is rejected before the re-GET, not after it."""
    with _mocked() as mocks:
        env = _preview(
            request_id=REQUEST_ID,
            confirm=True,
            allow_submit=True,
            preview_token_value="not-the-token",
        )
    assert _code(env) == "preview_required"
    mocks["get_json"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- registration guard -----------------------------------------------------


def test_submit_is_not_registered_as_an_mcp_tool():
    from pathlib import Path

    source = Path(workspace_submit.__file__).read_text()
    assert "mcp.tool" not in source
    server = Path(__file__).resolve().parents[1] / "cloudvision_mcp.py"
    assert "submit_cvp_workspace" not in server.read_text()
