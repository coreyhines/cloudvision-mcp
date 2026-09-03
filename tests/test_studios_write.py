"""Tests for the Studios 2.0 write library (bucket 1b).

HTTP is mocked at the transport boundary: reads through the ``studios`` GET
helpers, writes through ``urllib.request.urlopen``. Every refusal asserts that
no mutating request was made.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studios_write
from cvp_mcp.write_access import WRITES_ENV, preview_token

STUDIO_ID = studios_write.ACCESS_INTERFACE_STUDIO_ID
WORKSPACE = "ws-mcp-test-20260822-abcd1234"
DEVICE = "JPE19151499"
INTERFACE = "Ethernet6"
LOCATOR = f"interface:{INTERFACE}@{DEVICE}"

DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    """Writes are enabled for every test unless a test turns them back off."""
    monkeypatch.setenv(WRITES_ENV, "1")


# --- fixtures / fakes -------------------------------------------------------


def _inputs_document(description="pi5 - dns", extra_rows=0):
    """Root Inputs tree shaped like the live capture (fixture row nested)."""
    rows = [
        {
            "inputs": {
                "adapterDetails": {
                    "description": description,
                    "enabled": "Yes",
                    "portChannel": {},
                    "portProfile": "vl 2",
                    "vlans": {"vlans": None},
                }
            },
            "tags": {"query": LOCATOR},
        }
    ]
    for _ in range(extra_rows):
        rows.append(json.loads(json.dumps(rows[0])))
    rows.append(
        {
            "inputs": {"adapterDetails": {"description": "720xp-48-ma1"}},
            "tags": {"query": f"interface:Ethernet19@{DEVICE}"},
        }
    )
    return {"campus": {"connectedEndpoints": rows}}


def _inputs_row(workspace_id, document):
    return {
        "key": {
            "studioId": STUDIO_ID,
            "workspaceId": workspace_id,
            "path": {},
        },
        "inputs": json.dumps(document),
    }


def _workspace_value(state="WORKSPACE_STATE_PENDING", responses=None):
    value = {
        "key": {"workspaceId": WORKSPACE},
        "displayName": "mcp test",
        "state": state,
        "lastModifiedAt": "2026-08-22T14:00:00Z",
    }
    if responses is not None:
        value["responses"] = responses
    return {"value": value, "time": "2026-08-22T14:00:00Z"}


@contextmanager
def _mocked(workspace=("missing",), inputs=None, post_status=None):
    """Mock workspace GET, Inputs/all GET and the mutating urlopen.

    ``workspace`` is ``("missing",)`` for HTTP 404, ``("error", code)`` for a
    failed preflight, or a wire value dict. ``inputs`` is a list of raw
    Inputs/all resource values (``None`` means the GET is never expected).
    """
    if workspace == ("missing",):
        ws_result = (None, "http_error:404")
    elif isinstance(workspace, tuple) and workspace and workspace[0] == "error":
        ws_result = (None, workspace[1])
    else:
        ws_result = (workspace, None)

    if inputs is None:
        nd_result = ([], None, [])
    elif isinstance(inputs, tuple) and inputs and inputs[0] == "error":
        nd_result = (None, inputs[1], [])
    else:
        nd_result = (list(inputs), None, [])

    studio_env = {
        "coverage": "full",
        "object": {
            "studio_id": STUDIO_ID,
            "immutable": None,
            "from_package": None,
        },
        "warnings": [],
    }
    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer", return_value=ws_result
        ) as get_ws,
        patch(
            "cvp_mcp.grpc.studios_write.get_cvp_studio",
            return_value=studio_env,
        ),
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=nd_result,
        ) as get_inputs,
        patch("urllib.request.urlopen") as urlopen,
    ):
        resp = urlopen.return_value.__enter__.return_value
        resp.read.return_value = json.dumps(
            post_status or {"value": {}, "time": "2026-08-22T14:05:00Z"}
        ).encode()
        yield {"workspace_get": get_ws, "inputs_get": get_inputs, "urlopen": urlopen}


def _obj(envelope):
    return envelope["object"]


def _code(envelope):
    return _obj(envelope)["error"]["code"]


def _posted_body(urlopen):
    request = urlopen.call_args[0][0]
    return json.loads(request.data.decode())


# --- writes_disabled --------------------------------------------------------


def test_all_writes_refused_when_writes_disabled(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    calls = [
        lambda: studios_write.create_cvp_workspace(DATADICT, WORKSPACE, "n"),
        lambda: studios_write.delete_cvp_workspace(DATADICT, WORKSPACE),
        lambda: studios_write.build_cvp_workspace(DATADICT, WORKSPACE),
        lambda: studios_write.set_cvp_access_interface_description(
            DATADICT, WORKSPACE, DEVICE, INTERFACE, "a", "b"
        ),
    ]
    for call in calls:
        with _mocked() as mocks:
            env = call()
        assert _obj(env)["outcome"] == "refused"
        assert _code(env) == "writes_disabled"
        assert env["coverage"] == "none"
        mocks["workspace_get"].assert_not_called()
        mocks["urlopen"].assert_not_called()


# --- workspace id validation ------------------------------------------------


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
def test_workspace_id_refusals_never_touch_http(workspace_id, code):
    with _mocked() as mocks:
        env = studios_write.create_cvp_workspace(DATADICT, workspace_id, "n")
    assert _code(env) == code
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_description_refuses_mainline_workspace():
    with _mocked(inputs=[_inputs_row("", _inputs_document())]) as mocks:
        env = studios_write.set_cvp_access_interface_description(
            DATADICT, "", DEVICE, INTERFACE, "pi5 - dns", "pi5 - dns v2"
        )
    assert _code(env) == "workspace_id_required"
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- create -----------------------------------------------------------------


def test_create_preview_makes_no_post_and_returns_token():
    with _mocked() as mocks:
        env = studios_write.create_cvp_workspace(
            DATADICT, WORKSPACE, "mcp test", "desc"
        )
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["dry_run"] is True
    assert obj["error"] is None
    assert env["coverage"] == "full"
    assert obj["preview_token"] == preview_token(
        "create_cvp_workspace",
        {
            "workspace_id": WORKSPACE,
            "display_name": "mcp test",
            "description": "desc",
        },
    )
    assert obj["request_body"] == {
        "key": {"workspaceId": WORKSPACE},
        "displayName": "mcp test",
        "description": "desc",
    }
    mocks["urlopen"].assert_not_called()


def test_create_confirm_without_token_refuses_preview_required():
    with _mocked() as mocks:
        env = studios_write.create_cvp_workspace(
            DATADICT, WORKSPACE, "mcp test", confirm=True
        )
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_create_confirm_with_stale_token_refuses():
    stale = preview_token(
        "create_cvp_workspace",
        {"workspace_id": WORKSPACE, "display_name": "other", "description": ""},
    )
    with _mocked() as mocks:
        env = studios_write.create_cvp_workspace(
            DATADICT,
            WORKSPACE,
            "mcp test",
            confirm=True,
            preview_token_value=stale,
        )
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_create_confirm_posts_workspace_config_once():
    with _mocked() as mocks:
        preview = studios_write.create_cvp_workspace(DATADICT, WORKSPACE, "mcp test")
        env = studios_write.create_cvp_workspace(
            DATADICT,
            WORKSPACE,
            "mcp test",
            confirm=True,
            preview_token_value=_obj(preview)["preview_token"],
        )
        assert mocks["urlopen"].call_count == 1
        body = _posted_body(mocks["urlopen"])
        request = mocks["urlopen"].call_args[0][0]
    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["dry_run"] is False
    assert obj["resource_time"] == "2026-08-22T14:05:00Z"
    assert request.method == "POST"
    assert request.full_url.endswith(studios_write.WORKSPACE_CONFIG_PATH)
    assert body == {
        "key": {"workspaceId": WORKSPACE},
        "displayName": "mcp test",
        "description": "",
    }


def test_create_refuses_when_workspace_exists():
    with _mocked(workspace=_workspace_value()) as mocks:
        env = studios_write.create_cvp_workspace(DATADICT, WORKSPACE, "mcp test")
    assert _code(env) == "workspace_id_exists"
    mocks["urlopen"].assert_not_called()


def test_create_refuses_when_preflight_get_fails():
    with _mocked(workspace=("error", "http_error:503")) as mocks:
        env = studios_write.create_cvp_workspace(DATADICT, WORKSPACE, "mcp test")
    assert _code(env) == "workspace_read_failed"
    mocks["urlopen"].assert_not_called()


def test_create_refuses_without_credentials():
    with _mocked() as mocks:
        env = studios_write.create_cvp_workspace(
            {"cvp": "cvp.example.com"}, WORKSPACE, "mcp test"
        )
    assert _code(env) == "preflight_failed"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- delete -----------------------------------------------------------------


def test_delete_refuses_when_workspace_missing():
    with _mocked() as mocks:
        env = studios_write.delete_cvp_workspace(DATADICT, WORKSPACE)
    assert _code(env) == "workspace_not_found"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("WORKSPACE_STATE_SUBMITTED", "workspace_not_pending"),
        ("WORKSPACE_STATE_ABANDONED", "workspace_not_pending"),
        ("WORKSPACE_STATE_CONFLICTS", "workspace_not_pending"),
        ("", "workspace_state_unknown"),
    ],
)
def test_delete_only_pending_workspaces(state, code):
    with _mocked(workspace=_workspace_value(state=state)) as mocks:
        env = studios_write.delete_cvp_workspace(DATADICT, WORKSPACE)
    assert _code(env) == code
    mocks["urlopen"].assert_not_called()


def test_delete_preview_makes_no_request():
    with _mocked(workspace=_workspace_value()) as mocks:
        env = studios_write.delete_cvp_workspace(DATADICT, WORKSPACE)
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["preview_token"] == preview_token(
        "delete_cvp_workspace", {"workspace_id": WORKSPACE}
    )
    mocks["urlopen"].assert_not_called()


def test_delete_confirm_sends_delete_with_encoded_key():
    with _mocked(workspace=_workspace_value()) as mocks:
        preview = studios_write.delete_cvp_workspace(DATADICT, WORKSPACE)
        env = studios_write.delete_cvp_workspace(
            DATADICT,
            WORKSPACE,
            confirm=True,
            preview_token_value=_obj(preview)["preview_token"],
        )
        assert mocks["urlopen"].call_count == 1
        request = mocks["urlopen"].call_args[0][0]
    assert _obj(env)["outcome"] == "accepted"
    assert request.method == "DELETE"
    assert request.full_url == (
        f"https://cvp.example.com{studios_write.WORKSPACE_CONFIG_PATH}"
        f"?key.workspaceId={WORKSPACE}"
    )


def test_delete_confirm_without_token_refuses():
    with _mocked(workspace=_workspace_value()) as mocks:
        env = studios_write.delete_cvp_workspace(DATADICT, WORKSPACE, confirm=True)
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


# --- build ------------------------------------------------------------------


def test_build_preview_generates_request_id_and_no_post():
    with _mocked(workspace=_workspace_value()) as mocks:
        env = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["request"] == "REQUEST_START_BUILD"
    assert len(obj["request_id"]) == 36
    assert obj["done"] is False
    mocks["urlopen"].assert_not_called()


def test_build_confirm_posts_start_build_with_supplied_request_id():
    with _mocked(workspace=_workspace_value()) as mocks:
        preview = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
        request_id = _obj(preview)["request_id"]
        env = studios_write.build_cvp_workspace(
            DATADICT,
            WORKSPACE,
            request_id=request_id,
            confirm=True,
            preview_token_value=_obj(preview)["preview_token"],
        )
        assert mocks["urlopen"].call_count == 1
        body = _posted_body(mocks["urlopen"])
    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["done"] is False
    assert obj["request_id"] == request_id
    assert body == {
        "key": {"workspaceId": WORKSPACE},
        "request": "REQUEST_START_BUILD",
        "requestParams": {"requestId": request_id},
    }


def test_build_blank_request_id_refused():
    with _mocked(workspace=_workspace_value()) as mocks:
        env = studios_write.build_cvp_workspace(DATADICT, WORKSPACE, request_id="  ")
    assert _code(env) == "invalid_request_id"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_build_refused_while_earlier_build_not_terminal():
    responses = {"values": {"req-1": {"status": "RESPONSE_STATUS_RUNNING"}}}
    with _mocked(workspace=_workspace_value(responses=responses)) as mocks:
        env = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
    assert _code(env) == "build_in_progress"
    assert _obj(env)["error"]["details"]["request_ids"] == ["req-1"]
    mocks["urlopen"].assert_not_called()


def test_build_refused_when_response_state_unknown():
    responses = {"values": {"req-1": {"note": "no state field here"}}}
    with _mocked(workspace=_workspace_value(responses=responses)) as mocks:
        env = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
    assert _code(env) == "build_in_progress"
    mocks["urlopen"].assert_not_called()


def test_build_allowed_after_terminal_response():
    responses = {"values": {"req-1": {"status": "BUILD_STATE_SUCCESS"}}}
    with _mocked(workspace=_workspace_value(responses=responses)) as mocks:
        env = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
    assert _obj(env)["outcome"] == "preview"
    mocks["urlopen"].assert_not_called()


def test_build_refuses_non_pending_workspace():
    with _mocked(workspace=_workspace_value(state="WORKSPACE_STATE_SUBMITTED")) as m:
        env = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
    assert _code(env) == "workspace_not_pending"
    m["urlopen"].assert_not_called()


def test_build_confirm_without_request_id_refuses():
    with _mocked(workspace=_workspace_value()) as mocks:
        preview = studios_write.build_cvp_workspace(DATADICT, WORKSPACE)
        env = studios_write.build_cvp_workspace(
            DATADICT,
            WORKSPACE,
            confirm=True,
            preview_token_value=_obj(preview)["preview_token"],
        )
    assert _code(env) == "invalid_request_id"
    mocks["urlopen"].assert_not_called()


# --- description CAS --------------------------------------------------------


def _set_description(
    inputs, expected="pi5 - dns", new="pi5 - dns v2", confirm=False, token=None
):
    with _mocked(workspace=_workspace_value(), inputs=inputs) as mocks:
        env = studios_write.set_cvp_access_interface_description(
            DATADICT,
            WORKSPACE,
            DEVICE,
            INTERFACE,
            expected,
            new,
            confirm=confirm,
            preview_token_value=token,
        )
        urlopen = mocks["urlopen"]
        body = _posted_body(urlopen) if urlopen.call_count else None
    return env, urlopen, body


def test_description_preview_reports_single_leaf_and_no_post():
    document = _inputs_document()
    env, urlopen, _ = _set_description([_inputs_row("", document)])
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["locator"] == LOCATOR
    assert obj["before_description"] == "pi5 - dns"
    assert obj["after_description"] == "pi5 - dns v2"
    assert obj["changed_leaves"] == 1
    assert obj["posted_at_root"] is True
    assert obj["disruptive"] is False
    assert obj["inputs_source_workspace_id"] == ""
    assert obj["preview_token"] == preview_token(
        "set_cvp_access_interface_description",
        {
            "workspace_id": WORKSPACE,
            "device_id": DEVICE,
            "interface": INTERFACE,
            "expected_current_description": "pi5 - dns",
            "new_description": "pi5 - dns v2",
        },
    )
    urlopen.assert_not_called()


def test_description_confirm_posts_full_tree_at_root_path():
    document = _inputs_document()
    rows = [_inputs_row("", document)]
    env, _, _ = _set_description(rows)
    token = _obj(env)["preview_token"]
    env, urlopen, body = _set_description(rows, confirm=True, token=token)

    assert _obj(env)["outcome"] == "accepted"
    assert urlopen.call_count == 1
    assert body["key"] == {
        "studioId": STUDIO_ID,
        "workspaceId": WORKSPACE,
        "path": {"values": []},
    }
    posted = json.loads(body["inputs"])
    row = posted["campus"]["connectedEndpoints"][0]
    assert row["inputs"]["adapterDetails"]["description"] == "pi5 - dns v2"
    # Siblings are copied unchanged.
    assert row["inputs"]["adapterDetails"]["portProfile"] == "vl 2"
    assert row["inputs"]["adapterDetails"]["enabled"] == "Yes"
    assert posted["campus"]["connectedEndpoints"][-1] == (
        document["campus"]["connectedEndpoints"][-1]
    )


def test_description_prefers_workspace_overlay_over_mainline():
    mainline = _inputs_document(description="stale mainline")
    overlay = _inputs_document(description="overlay current")
    rows = [_inputs_row("", mainline), _inputs_row(WORKSPACE, overlay)]
    env, urlopen, _ = _set_description(rows, expected="overlay current")
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["inputs_source_workspace_id"] == WORKSPACE
    urlopen.assert_not_called()


def test_description_ignores_other_studios_and_other_workspaces():
    other_studio = _inputs_row("", _inputs_document())
    other_studio["key"]["studioId"] = "studio-other"
    other_ws = _inputs_row("ws-mcp-someone-else", _inputs_document())
    env, urlopen, _ = _set_description([other_studio, other_ws])
    assert _code(env) == "inputs_path_unresolved"
    urlopen.assert_not_called()


def test_description_cas_mismatch_refuses_without_post():
    document = _inputs_document(description="pi5 - dns")
    env, urlopen, _ = _set_description(
        [_inputs_row("", document)], expected="something else"
    )
    assert _code(env) == "current_description_mismatch"
    details = _obj(env)["error"]["details"]
    assert details["current_description"] == "pi5 - dns"
    assert details["expected_current_description"] == "something else"
    urlopen.assert_not_called()


def test_description_null_current_matches_empty_expected():
    document = _inputs_document(description=None)
    env, urlopen, _ = _set_description(
        [_inputs_row("", document)], expected="", new="pi5 - dns"
    )
    assert _obj(env)["outcome"] == "preview"
    assert _obj(env)["before_description"] == ""
    urlopen.assert_not_called()


def test_description_locator_miss_refuses():
    document = _inputs_document()
    with _mocked(workspace=_workspace_value(), inputs=[_inputs_row("", document)]) as m:
        env = studios_write.set_cvp_access_interface_description(
            DATADICT, WORKSPACE, DEVICE, "Ethernet7", "x", "y"
        )
    assert _code(env) == "inputs_path_not_found"
    assert _obj(env)["error"]["details"]["matches"] == 0
    m["urlopen"].assert_not_called()


def test_description_duplicate_locator_refuses():
    document = _inputs_document(extra_rows=1)
    env, urlopen, _ = _set_description([_inputs_row("", document)])
    assert _code(env) == "inputs_path_not_found"
    assert _obj(env)["error"]["details"]["matches"] == 2
    urlopen.assert_not_called()


def test_description_missing_adapter_details_refuses():
    document = {"campus": {"connectedEndpoints": [{"tags": {"query": LOCATOR}}]}}
    env, urlopen, _ = _set_description([_inputs_row("", document)])
    assert _code(env) == "inputs_path_unresolved"
    urlopen.assert_not_called()


def test_description_extra_leaf_change_refuses_before_post(monkeypatch):
    """A copy that mutates a sibling must be caught by the tree diff."""
    document = _inputs_document()

    def _tampering_deepcopy(tree):
        clone = json.loads(json.dumps(tree))
        clone["campus"]["connectedEndpoints"][0]["inputs"]["adapterDetails"][
            "enabled"
        ] = "No"
        return clone

    monkeypatch.setattr(studios_write.copy, "deepcopy", _tampering_deepcopy)
    env, urlopen, _ = _set_description([_inputs_row("", document)])
    assert _code(env) == "tree_diff_not_description_only"
    details = _obj(env)["error"]["details"]
    assert details["changed_count"] == 2
    urlopen.assert_not_called()


@pytest.mark.parametrize(
    "new_description",
    ["shutdown", "no shutdown", "no interface Ethernet6", "reload", "write erase"],
)
def test_description_eos_lint_refuses(new_description):
    document = _inputs_document()
    env, urlopen, _ = _set_description([_inputs_row("", document)], new=new_description)
    assert _code(env) == "disruptive_content_forbidden"
    urlopen.assert_not_called()


def test_description_pre_existing_disruptive_text_elsewhere_is_not_refused():
    """Lint fires on introduced text, not on unrelated pre-existing content."""
    document = _inputs_document()
    document["campus"]["connectedEndpoints"][-1]["inputs"]["adapterDetails"][
        "description"
    ] = "reload me"
    env, urlopen, _ = _set_description([_inputs_row("", document)])
    assert _obj(env)["outcome"] == "preview"
    urlopen.assert_not_called()


def test_description_inputs_get_failure_refuses():
    env, urlopen, _ = _set_description(("error", "http_error:500"))
    assert _code(env) == "preflight_failed"
    urlopen.assert_not_called()


def test_description_refuses_non_pending_workspace():
    document = _inputs_document()
    with _mocked(
        workspace=_workspace_value(state="WORKSPACE_STATE_SUBMITTED"),
        inputs=[_inputs_row("", document)],
    ) as mocks:
        env = studios_write.set_cvp_access_interface_description(
            DATADICT, WORKSPACE, DEVICE, INTERFACE, "pi5 - dns", "pi5 - dns v2"
        )
    assert _code(env) == "workspace_not_pending"
    mocks["urlopen"].assert_not_called()


def test_description_refuses_immutable_studio():
    document = _inputs_document()
    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": True, "from_package": None},
        "warnings": [],
    }
    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer",
            return_value=(_workspace_value(), None),
        ),
        patch("cvp_mcp.grpc.studios_write.get_cvp_studio", return_value=studio_env),
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=([_inputs_row("", document)], None, []),
        ),
        patch("urllib.request.urlopen") as urlopen,
    ):
        env = studios_write.set_cvp_access_interface_description(
            DATADICT, WORKSPACE, DEVICE, INTERFACE, "pi5 - dns", "pi5 - dns v2"
        )
    assert _code(env) == "studio_immutable"
    urlopen.assert_not_called()


def test_description_refuses_packaged_studio():
    document = _inputs_document()
    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": None, "from_package": True},
        "warnings": [],
    }
    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer",
            return_value=(_workspace_value(), None),
        ),
        patch("cvp_mcp.grpc.studios_write.get_cvp_studio", return_value=studio_env),
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=([_inputs_row("", document)], None, []),
        ),
        patch("urllib.request.urlopen") as urlopen,
    ):
        env = studios_write.set_cvp_access_interface_description(
            DATADICT, WORKSPACE, DEVICE, INTERFACE, "pi5 - dns", "pi5 - dns v2"
        )
    assert _code(env) == "studio_from_package"
    urlopen.assert_not_called()


def test_description_truncated_inputs_stream_refuses():
    document = _inputs_document()
    rows = [_inputs_row("", document)]
    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": None, "from_package": None},
        "warnings": [],
    }
    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer",
            return_value=(_workspace_value(), None),
        ),
        patch("cvp_mcp.grpc.studios_write.get_cvp_studio", return_value=studio_env),
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=(rows, None, ["truncated_to_32000000_bytes"]),
        ),
        patch("urllib.request.urlopen") as urlopen,
    ):
        env = studios_write.set_cvp_access_interface_description(
            DATADICT, WORKSPACE, DEVICE, INTERFACE, "pi5 - dns", "pi5 - dns v2"
        )
    assert _code(env) == "preflight_failed"
    urlopen.assert_not_called()


def test_description_confirm_without_token_refuses():
    document = _inputs_document()
    env, urlopen, _ = _set_description([_inputs_row("", document)], confirm=True)
    assert _code(env) == "preview_required"
    urlopen.assert_not_called()


def test_description_confirm_with_token_for_other_value_refuses():
    document = _inputs_document()
    env, _, _ = _set_description([_inputs_row("", document)])
    stale = _obj(env)["preview_token"]
    env, urlopen, _ = _set_description(
        [_inputs_row("", document)], new="another label", confirm=True, token=stale
    )
    assert _code(env) == "preview_required"
    urlopen.assert_not_called()


def test_description_post_failure_reports_resource_write_failed():
    document = _inputs_document()
    rows = [_inputs_row("", document)]
    env, _, _ = _set_description(rows)
    token = _obj(env)["preview_token"]
    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": None, "from_package": None},
        "warnings": [],
    }
    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer",
            return_value=(_workspace_value(), None),
        ),
        patch("cvp_mcp.grpc.studios_write.get_cvp_studio", return_value=studio_env),
        patch(
            "cvp_mcp.grpc.studios_write.get_ndjson_all_values_with_bearer",
            return_value=(rows, None, []),
        ),
        patch(
            "cvp_mcp.grpc.studios_write.post_resource_config",
            return_value=(None, "http_error:403"),
        ),
    ):
        env = studios_write.set_cvp_access_interface_description(
            DATADICT,
            WORKSPACE,
            DEVICE,
            INTERFACE,
            "pi5 - dns",
            "pi5 - dns v2",
            confirm=True,
            preview_token_value=token,
        )
    assert _code(env) == "resource_write_failed"
    assert _obj(env)["error"]["details"]["reason"] == "http_error:403"


# --- helpers ----------------------------------------------------------------


def test_changed_leaf_paths_detects_added_and_removed_keys():
    before = {"a": {"b": 1}, "c": [1, 2]}
    after = {"a": {"b": 1, "d": 2}, "c": [1, 3]}
    assert studios_write._changed_leaf_paths(before, after) == ["$.a.d", "$.c[1]"]


def test_is_root_path_accepts_only_empty_paths():
    assert studios_write._is_root_path({"path": {}}) is True
    assert studios_write._is_root_path({}) is True
    assert studios_write._is_root_path({"path": {"values": []}}) is True
    assert studios_write._is_root_path({"path": {"values": ["campus"]}}) is False


# --- _load_root_inputs(studio_id=) (final spec bucket R) --------------------


def _other_studio_row(studio_id, workspace_id, document):
    return {
        "key": {"studioId": studio_id, "workspaceId": workspace_id, "path": {}},
        "inputs": json.dumps(document),
    }


def test_load_root_inputs_defaults_to_access_interface_studio():
    access = _inputs_document()
    mss = {"rules": [], "policies": []}
    rows = [_inputs_row("", access), _other_studio_row("studio-mss-service", "", mss)]
    with _mocked(inputs=rows):
        document, source, err, _ = studios_write._load_root_inputs(DATADICT, WORKSPACE)
    assert err is None
    assert source == ""
    assert document == access


def test_load_root_inputs_filters_by_studio_id():
    access = _inputs_document()
    mss = {"rules": [], "policies": []}
    rows = [_inputs_row("", access), _other_studio_row("studio-mss-service", "", mss)]
    with _mocked(inputs=rows):
        document, source, err, _ = studios_write._load_root_inputs(
            DATADICT, WORKSPACE, studio_id="studio-mss-service"
        )
    assert err is None
    assert source == ""
    assert document == mss


def test_load_root_inputs_missing_studio_is_unresolved():
    with _mocked(inputs=[_inputs_row("", _inputs_document())]):
        document, _, err, _ = studios_write._load_root_inputs(
            DATADICT, WORKSPACE, studio_id="studio-mss-service"
        )
    assert document is None
    assert err == "inputs_path_unresolved"
