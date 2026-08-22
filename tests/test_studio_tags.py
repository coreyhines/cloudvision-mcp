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
def _mocked(workspace=None, tags=None, post_status=None, tag_warnings=None):
    """Mock workspace GET, AssignedTags/all GET and the mutating urlopen.

    ``workspace`` defaults to a pending draft; pass ``("missing",)`` for HTTP
    404 or ``("error", code)`` for a failed GET. ``tags`` is a list of raw
    AssignedTags resource values, or ``("error", code)`` to fail the GET.
    ``tag_warnings`` are the warnings the NDJSON helper returns alongside a
    200 — ``truncated_to_*`` / ``ndjson_skip_invalid_line`` mark the stream
    incomplete.
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
        tag_result = (list(tags or []), None, list(tag_warnings or []))

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


def test_read_no_matching_row_is_an_empty_query():
    """Complete /all with rows for other studios: this studio is unassigned."""
    rows = [
        _tag_row(studio_id="studio-a", workspace_id="", query="device:a"),
        _tag_row(studio_id="studio-b", workspace_id="", query="device:b"),
    ]
    with _mocked(tags=rows) as mocks:
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "full"
    assert env["items"] == [
        {"studio_id": STUDIO_ID, "workspace_id": WORKSPACE, "query": ""}
    ]
    assert "assigned_tags_unavailable" not in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_read_mainline_no_row_is_an_empty_query():
    with _mocked(tags=[_tag_row(studio_id="studio-other", workspace_id="")]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID)
    assert env["coverage"] == "full"
    assert env["items"] == [{"studio_id": STUDIO_ID, "workspace_id": "", "query": ""}]
    assert "assigned_tags_unavailable" not in env["warnings"]


@pytest.mark.parametrize(
    "warning",
    ["truncated_to_96000000_bytes", "ndjson_skip_invalid_line:3"],
)
def test_read_incomplete_stream_never_invents_an_empty_query(warning):
    """A studio absent from a partial stream is unknown, not unassigned."""
    rows = [_tag_row(studio_id="studio-a", workspace_id="", query="device:a")]
    with _mocked(tags=rows, tag_warnings=[warning]) as mocks:
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert warning in env["warnings"]
    assert "assigned_tags_read_failed" in env["warnings"]
    assert "assigned_tags_unavailable" not in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_read_stream_with_no_rows_at_all_is_read_failed():
    """A 200 that parsed zero AssignedTags rows proves nothing about C."""
    with _mocked(tags=[]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "assigned_tags_read_failed" in env["warnings"]
    assert "assigned_tags_unavailable" not in env["warnings"]


def test_read_matching_row_without_a_query_field_is_read_failed():
    row = {"key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE}}
    with _mocked(tags=[row]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "assigned_tags_read_failed" in env["warnings"]


def test_read_accepts_the_tag_query_field_alias():
    row = {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "tagQuery": CURRENT_QUERY,
    }
    with _mocked(tags=[row]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "full"
    assert env["items"][0]["query"] == CURRENT_QUERY


def test_read_duplicate_rows_are_ambiguous():
    rows = [_tag_row(), _tag_row(query="device:dup")]
    with _mocked(tags=rows):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "none"
    assert env["items"] == []
    assert "assigned_tags_ambiguous" in env["warnings"]


def test_read_draft_inherits_the_mainline_row():
    rows = [_tag_row(workspace_id="", query="device:mainline")]
    with _mocked(tags=rows):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
    assert env["coverage"] == "full"
    assert env["items"] == [
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "query": "device:mainline",
        }
    ]


def test_read_never_copies_another_workspaces_query():
    """A UUID workspace's row belongs to neither mainline nor this draft."""
    rows = [
        _tag_row(
            workspace_id="8f1d0c1e-0000-4a00-9c00-000000000001",
            query="device:someone-else",
        )
    ]
    with _mocked(tags=rows):
        draft = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID, WORKSPACE)
        mainline = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID)
    assert draft["items"] == [
        {"studio_id": STUDIO_ID, "workspace_id": WORKSPACE, "query": ""}
    ]
    assert mainline["items"] == [
        {"studio_id": STUDIO_ID, "workspace_id": "", "query": ""}
    ]


def test_read_mainline_ignores_a_draft_row():
    """workspace_id=None resolves mainline only; it must not read the draft."""
    with _mocked(tags=[_tag_row(query="device:draft-only")]):
        env = studio_tags.get_cvp_studio_assigned_tags(DATADICT, STUDIO_ID)
    assert env["items"] == [{"studio_id": STUDIO_ID, "workspace_id": "", "query": ""}]


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


@pytest.mark.parametrize("expected", [None, 0, [], {}])
def test_assign_requires_expected_current_query(expected):
    """Missing or non-str is refused before any HTTP; "" is a valid value."""
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


def test_assign_first_assignment_previews_then_posts_once():
    """No row anywhere + expected "" is the first-assignment CAS."""
    rows = [_tag_row(studio_id="studio-other", workspace_id="", query="device:a")]
    token = preview_token(
        "assign_cvp_studio_tags",
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "query": NEW_QUERY,
            "expected_current_query": "",
        },
    )
    with _mocked(tags=rows) as mocks:
        preview = _assign(expected_current_query="")
        obj = _obj(preview)
        assert obj["outcome"] == "preview"
        assert obj["before_query"] == ""
        assert obj["preview_token"] == token
        mocks["urlopen"].assert_not_called()

        env = _assign(
            expected_current_query="", confirm=True, preview_token_value=token
        )
    assert _obj(env)["outcome"] == "accepted"
    assert mocks["urlopen"].call_count == 1
    assert _posted_body(mocks["urlopen"]) == {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "query": NEW_QUERY,
    }


def test_assign_preview_token_is_bound_to_an_empty_expected():
    """A token minted for expected "" must not confirm a non-empty expected."""
    rows = [_tag_row(studio_id="studio-other", workspace_id="", query="device:a")]
    with _mocked(tags=rows) as mocks:
        env = _assign(
            expected_current_query="",
            confirm=True,
            preview_token_value=_preview_token(),
        )
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_non_empty_expected_when_unassigned():
    rows = [_tag_row(studio_id="studio-other", workspace_id="", query="device:a")]
    with _mocked(tags=rows) as mocks:
        env = _assign()
    assert _code(env) == "current_query_mismatch"
    details = _obj(env)["error"]["details"]
    assert details["current_query"] == ""
    assert details["expected_current_query"] == CURRENT_QUERY
    mocks["urlopen"].assert_not_called()


def test_assign_empty_expected_is_refused_when_mainline_has_a_query():
    """The draft inherits mainline, so "" is not the current query."""
    rows = [_tag_row(workspace_id="", query="device:Y")]
    with _mocked(tags=rows) as mocks:
        env = _assign(expected_current_query="")
    assert _code(env) == "current_query_mismatch"
    assert _obj(env)["error"]["details"]["current_query"] == "device:Y"
    mocks["urlopen"].assert_not_called()


def test_assign_inherits_mainline_and_posts_to_the_draft():
    """Overlay-then-mainline CAS; the POST key is the draft, never ""."""
    rows = [_tag_row(workspace_id="", query="device:Y")]
    token = preview_token(
        "assign_cvp_studio_tags",
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "query": NEW_QUERY,
            "expected_current_query": "device:Y",
        },
    )
    with _mocked(tags=rows) as mocks:
        preview = _assign(expected_current_query="device:Y")
        assert _obj(preview)["outcome"] == "preview"
        assert _obj(preview)["before_query"] == "device:Y"
        assert _obj(preview)["preview_token"] == token

        env = _assign(
            expected_current_query="device:Y",
            confirm=True,
            preview_token_value=token,
        )
    assert _obj(env)["outcome"] == "accepted"
    assert mocks["urlopen"].call_count == 1
    assert _posted_body(mocks["urlopen"])["key"] == {
        "studioId": STUDIO_ID,
        "workspaceId": WORKSPACE,
    }


def test_assign_prefers_the_draft_row_over_mainline():
    rows = [_tag_row(), _tag_row(workspace_id="", query="device:mainline")]
    with _mocked(tags=rows) as mocks:
        env = _assign()
    assert _obj(env)["outcome"] == "preview"
    assert _obj(env)["before_query"] == CURRENT_QUERY
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    "warning",
    ["truncated_to_96000000_bytes", "ndjson_skip_invalid_line:3"],
)
def test_assign_refuses_an_incomplete_stream(warning):
    rows = [_tag_row(studio_id="studio-other", workspace_id="", query="device:a")]
    with _mocked(tags=rows, tag_warnings=[warning]) as mocks:
        env = _assign(expected_current_query="")
    assert _code(env) == "assigned_tags_read_failed"
    assert warning in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_assign_refuses_a_row_without_a_query_field():
    row = {"key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE}}
    with _mocked(tags=[row]) as mocks:
        env = _assign(expected_current_query="")
    assert _code(env) == "assigned_tags_read_failed"
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
