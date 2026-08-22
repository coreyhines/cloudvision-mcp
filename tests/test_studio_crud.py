"""Tests for the Studios 2.2 studio CRUD library (bucket 1d).

HTTP is mocked at the transport boundary: preflight reads through
``cvp_mcp.grpc.studios.get_json_with_bearer`` (so the real envelope parsing
runs) and the mutating POST through ``urllib.request.urlopen``. Every refusal
asserts that no mutating request was made; the lint refusals assert that no
request was made at all.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from cvp_mcp.grpc import resource_write, studio_crud
from cvp_mcp.write_access import WRITES_ENV, preview_token

WORKSPACE = "ws-mcp-test-20260822-abcd1234"
STUDIO_ID = "studio-mcp-campus-uplinks"
DISPLAY_NAME = "mcp campus uplinks"
TEMPLATE = "! rendered by mcp\ninterface Ethernet1\n   description mcp uplink\n"

DATADICT = {"cvtoken": "container-token", "cvp": "cvp.example.com", "cert": None}

MISSING = ("missing",)


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    """Writes are enabled for every test unless a test turns them back off."""
    monkeypatch.setenv(WRITES_ENV, "1")


# --- fixtures / fakes -------------------------------------------------------


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


def _studio_value(
    workspace_id="",
    immutable=None,
    from_package=None,
    in_use=None,
    template="! existing\n",
):
    value = {
        "key": {"studioId": STUDIO_ID, "workspaceId": workspace_id},
        "displayName": "existing studio",
        "description": "already here",
        "template": {"type": "TEMPLATE_TYPE_MAKO", "body": template},
    }
    if immutable is not None:
        value["immutable"] = immutable
    if from_package is not None:
        value["fromPackage"] = from_package
    if in_use is not None:
        value["inUse"] = in_use
    return {"value": value, "time": "2026-08-22T14:00:00Z"}


def _result(spec):
    """Translate a fixture spec into a ``get_json_with_bearer`` return value."""
    if spec == MISSING:
        return None, "http_error:404"
    if isinstance(spec, tuple) and spec and spec[0] == "error":
        return None, spec[1]
    return spec, None


@contextmanager
def _mocked(
    workspace=None,
    studio_overlay=MISSING,
    studio_mainline=MISSING,
    post_response=None,
    post_error=None,
):
    """Mock the keyed workspace/studio GETs and the mutating urlopen.

    Each of ``workspace`` / ``studio_overlay`` / ``studio_mainline`` is
    ``MISSING`` for HTTP 404, ``("error", code)`` for a failed GET, or a wire
    resource message. The studio GET is dispatched on ``key.workspaceId`` so
    overlay and mainline can be fixtured independently.
    """
    ws_result = _result(_workspace_value() if workspace is None else workspace)
    overlay_result = _result(studio_overlay)
    mainline_result = _result(studio_mainline)

    def _dispatch(uri, _token, **_kwargs):
        if "/workspace/v1/Workspace?" in uri:
            return ws_result
        if "/studio/v1/Studio?" in uri:
            query = parse_qs(urlparse(uri).query)
            requested = query.get("key.workspaceId", [""])[0]
            return overlay_result if requested else mainline_result
        raise AssertionError(f"unexpected GET {uri}")

    with (
        patch(
            "cvp_mcp.grpc.studios.get_json_with_bearer", side_effect=_dispatch
        ) as get_json,
        patch("urllib.request.urlopen") as urlopen,
    ):
        if post_error:
            urlopen.side_effect = post_error
        else:
            resp = urlopen.return_value.__enter__.return_value
            resp.read.return_value = json.dumps(
                post_response or {"value": {}, "time": "2026-08-22T14:05:00Z"}
            ).encode()
        yield {"get": get_json, "urlopen": urlopen}


def _obj(envelope):
    return envelope["object"]


def _code(envelope):
    return _obj(envelope)["error"]["code"]


def _details(envelope):
    return _obj(envelope)["error"]["details"]


def _posted(urlopen):
    request = urlopen.call_args[0][0]
    return request, json.loads(request.data.decode())


def _create(**kwargs):
    args = {
        "workspace_id": WORKSPACE,
        "studio_id": STUDIO_ID,
        "display_name": DISPLAY_NAME,
        "template_body": TEMPLATE,
    }
    args.update(kwargs)
    return studio_crud.create_cvp_studio(DATADICT, **args)


def _delete(**kwargs):
    args = {"workspace_id": WORKSPACE, "studio_id": STUDIO_ID}
    args.update(kwargs)
    return studio_crud.delete_cvp_studio(DATADICT, **args)


# --- contract with the shared write helper ----------------------------------


def test_studio_config_path_is_post_allowlisted():
    assert studio_crud.STUDIO_CONFIG_PATH in resource_write.POST_PATH_ALLOWLIST
    # Removal is a StudioConfig POST, never a Resource DELETE.
    assert studio_crud.STUDIO_CONFIG_PATH not in resource_write.DELETE_PATH_ALLOWLIST


# --- writes_disabled --------------------------------------------------------


def test_all_writes_refused_when_writes_disabled(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    for call in (_create, _delete):
        with _mocked() as mocks:
            env = call()
        assert _obj(env)["outcome"] == "refused"
        assert _code(env) == "writes_disabled"
        assert env["coverage"] == "none"
        mocks["get"].assert_not_called()
        mocks["urlopen"].assert_not_called()


# --- id validation ----------------------------------------------------------


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
@pytest.mark.parametrize("call", [_create, _delete])
def test_workspace_id_refusals_never_touch_http(call, workspace_id, code):
    with _mocked() as mocks:
        env = call(workspace_id=workspace_id)
    assert _code(env) == code
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("studio_id", "code"),
    [
        ("", "studio_id_required"),
        ("   ", "studio_id_required"),
        (None, "studio_id_required"),
        ("studio one", "invalid_studio_id"),
        ("studio?key.workspaceId=", "invalid_studio_id"),
        ("../../changecontrol", "invalid_studio_id"),
    ],
)
@pytest.mark.parametrize("call", [_create, _delete])
def test_studio_id_refusals_never_touch_http(call, studio_id, code):
    with _mocked() as mocks:
        env = call(studio_id=studio_id)
    assert _code(env) == code
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- EOS lint ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "matched"),
    [
        ("interface Ethernet1\n   shutdown\n", "shutdown"),
        ("interface Ethernet1\n   no shutdown\n", "no_shutdown"),
        ("interface Ethernet1\n   SHUTDOWN\n", "shutdown"),
        ("no interface Vlan42\n", "no_interface"),
        ("No   Interface Vlan42\n", "no_interface"),
        ("reload in 5\n", "reload"),
        ("write erase\n", "write_erase"),
        ("WRITE\tERASE\n", "write_erase"),
    ],
)
def test_create_refuses_disruptive_template_without_any_http(template, matched):
    with _mocked() as mocks:
        env = _create(template_body=template)
    assert _code(env) == "disruptive_content_forbidden"
    assert matched in _details(env)["matched"]
    assert _details(env)["fields"] == ["template_body"]
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("field", ["display_name", "description"])
def test_create_lints_display_name_and_description(field):
    with _mocked() as mocks:
        env = _create(**{field: "port shutdown helper"})
    assert _code(env) == "disruptive_content_forbidden"
    assert _details(env)["fields"] == [field]
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_create_has_no_allow_disruptive_escape_hatch():
    with pytest.raises(TypeError):
        _create(template_body="shutdown\n", allow_disruptive=True)


def test_delete_does_not_lint_the_studio_id():
    """A studio literally named ``...-reload-...`` must stay removable."""
    studio_id = "studio-mcp-reload-helper"
    value = _studio_value()
    value["value"]["key"]["studioId"] = studio_id
    with _mocked(studio_mainline=value) as mocks:
        env = _delete(studio_id=studio_id)
    assert _obj(env)["outcome"] == "preview"
    mocks["urlopen"].assert_not_called()


# --- create: preview vs confirm ---------------------------------------------


def test_create_preview_makes_no_post_and_returns_token():
    with _mocked() as mocks:
        env = _create(description="uplink studio")
        get_calls = mocks["get"].call_count
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["dry_run"] is True
    assert obj["error"] is None
    assert obj["disruptive"] is False
    assert env["coverage"] == "full"
    assert obj["template_bytes"] == len(TEMPLATE.encode())
    assert obj["preview_token"] == preview_token(
        "create_cvp_studio",
        {
            "workspace_id": WORKSPACE,
            "studio_id": STUDIO_ID,
            "display_name": DISPLAY_NAME,
            "description": "uplink studio",
            "template_type": "TEMPLATE_TYPE_MAKO",
            "template_sha256": obj["template_sha256"],
        },
    )
    assert obj["request_body"] == {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "displayName": DISPLAY_NAME,
        "description": "uplink studio",
        "template": {"type": "TEMPLATE_TYPE_MAKO", "body": TEMPLATE},
    }
    # workspace GET + studio GET in the overlay + studio GET in mainline.
    assert get_calls == 3
    mocks["urlopen"].assert_not_called()


def test_create_confirm_posts_studio_config_once():
    with _mocked() as mocks:
        preview = _create()
        env = _create(confirm=True, preview_token_value=_obj(preview)["preview_token"])
        assert mocks["urlopen"].call_count == 1
        request, body = _posted(mocks["urlopen"])
    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["dry_run"] is False
    assert obj["resource_time"] == "2026-08-22T14:05:00Z"
    assert obj["next_action"] == "build_cvp_workspace"
    assert request.method == "POST"
    assert request.full_url.endswith(studio_crud.STUDIO_CONFIG_PATH)
    assert body == {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "displayName": DISPLAY_NAME,
        "description": "",
        "template": {"type": "TEMPLATE_TYPE_MAKO", "body": TEMPLATE},
    }


def test_create_confirm_without_token_refuses_preview_required():
    with _mocked() as mocks:
        env = _create(confirm=True)
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_create_confirm_with_token_for_another_template_refuses():
    with _mocked() as mocks:
        preview = _create(template_body="! other\n")
        env = _create(confirm=True, preview_token_value=_obj(preview)["preview_token"])
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


def test_create_post_failure_reports_resource_write_failed():
    with _mocked(post_error=OSError("boom")) as mocks:
        preview = _create()
        env = _create(confirm=True, preview_token_value=_obj(preview)["preview_token"])
        assert mocks["urlopen"].call_count == 1
    assert _code(env) == "resource_write_failed"


# --- create: preflight refusals ---------------------------------------------


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ({"immutable": True}, "studio_immutable"),
        ({"from_package": True}, "studio_from_package"),
        ({"immutable": True, "from_package": True}, "studio_from_package"),
    ],
)
def test_create_refuses_immutable_or_packaged_studio(flags, code):
    with _mocked(studio_mainline=_studio_value(**flags)) as mocks:
        env = _create()
    assert _code(env) == code
    assert _details(env)["found_in_workspace_id"] == ""
    mocks["urlopen"].assert_not_called()


def test_create_refuses_immutable_studio_found_in_the_workspace_overlay():
    overlay = _studio_value(workspace_id=WORKSPACE, immutable=True)
    with _mocked(studio_overlay=overlay) as mocks:
        env = _create()
    assert _code(env) == "studio_immutable"
    assert _details(env)["found_in_workspace_id"] == WORKSPACE
    mocks["urlopen"].assert_not_called()


def test_create_refuses_existing_mutable_studio_with_both_digests():
    with _mocked(studio_mainline=_studio_value(template="! existing\n")) as mocks:
        env = _create()
    assert _code(env) == "studio_exists"
    details = _details(env)
    assert details["found_in_workspace_id"] == ""
    assert details["existing_template_sha256"] != details["new_template_sha256"]
    mocks["urlopen"].assert_not_called()


def test_create_refuses_studio_already_copied_into_the_workspace():
    overlay = _studio_value(workspace_id=WORKSPACE)
    with _mocked(studio_overlay=overlay) as mocks:
        env = _create()
    assert _code(env) == "studio_exists"
    assert _details(env)["found_in_workspace_id"] == WORKSPACE
    mocks["urlopen"].assert_not_called()


def test_create_refuses_when_studio_preflight_get_fails():
    with _mocked(studio_mainline=("error", "http_error:503")) as mocks:
        env = _create()
    assert _code(env) == "studio_read_failed"
    mocks["urlopen"].assert_not_called()


def test_create_refuses_when_studio_get_returns_200_without_value():
    with _mocked(studio_mainline={"result": {}}) as mocks:
        env = _create()
    assert _code(env) == "studio_read_failed"
    mocks["urlopen"].assert_not_called()


def test_create_refuses_when_workspace_missing():
    with _mocked(workspace=MISSING) as mocks:
        env = _create()
    assert _code(env) == "workspace_not_found"
    mocks["urlopen"].assert_not_called()


def test_create_refuses_when_workspace_get_fails():
    with _mocked(workspace=("error", "http_error:503")) as mocks:
        env = _create()
    assert _code(env) == "workspace_read_failed"
    mocks["urlopen"].assert_not_called()


def test_create_refuses_without_credentials():
    with _mocked() as mocks:
        env = studio_crud.create_cvp_studio(
            {"cvtoken": "", "cvp": "cvp.example.com"},
            WORKSPACE,
            STUDIO_ID,
            DISPLAY_NAME,
            TEMPLATE,
        )
    assert _code(env) == "preflight_failed"
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("template_type", ["", "TEMPLATE_TYPE_JINJA", "mako", None])
def test_create_refuses_unknown_template_type(template_type):
    with _mocked() as mocks:
        env = _create(template_type=template_type)
    assert _code(env) == "invalid_template_type"
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize("display_name", ["", "   ", None])
def test_create_requires_display_name(display_name):
    with _mocked() as mocks:
        env = _create(display_name=display_name)
    assert _code(env) == "display_name_required"
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    "kwargs", [{"template_body": None}, {"template_body": 7}, {"description": []}]
)
def test_create_requires_string_template_and_description(kwargs):
    with _mocked() as mocks:
        env = _create(**kwargs)
    assert _code(env) == "invalid_template"
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


# --- delete -----------------------------------------------------------------


def test_delete_preview_makes_no_request():
    with _mocked(studio_mainline=_studio_value()) as mocks:
        env = _delete()
    obj = _obj(env)
    assert obj["outcome"] == "preview"
    assert obj["dry_run"] is True
    assert obj["found_in_workspace_id"] == ""
    assert obj["request_body"] == {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "remove": True,
    }
    assert obj["preview_token"] == preview_token(
        "delete_cvp_studio", {"workspace_id": WORKSPACE, "studio_id": STUDIO_ID}
    )
    mocks["urlopen"].assert_not_called()


def test_delete_confirm_posts_remove_true_once_and_never_deletes():
    with _mocked(studio_mainline=_studio_value()) as mocks:
        preview = _delete()
        env = _delete(confirm=True, preview_token_value=_obj(preview)["preview_token"])
        assert mocks["urlopen"].call_count == 1
        request, body = _posted(mocks["urlopen"])
    obj = _obj(env)
    assert obj["outcome"] == "accepted"
    assert obj["dry_run"] is False
    assert obj["resource_time"] == "2026-08-22T14:05:00Z"
    assert request.method == "POST"
    assert request.full_url.endswith(studio_crud.STUDIO_CONFIG_PATH)
    assert "changecontrol" not in request.full_url.lower()
    assert body == {
        "key": {"studioId": STUDIO_ID, "workspaceId": WORKSPACE},
        "remove": True,
    }


def test_delete_confirm_without_token_refuses():
    with _mocked(studio_mainline=_studio_value()) as mocks:
        env = _delete(confirm=True)
    assert _code(env) == "preview_required"
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("WORKSPACE_STATE_SUBMITTED", "workspace_not_pending"),
        ("WORKSPACE_STATE_ABANDONED", "workspace_not_pending"),
        ("WORKSPACE_STATE_CONFLICTS", "workspace_not_pending"),
        ("", "workspace_not_pending"),
    ],
)
@pytest.mark.parametrize("call", [_create, _delete])
def test_studio_writes_require_a_pending_workspace(call, state, code):
    with _mocked(
        workspace=_workspace_value(state=state), studio_mainline=_studio_value()
    ) as mocks:
        env = call()
    assert _code(env) == code
    assert _details(env)["state"] == state
    mocks["urlopen"].assert_not_called()


def test_delete_refuses_when_studio_missing_everywhere():
    with _mocked() as mocks:
        env = _delete()
        get_calls = mocks["get"].call_count
    assert _code(env) == "studio_not_found"
    assert get_calls == 3
    mocks["urlopen"].assert_not_called()


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ({"immutable": True}, "studio_immutable"),
        ({"from_package": True}, "studio_from_package"),
    ],
)
def test_delete_refuses_immutable_or_packaged_studio(flags, code):
    with _mocked(studio_mainline=_studio_value(**flags)) as mocks:
        env = _delete()
    assert _code(env) == code
    mocks["urlopen"].assert_not_called()


def test_delete_refuses_studio_still_in_use():
    with _mocked(studio_mainline=_studio_value(in_use=True)) as mocks:
        env = _delete()
    assert _code(env) == "studio_in_use"
    mocks["urlopen"].assert_not_called()


def test_delete_prefers_the_workspace_overlay_over_mainline():
    overlay = _studio_value(workspace_id=WORKSPACE, template="! overlay\n")
    with _mocked(
        studio_overlay=overlay, studio_mainline=_studio_value(template="! mainline\n")
    ) as mocks:
        env = _delete()
        get_calls = mocks["get"].call_count
    obj = _obj(env)
    assert obj["found_in_workspace_id"] == WORKSPACE
    # Mainline is not consulted once the overlay resolves.
    assert get_calls == 2


def test_delete_refuses_when_studio_preflight_get_fails():
    with _mocked(studio_mainline=("error", "http_error:503")) as mocks:
        env = _delete()
    assert _code(env) == "studio_read_failed"
    mocks["urlopen"].assert_not_called()


def test_delete_refuses_without_credentials():
    with _mocked() as mocks:
        env = studio_crud.delete_cvp_studio(
            {"cvtoken": "container-token", "cvp": ""}, WORKSPACE, STUDIO_ID
        )
    assert _code(env) == "preflight_failed"
    mocks["get"].assert_not_called()
    mocks["urlopen"].assert_not_called()


def test_delete_post_failure_reports_resource_write_failed():
    with _mocked(studio_mainline=_studio_value(), post_error=OSError("boom")) as mocks:
        preview = _delete()
        env = _delete(confirm=True, preview_token_value=_obj(preview)["preview_token"])
        assert mocks["urlopen"].call_count == 1
    assert _code(env) == "resource_write_failed"
