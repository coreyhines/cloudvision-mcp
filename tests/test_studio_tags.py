"""Tests for the AssignedTags read and assign CAS (bucket 1a).

HTTP is mocked at the transport boundary: the workspace GET through the
``studios`` JSON helper, the AssignedTags GET through the NDJSON helper, and the
write through ``urllib.request.urlopen``. Every refusal asserts that no mutating
request was made.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_tags
from cvp_mcp.write_access import WRITES_ENV, preview_token

STUDIO_ID = "studio-campus-access-interfaces"
WORKSPACE = "ws-mcp-test-20260822-abcd1234"
CURRENT_QUERY = "device:campus-leaf1"
NEW_QUERY = "device:campus-leaf1 OR device:campus-leaf2"

DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    """Writes are enabled for every test unless a test turns them back off."""
    monkeypatch.setenv(WRITES_ENV, "1")


# --- fixtures / fakes -------------------------------------------------------


def _tag_row(workspace_id=WORKSPACE, query=CURRENT_QUERY, studio_id=STUDIO_ID):
    return {
        "key": {"studioId": studio_id, "workspaceId": workspace_id},
        "query": query,
    }


def _workspace_value(state="WORKSPACE_STATE_PENDING"):
    return {
        "value": {
            "key": {"workspaceId": WORKSPACE},
            "displayName": "mcp test",
            "state": state,
            "lastModifiedAt": "2026-08-22T14:00:00Z",
        },
        "time": "2026-08-22T14:00:00Z",
    }


@contextmanager
def _mocked(workspace=None, tags=None, post_status=None):
    """Mock workspace GET, AssignedTags/all GET and the mutating urlopen.

    ``workspace`` defaults to a pending draft; pass ``("missing",)`` for HTTP
    404 or ``("error", code)`` for a failed GET. ``tags`` is a list of raw
    AssignedTags resource values, or ``("error", code)`` to fail the GET.
    """
    if workspace is None:
        workspace = _workspace_value()
    if workspace == ("missing",):
        ws_result = (None, "http_error:404")
    elif isinstance(workspace, tuple) and workspace and workspace[0] == "error":
        ws_result = (None, workspace[1])
    else:
        ws_result = (workspace, None)

    if isinstance(tags, tuple) and tags and tags[0] == "error":
        tag_result = (None, tags[1], [])
    else:
        tag_result = (list(tags or []), None, [])

    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer", return_value=ws_result
        ) as get_ws,
        patch(
            "cvp_mcp.grpc.studio_tags.get_ndjson_all_values_with_bearer",
            return_value=tag_result,
        ) as get_tags,
        patch("urllib.request.urlopen") as urlopen,
    ):
        resp = urlopen.return_value.__enter__.return_value
        resp.read.return_value = json.dumps(
            post_status or {"value": {}, "time": "2026-08-22T14:05:00Z"}
        ).encode()
        yield {"workspace_get": get_ws, "tags_get": get_tags, "urlopen": urlopen}


def _obj(envelope):
    return envelope["object"]


def _code(envelope):
    return _obj(envelope)["error"]["code"]


def _posted_body(urlopen):
    request = urlopen.call_args[0][0]
    return json.loads(request.data.decode())


def _assign(**kwargs):
    args = {
        "datadict": DATADICT,
        "studio_id": STUDIO_ID,
        "workspace_id": WORKSPACE,
        "query": NEW_QUERY,
        "expected_current_query": CURRENT_QUERY,
    }
    args.update(kwargs)
    return studio_tags.assign_cvp_studio_tags(**args)


def _preview_token():
    return preview_token(
        "assign_cvp_studio_tags",
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "query": NEW_QUERY,
            "expected_current_query": CURRENT_QUERY,
        },
    )


# --- read: get_cvp_studio_assigned_tags -------------------------------------


def test_read_returns_filtered_rows():
    rows = [
        _tag_row(),
        _tag_row(workspace_id="", query="device:mainline"),
        _tag_row(studio_id="studio-other", query="device:other"),
    ]
    with _mocked(tags=rows) as mocks:
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "full"
    assert env["items"] == [
        {"studio_id": STUDIO_ID, "workspace_id": WORKSPACE, "query": CURRENT_QUERY}
    ]
    assert "assigned_tags_unavailable" not in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_read_defaults_to_mainline_workspace():
    with _mocked(tags=[_tag_row(workspace_id="", query="device:mainline")]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID)
    assert env["coverage"] == "full"
    assert env["items"][0]["workspace_id"] == ""
    assert env["items"][0]["query"] == "device:mainline"


def test_read_404_is_unavailable_and_invents_nothing():
    with _mocked(tags=("error", "http_error:404")) as mocks:
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "assigned_tags_unavailable" in env["warnings"]
    assert "http_error:404" in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_read_empty_stream_is_unavailable():
    with _mocked(tags=("error", "empty_response")):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "assigned_tags_unavailable" in env["warnings"]


def test_read_no_matching_row_is_unavailable():
    with _mocked(tags=[_tag_row(studio_id="studio-other")]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "assigned_tags_unavailable" in env["warnings"]


def test_read_transport_error_is_not_reported_as_unavailable():
    with _mocked(tags=("error", "uri_fetch_failed")):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "uri_fetch_failed" in env["warnings"]
    assert "assigned_tags_unavailable" not in env["warnings"]


def test_read_without_studio_id_makes_no_http_call():
    with _mocked() as mocks:
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, "  ", WORKSPACE)
    assert env["coverage"] == "none"
    assert env["warnings"] == ["missing_studio_id"]
    mocks["tags_get"].assert_not_called()


def test_read_runs_without_the_writes_gate(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    with _mocked(tags=[_tag_row()]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "full"
    assert env["items"][0]["query"] == CURRENT_QUERY


# --- assign: gates and validation -------------------------------------------


def test_assign_refused_when_writes_disabled(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign()
    assert _obj(env)["outcome"] == "refused"
    assert _code(env) == "writes_disabled"
    assert env["coverage"] == "none"
    mocks["workspace_get"].assert_not_called()
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("query", ["", "   ", None])
def test_assign_refuses_empty_query(query):
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(query=query)
    assert _code(env) == "empty_query_forbidden"
    mocks["workspace_get"].assert_not_called()
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("workspace_id", "code"),
    [
        ("", "workspace_id_required"),
        ("   ", "workspace_id_required"),
        ("builtin-studios-v1", "builtin_workspace_forbidden"),
        ("BUILTIN-studios-v1", "builtin_workspace_forbidden"),
        ("ws-other-1", "invalid_workspace_id"),
    ],
)
def test_assign_workspace_id_refusals_never_touch_http(workspace_id, code):
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(workspace_id=workspace_id)
    assert _code(env) == code
    mocks["workspace_get"].assert_not_called()
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_assign_requires_studio_id():
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(studio_id=" ")
    assert _code(env) == "studio_id_required"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("expected", ["", None])
def test_assign_requires_expected_current_query(expected):
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(expected_current_query=expected)
    assert _code(env) == "expected_current_query_required"
    mocks["workspace_get"].assert_not_called()
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_incomplete_credentials():
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(datadict={"cvtoken": "", "cvp": "cvp.example.com"})
    assert _code(env) == "preflight_failed"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- assign: workspace preflight --------------------------------------------


def test_assign_refuses_missing_workspace():
    with _mocked(workspace=("missing",), tags=[_tag_row()]) as mocks:
        env = _assign()
    assert _code(env) == "workspace_not_found"
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_failed_workspace_read():
    with _mocked(workspace=("error", "http_error:503"), tags=[_tag_row()]) as mocks:
        env = _assign()
    assert _code(env) == "workspace_read_failed"
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_non_pending_workspace():
    with _mocked(
        workspace=_workspace_value(state="WORKSPACE_STATE_SUBMITTED"),
        tags=[_tag_row()],
    ) as mocks:
        env = _assign()
    assert _code(env) == "workspace_not_pending"
    assert _obj(env)["error"]["details"]["state"] == "WORKSPACE_STATE_SUBMITTED"
    mocks["tags_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- assign: compare-and-set ------------------------------------------------


def test_assign_refuses_when_tags_unavailable():
    with _mocked(tags=("error", "http_error:404")) as mocks:
        env = _assign()
    assert _code(env) == "assigned_tags_unavailable"
    assert "assigned_tags_unavailable" in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_when_tags_read_fails():
    with _mocked(tags=("error", "uri_fetch_failed")) as mocks:
        env = _assign()
    assert _code(env) == "assigned_tags_read_failed"
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_ambiguous_rows():
    with _mocked(tags=[_tag_row(), _tag_row(query="device:dup")]) as mocks:
        env = _assign()
    assert _code(env) == "assigned_tags_ambiguous"
    assert _obj(env)["error"]["details"]["matches"] == 2
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_expected_current_mismatch():
    with _mocked(tags=[_tag_row(query="device:campus-leaf9")]) as mocks:
        env = _assign()
    assert _code(env) == "current_query_mismatch"
    details = _obj(env)["error"]["details"]
    assert details["current_query"] == "device:campus-leaf9"
    assert details["expected_current_query"] == CURRENT_QUERY
    mocks["urlopen"].assert_not_called()


# --- assign: preview / confirm ----------------------------------------------


def test_assign_preview_makes_no_post_and_returns_token():
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign()
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["dry_run"] is True
    assert obj["error"] is None
    assert env["coverage"] == "full"
    assert obj["before_query"] == CURRENT_QUERY
    assert obj["after_query"] == NEW_QUERY
    assert obj["target_preview"] is None
    assert "target_preview_unresolved" in env["warnings"]
    assert obj["preview_token"] == _preview_token()
    mocks["urlopen"].assert_not_called()


def test_assign_confirm_without_token_refuses():
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(confirm=True)
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_assign_confirm_with_stale_token_refuses():
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(confirm=True, preview_token_value="deadbeef")
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_assign_confirm_with_token_posts_once():
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(confirm=True, preview_token_value=_preview_token())
    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["dry_run"] is False
    assert obj["error"] is None
    assert obj["resource_time"] == "2026-08-22T14:05:00Z"
    assert mocks["urlopen"].call_count == 1

    request = mocks["urlopen"].call_args[0][0]
    assert request.full_url == (
        "https://cvp.example.com/api/resources/studio/v1/AssignedTagsConfig"
    )
    assert _posted_body(mocks["urlopen"]) == {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "query": NEW_QUERY,
    }


def test_assign_preview_token_is_bound_to_the_new_query():
    """A token minted for one query must not confirm a different one."""
    with _mocked(tags=[_tag_row()]) as mocks:
        env = _assign(
            query="device:campus-leaf3",
            confirm=True,
            preview_token_value=_preview_token(),
        )
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()
