"""Tests for the Studios 2.1 generic Inputs write library (bucket 1b).

HTTP is mocked at the boundary: the workspace GET through
``studios.get_json_with_bearer``, the studio and Inputs reads through the
``studios`` helpers, and the mutating write through ``urllib.request.urlopen``.
Every refusal asserts that no mutating request was made.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_inputs_generic as generic
from cvp_mcp.write_access import WRITES_ENV, preview_token

STUDIO_ID = "studio-campus-access-interfaces"
WORKSPACE = "ws-mcp-test-20260822-abcd1234"
PATH = ["campus", "connectedEndpoints", "endpoint-1"]

DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    """Writes are enabled for every test unless a test turns them back off."""
    monkeypatch.setenv(WRITES_ENV, "1")


# --- fixtures / fakes -------------------------------------------------------


def _document(description="pi5 - dns", enabled="Yes"):
    """Subtree shaped like a live access-interface row at a scoped path."""
    return {
        "adapterDetails": {
            "description": description,
            "enabled": enabled,
            "portProfile": "vl 2",
            "vlans": {"vlans": None},
        },
        "tags": {"query": "interface:Ethernet6@JPE19151499"},
    }


def _inputs_item(workspace_id, document, path_values=None):
    return {
        "studio_id": STUDIO_ID,
        "workspace_id": workspace_id,
        "path_values": list(PATH if path_values is None else path_values),
        "inputs": document,
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


def _inputs_env(items, warnings=None):
    return {
        "coverage": "full" if items else "none",
        "items": list(items),
        "warnings": list(warnings or []),
    }


@contextmanager
def _mocked(workspace=None, inputs=None, studio=None):
    """Mock the workspace GET, studio GET, Inputs read and mutating urlopen.

    ``workspace`` defaults to a pending draft; ``("missing",)`` is HTTP 404 and
    ``("error", code)`` a failed GET. ``inputs`` is either a list of envelopes
    returned in call order (workspace overlay first, mainline second) or a
    single envelope reused for every call.
    """
    if workspace is None:
        ws_result = (_workspace_value(), None)
    elif workspace == ("missing",):
        ws_result = (None, "http_error:404")
    elif isinstance(workspace, tuple) and workspace[0] == "error":
        ws_result = (None, workspace[1])
    else:
        ws_result = (workspace, None)

    if inputs is None:
        inputs_kwargs = {"return_value": _inputs_env([])}
    elif isinstance(inputs, list):
        inputs_kwargs = {"side_effect": inputs}
    else:
        inputs_kwargs = {"return_value": inputs}

    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": None, "from_package": None},
        "warnings": [],
    }
    if studio is None:
        studio_kwargs = {"return_value": studio_env}
    elif isinstance(studio, list):
        studio_kwargs = {"side_effect": studio}
    else:
        studio_kwargs = {"return_value": studio}

    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer", return_value=ws_result
        ) as get_ws,
        patch(
            "cvp_mcp.grpc.studio_crud.get_cvp_studio",
            **studio_kwargs,
        ) as get_studio,
        patch(
            "cvp_mcp.grpc.studio_inputs_generic.get_cvp_studio_inputs",
            **inputs_kwargs,
        ) as get_inputs,
        patch("urllib.request.urlopen") as urlopen,
    ):
        resp = urlopen.return_value.__enter__.return_value
        resp.read.return_value = json.dumps(
            {"value": {}, "time": "2026-08-22T14:05:00Z"}
        ).encode()
        yield {
            "workspace_get": get_ws,
            "studio_get": get_studio,
            "inputs_get": get_inputs,
            "urlopen": urlopen,
        }


def _obj(envelope):
    return envelope["object"]


def _code(envelope):
    return _obj(envelope)["error"]["code"]


def _posted_body(urlopen):
    request = urlopen.call_args[0][0]
    return json.loads(request.data.decode())


def _call(**overrides):
    kwargs = {
        "studio_id": STUDIO_ID,
        "workspace_id": WORKSPACE,
        "path_values": list(PATH),
        "inputs": _document(description="pi5 - dns v2"),
    }
    kwargs.update(overrides)
    return generic.set_cvp_studio_inputs(DATADICT, **kwargs)


# --- writes gate ------------------------------------------------------------


def test_writes_disabled_refuses_before_any_get(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    with _mocked(inputs=_inputs_env([_inputs_item(WORKSPACE, _document())])) as mocks:
        env = _call()
    assert _obj(env)["outcome"] == "refused"
    assert _code(env) == "writes_disabled"
    assert env["coverage"] == "none"
    mocks["workspace_get"].assert_not_called()
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- root path --------------------------------------------------------------


@pytest.mark.parametrize("path_values", [[], (), None])
def test_empty_path_values_refuses_root_path_forbidden(path_values):
    with _mocked(inputs=_inputs_env([_inputs_item(WORKSPACE, _document())])) as mocks:
        env = _call(path_values=path_values)
    assert _code(env) == "root_path_forbidden"
    assert "set_cvp_access_interface_description" in _obj(env)["error"]["message"]
    assert env["coverage"] == "none"
    mocks["workspace_get"].assert_not_called()
    mocks["studio_get"].assert_not_called()
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("path_values", ["campus", ["campus", ""], ["campus", 3]])
def test_malformed_path_values_refuse_without_http(path_values):
    with _mocked() as mocks:
        env = _call(path_values=path_values)
    assert _code(env) == "inputs_path_unresolved"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- id / argument validation -----------------------------------------------


@pytest.mark.parametrize(
    ("workspace_id", "code"),
    [
        ("", "workspace_id_required"),
        ("builtin-studios-v1", "builtin_workspace_forbidden"),
        ("ws-other-1", "invalid_workspace_id"),
    ],
)
def test_workspace_id_refusals_never_touch_http(workspace_id, code):
    with _mocked() as mocks:
        env = _call(workspace_id=workspace_id)
    assert _code(env) == code
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_blank_studio_id_refuses():
    with _mocked() as mocks:
        env = _call(studio_id="  ")
    assert _code(env) == "studio_not_found"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_non_document_inputs_refuses():
    with _mocked() as mocks:
        env = _call(inputs="just a string")
    assert _code(env) == "inputs_path_unresolved"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- leaf allowlist ---------------------------------------------------------


def test_changing_enabled_refuses_input_key_not_allowed():
    current = _inputs_env([_inputs_item(WORKSPACE, _document(enabled="Yes"))])
    with _mocked(inputs=current) as mocks:
        env = _call(inputs=_document(enabled="No"))
    assert _code(env) == "input_key_not_allowed"
    assert env["coverage"] == "none"
    details = _obj(env)["error"]["details"]
    assert details["changed_count"] == 1
    assert details["not_allowed"] == ["$.adapterDetails.enabled"]
    assert details["forbidden"] == [
        {"path": "$.adapterDetails.enabled", "matched": ["enabled"]}
    ]
    mocks["urlopen"].assert_not_called()


def test_enabled_false_boolean_refuses_input_key_not_allowed():
    current = _inputs_env(
        [_inputs_item(WORKSPACE, {"adapterDetails": {"enabled": True}})]
    )
    with _mocked(inputs=current) as mocks:
        env = _call(inputs={"adapterDetails": {"enabled": False}})
    assert _code(env) == "input_key_not_allowed"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("key", "before", "after"),
    [
        ("portProfile", "vl 2", "vl 999"),
        ("vlans", "2", "999"),
        ("poe_enabled", "on", "off"),
        ("Mode", "access", "trunk"),
        ("shutdown", "false", "true"),
        ("disabled", "no", "yes"),
    ],
)
def test_admin_meaning_keys_are_never_writable(key, before, after):
    current = _inputs_env([_inputs_item(WORKSPACE, {"adapterDetails": {key: before}})])
    with _mocked(inputs=current) as mocks:
        env = _call(inputs={"adapterDetails": {key: after}})
    assert _code(env) == "input_key_not_allowed"
    mocks["urlopen"].assert_not_called()


def test_allowed_input_keys_may_not_widen_onto_an_admin_key():
    current = _inputs_env([_inputs_item(WORKSPACE, _document())])
    with _mocked(inputs=current) as mocks:
        env = _call(
            inputs=_document(enabled="No"),
            allowed_input_keys=["description", "enabled"],
        )
    assert _code(env) == "input_key_not_allowed"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_description_nested_under_a_vlan_key_is_refused():
    current = _inputs_env([_inputs_item(WORKSPACE, {"vlans": {"description": "old"}})])
    with _mocked(inputs=current) as mocks:
        env = _call(inputs={"vlans": {"description": "new"}})
    assert _code(env) == "input_key_not_allowed"
    mocks["urlopen"].assert_not_called()


def test_added_leaf_outside_allowlist_refuses():
    current = _inputs_env([_inputs_item(WORKSPACE, {"adapterDetails": {}})])
    with _mocked(inputs=current) as mocks:
        env = _call(inputs={"adapterDetails": {"speed": "10g"}})
    assert _code(env) == "input_key_not_allowed"
    assert _obj(env)["error"]["details"]["not_allowed"] == ["$.adapterDetails.speed"]
    mocks["urlopen"].assert_not_called()


def test_caller_may_narrow_the_allowlist():
    current = _inputs_env([_inputs_item(WORKSPACE, {"adapterDetails": {"note": "a"}})])
    with _mocked(inputs=current) as mocks:
        env = _call(
            inputs={"adapterDetails": {"note": "b"}}, allowed_input_keys=["note"]
        )
    assert _obj(env)["outcome"] == "preview"
    assert _obj(env)["allowed_input_keys"] == ["note"]
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("allowed", [[], "description", [""], [None]])
def test_malformed_allowed_input_keys_refuse(allowed):
    with _mocked() as mocks:
        env = _call(allowed_input_keys=allowed)
    assert _code(env) == "input_key_not_allowed"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- description-only happy path --------------------------------------------


def test_description_only_change_previews_then_confirms_post():
    proposed = _document(description="pi5 - dns v2")
    current = _inputs_env([_inputs_item(WORKSPACE, _document())])

    with _mocked(inputs=current) as mocks:
        env = _call(inputs=proposed)
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["dry_run"] is True
    assert obj["error"] is None
    assert env["coverage"] == "full"
    assert obj["changed_leaves"] == 1
    assert obj["changed_leaf_paths"] == ["$.adapterDetails.description"]
    assert obj["posted_at_root"] is False
    assert obj["path_values"] == PATH
    assert obj["inputs_source_workspace_id"] == WORKSPACE
    assert obj["allowed_input_keys"] == ["description"]
    mocks["urlopen"].assert_not_called()

    token = obj["preview_token"]
    assert token == preview_token(
        "set_cvp_studio_inputs",
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "path_values": PATH,
            "inputs": proposed,
            "allowed_input_keys": ["description"],
        },
    )

    with _mocked(inputs=current) as mocks:
        env = _call(inputs=proposed, confirm=True, preview_token_value=token)
    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["dry_run"] is False
    assert obj["error"] is None
    assert obj["resource_time"] == "2026-08-22T14:05:00Z"
    assert mocks["urlopen"].call_count == 1

    request = mocks["urlopen"].call_args[0][0]
    assert request.full_url == (
        "https://cvp.example.com/api/resources/studio/v1/InputsConfig"
    )
    body = _posted_body(mocks["urlopen"])
    assert body["key"] == {
        "studioId": STUDIO_ID,
        "workspaceId": WORKSPACE,
        "path": {"values": PATH},
    }
    assert json.loads(body["inputs"]) == proposed


def test_confirm_without_token_refuses_preview_required():
    current = _inputs_env([_inputs_item(WORKSPACE, _document())])
    with _mocked(inputs=current) as mocks:
        env = _call(confirm=True)
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_confirm_with_token_for_other_inputs_refuses():
    stale = preview_token(
        "set_cvp_studio_inputs",
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "path_values": PATH,
            "inputs": _document(description="something else"),
            "allowed_input_keys": ["description"],
        },
    )
    current = _inputs_env([_inputs_item(WORKSPACE, _document())])
    with _mocked(inputs=current) as mocks:
        env = _call(confirm=True, preview_token_value=stale)
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_unchanged_inputs_preview_warns_and_counts_zero_leaves():
    document = _document()
    current = _inputs_env([_inputs_item(WORKSPACE, document)])
    with _mocked(inputs=current) as mocks:
        env = _call(inputs=json.loads(json.dumps(document)))
    assert _obj(env)["outcome"] == "preview"
    assert _obj(env)["changed_leaves"] == 0
    assert "inputs_unchanged" in env["warnings"]
    mocks["urlopen"].assert_not_called()


# --- current-document read --------------------------------------------------


def test_falls_back_to_mainline_when_workspace_has_no_overlay_row():
    calls = [
        _inputs_env([]),
        _inputs_env([_inputs_item("", _document())]),
    ]
    with _mocked(inputs=calls) as mocks:
        env = _call()
    assert _obj(env)["outcome"] == "preview"
    assert _obj(env)["inputs_source_workspace_id"] == ""
    assert [c.args[2] for c in mocks["inputs_get"].call_args_list] == [WORKSPACE, ""]


def test_row_at_a_different_path_is_not_used():
    other = _inputs_env(
        [_inputs_item(WORKSPACE, _document(), path_values=["campus", "other"])]
    )
    with _mocked(inputs=[other, other]) as mocks:
        env = _call()
    assert _code(env) == "inputs_path_not_found"
    assert _obj(env)["error"]["details"]["available_path_values"] == [
        ["campus", "other"]
    ]
    assert mocks["inputs_get"].call_count == 1
    mocks["urlopen"].assert_not_called()


def test_miss_reports_resource_root_path_and_description_cas_hint():
    root_document = {"campus": {"connectedEndpoints": {"endpoint-1": _document()}}}
    root = _inputs_env([_inputs_item(WORKSPACE, root_document, path_values=[])])
    with _mocked(inputs=root) as mocks:
        env = _call()

    assert _code(env) == "inputs_path_not_found"
    details = _obj(env)["error"]["details"]
    assert details == {
        "studio_id": STUDIO_ID,
        "path_values": PATH,
        "available_path_values": [[]],
        "hint": (
            "Use set_cvp_access_interface_description for this studio’s only "
            "Resource row (path_values []). Generic Inputs cannot POST the root."
        ),
    }
    assert "inputs" not in details
    assert mocks["inputs_get"].call_count == 1
    mocks["urlopen"].assert_not_called()


def test_available_resource_paths_are_unique_and_capped():
    items = [
        _inputs_item(WORKSPACE, {}, path_values=[f"path-{index}"])
        for index in range(11)
    ]
    items.append(_inputs_item(WORKSPACE, {"duplicate": True}, path_values=["path-0"]))
    with _mocked(inputs=_inputs_env(items)) as mocks:
        env = _call(path_values=["missing"])

    details = _obj(env)["error"]["details"]
    assert details["available_path_values"] == [
        [f"path-{index}"] for index in range(10)
    ]
    assert "available_path_values_truncated_to_10" in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_duplicate_rows_at_the_same_path_refuse():
    dupes = _inputs_env(
        [
            _inputs_item(WORKSPACE, _document()),
            _inputs_item(WORKSPACE, _document(description="other")),
        ]
    )
    with _mocked(inputs=dupes) as mocks:
        env = _call()
    assert _code(env) == "inputs_path_not_found"
    mocks["urlopen"].assert_not_called()


def test_any_inputs_read_warning_fails_closed():
    with _mocked(
        inputs=_inputs_env(
            [_inputs_item(WORKSPACE, _document())], warnings=["truncated_to_96000000"]
        )
    ) as mocks:
        env = _call()
    assert _code(env) == "preflight_failed"
    assert "truncated_to_96000000" in env["warnings"]
    mocks["urlopen"].assert_not_called()


def test_skipped_invalid_inputs_line_fails_closed():
    with _mocked(
        inputs=_inputs_env([], warnings=["ndjson_skip_invalid_line:2"])
    ) as mocks:
        env = _call()
    assert _code(env) == "preflight_failed"
    assert "ndjson_skip_invalid_line:2" in env["warnings"]
    assert mocks["inputs_get"].call_count == 1
    mocks["urlopen"].assert_not_called()


def test_non_document_current_row_refuses():
    with _mocked(inputs=_inputs_env([_inputs_item(WORKSPACE, "not-json")])) as mocks:
        env = _call()
    assert _code(env) == "inputs_path_unresolved"
    mocks["urlopen"].assert_not_called()


# --- workspace / studio preflight -------------------------------------------


def test_missing_workspace_refuses_before_inputs_read():
    with _mocked(workspace=("missing",)) as mocks:
        env = _call()
    assert _code(env) == "workspace_not_found"
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_failed_workspace_get_refuses():
    with _mocked(workspace=("error", "http_error:500")) as mocks:
        env = _call()
    assert _code(env) == "workspace_read_failed"
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_submitted_workspace_refuses():
    with _mocked(
        workspace=_workspace_value(state="WORKSPACE_STATE_SUBMITTED")
    ) as mocks:
        env = _call()
    assert _code(env) == "workspace_not_pending"
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_unknown_workspace_state_refuses():
    with _mocked(workspace=_workspace_value(state="")) as mocks:
        env = _call()
    assert _code(env) == "workspace_state_unknown"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("flag", "code"),
    [("immutable", "studio_immutable"), ("from_package", "studio_from_package")],
)
def test_immutable_or_packaged_studio_refuses(flag, code):
    studio_env = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, flag: True},
        "warnings": [],
    }
    with _mocked(studio=studio_env) as mocks:
        env = _call()
    assert _code(env) == code
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_failed_studio_get_refuses():
    studio_env = {"coverage": "none", "object": {}, "warnings": ["http_error:404"]}
    with _mocked(studio=studio_env) as mocks:
        env = _call()
    assert _code(env) == "preflight_failed"
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_overlay_studio_get_is_used_without_mainline_fallback():
    with _mocked(inputs=_inputs_env([_inputs_item(WORKSPACE, _document())])) as mocks:
        env = _call()

    assert _obj(env)["outcome"] == "preview"
    assert [call.args[2] for call in mocks["studio_get"].call_args_list] == [WORKSPACE]


def test_overlay_studio_404_falls_back_to_mainline():
    overlay_missing = {
        "coverage": "none",
        "object": {},
        "warnings": ["http_error:404"],
    }
    mainline = {
        "coverage": "full",
        "object": {"studio_id": STUDIO_ID, "immutable": None, "from_package": None},
        "warnings": [],
    }
    with _mocked(
        studio=[overlay_missing, mainline],
        inputs=_inputs_env([_inputs_item(WORKSPACE, _document())]),
    ) as mocks:
        env = _call()

    assert _obj(env)["outcome"] == "preview"
    assert [call.args[2] for call in mocks["studio_get"].call_args_list] == [
        WORKSPACE,
        "",
    ]


def test_overlay_studio_read_failure_does_not_fall_back():
    overlay_failed = {
        "coverage": "none",
        "object": {},
        "warnings": ["http_error:500"],
    }
    with _mocked(studio=overlay_failed) as mocks:
        env = _call()

    assert _code(env) == "preflight_failed"
    assert [call.args[2] for call in mocks["studio_get"].call_args_list] == [WORKSPACE]
    mocks["inputs_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_missing_credentials_refuse_before_any_get():
    with _mocked() as mocks:
        env = generic.set_cvp_studio_inputs(
            {"cvp": "cvp.example.com"},
            STUDIO_ID,
            WORKSPACE,
            list(PATH),
            _document(),
        )
    assert _code(env) == "preflight_failed"
    mocks["workspace_get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- EOS lint / write failure -----------------------------------------------


def test_description_introducing_disruptive_text_refuses():
    current = _inputs_env([_inputs_item(WORKSPACE, _document())])
    with _mocked(inputs=current) as mocks:
        env = _call(inputs=_document(description="port shutdown pending"))
    assert _code(env) == "disruptive_content_forbidden"
    assert _obj(env)["error"]["details"]["matched"] == ["shutdown"]
    mocks["urlopen"].assert_not_called()


def test_pre_existing_disruptive_text_elsewhere_is_not_refused():
    document = _document(description="pi5 - dns")
    document["tags"]["query"] = "interface:Ethernet6@JPE19151499 shutdown"
    proposed = json.loads(json.dumps(document))
    proposed["adapterDetails"]["description"] = "pi5 - dns v2"
    with _mocked(inputs=_inputs_env([_inputs_item(WORKSPACE, document)])) as mocks:
        env = _call(inputs=proposed)
    assert _obj(env)["outcome"] == "preview"
    mocks["urlopen"].assert_not_called()


def test_post_failure_reports_resource_write_failed():
    proposed = _document(description="pi5 - dns v2")
    token = preview_token(
        "set_cvp_studio_inputs",
        {
            "studio_id": STUDIO_ID,
            "workspace_id": WORKSPACE,
            "path_values": PATH,
            "inputs": proposed,
            "allowed_input_keys": ["description"],
        },
    )
    current = _inputs_env([_inputs_item(WORKSPACE, _document())])
    with _mocked(inputs=current) as mocks:
        mocks["urlopen"].side_effect = OSError("boom")
        env = _call(inputs=proposed, confirm=True, preview_token_value=token)
    assert _code(env) == "resource_write_failed"


# --- helper units -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "segments"),
    [
        ("$.adapterDetails.description", ["adapterDetails", "description"]),
        ("$.rows[2].vlans[0]", ["rows", "vlans"]),
        ("$", []),
    ],
)
def test_path_segments_strips_indices_and_root(path, segments):
    assert generic._path_segments(path) == segments


@pytest.mark.parametrize(
    ("name", "tokens"),
    [
        ("description", []),
        ("portProfile", ["profile"]),
        ("poe_enabled", ["enabled", "poe"]),
        ("VLANs", ["vlan"]),
    ],
)
def test_forbidden_tokens_normalizes_case_and_separators(name, tokens):
    assert sorted(generic._forbidden_tokens(name)) == sorted(tokens)
