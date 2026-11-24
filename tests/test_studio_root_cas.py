"""Unit tests for shared studio root Inputs CAS."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from cvp_mcp.grpc import studio_root_cas as root_cas
from cvp_mcp.grpc.inputs_digest import inputs_sha256
from cvp_mcp.write_access import WRITES_ENV

DOC = {"onboardingProfiles": [], "profileAssignment": [], "customFlags": []}
SHA = inputs_sha256(DOC)
CFG = root_cas.RootCasConfig(
    tool_name="set_cvp_test_root_inputs",
    studio_id="studio-test-root",
    operation="set_test_root_inputs",
    writable_top_level_keys=("onboardingProfiles", "profileAssignment"),
    scope_error_code="tree_diff_outside_test_scope",
    scope_error_message="outside writable keys",
    load_error_message="Could not read test root Inputs.",
    digest_mismatch_message="digest mismatch",
)
DATADICT = {"cvtoken": "t", "cvp": "cvp.example.com", "cert": None}
WS = "ws-mcp-root-cas-test"
PENDING = ({"state": "WORKSPACE_STATE_PENDING"}, "ok", [])
STUDIO_OK = ({"immutable": None, "from_package": None}, "", None, [])


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch):
    monkeypatch.setenv(WRITES_ENV, "1")


def _apply_noop(document):
    return document, {"added": [], "replaced": [], "removed": []}, None


def _apply_profile(document):
    after = dict(document)
    after["onboardingProfiles"] = [{"profileName": "x"}]
    return after, {"added": ["profile:x"], "replaced": [], "removed": []}, None


def _run(
    *,
    sha=SHA,
    apply=_apply_noop,
    workspace=PENDING,
    studio=STUDIO_OK,
    load=(DOC, "", None, []),
    creds=("t", "https://cvp", None),
    post_return=({"time": "2026-11-23T00:00:00Z"}, None),
    ws_id=WS,
    **kwargs,
):
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(root_cas, "_read_workspace", return_value=workspace)
        )
        stack.enter_context(
            patch.object(root_cas, "_read_studio_anywhere", return_value=studio)
        )
        stack.enter_context(
            patch.object(root_cas, "_load_root_inputs", return_value=load)
        )
        stack.enter_context(patch.object(root_cas, "_credentials", return_value=creds))
        post = stack.enter_context(
            patch.object(root_cas, "post_resource_config", return_value=post_return)
        )
        kwargs.setdefault("operations", [{"op": "noop"}])
        env = root_cas.run_root_inputs_cas(
            DATADICT, ws_id, sha, config=CFG, apply=apply, **kwargs
        )
    return env, post


def _code(env):
    return env["object"]["error"]["code"]


def test_digest_mismatch_refuses_without_post():
    env, post = _run(sha="0" * 64, operations=[])
    assert _code(env) == "inputs_digest_mismatch"
    assert env["object"]["error"]["message"] == "digest mismatch"
    assert post.call_count == 0


def test_writes_disabled(monkeypatch):
    monkeypatch.delenv(WRITES_ENV, raising=False)
    env, post = _run()
    assert _code(env) == "writes_disabled"
    assert post.call_count == 0


def test_bad_workspace_id_refused():
    env, _ = _run(ws_id="ws-other")
    assert _code(env)  # validate_workspace_id code
    assert "ws-mcp-" in env["object"]["error"]["message"]


@pytest.mark.parametrize("sha", ["", "abc", "G" * 64, "A" * 64, None])
def test_malformed_digest_refused(sha):
    env, post = _run(sha=sha)
    assert _code(env) == "expected_inputs_sha256_required"
    assert post.call_count == 0


def test_missing_credentials_refused():
    env, _ = _run(creds=("", "", "cvtoken missing"))
    assert _code(env) == "preflight_failed"


def test_workspace_not_pending_refused():
    env, _ = _run(workspace=({"state": "WORKSPACE_STATE_SUBMITTED"}, "ok", []))
    assert _code(env) == "workspace_not_pending"


def test_workspace_not_found_refused():
    env, _ = _run(workspace=(None, "not_found", []))
    assert _code(env) == "workspace_not_found"


@pytest.mark.parametrize(
    ("studio_obj", "code"),
    [
        ({"immutable": True, "from_package": None}, "studio_immutable"),
        ({"immutable": None, "from_package": True}, "studio_from_package"),
    ],
)
def test_immutable_studio_refused(studio_obj, code):
    env, post = _run(studio=(studio_obj, "", None, []))
    assert _code(env) == code
    assert post.call_count == 0


def test_studio_not_found_refused():
    env, _ = _run(studio=(None, "", "not_found", []))
    assert _code(env) == "preflight_failed"


def test_load_error_uses_config_message():
    env, _ = _run(load=(None, "", "inputs_path_unresolved", []))
    assert _code(env) == "inputs_path_unresolved"
    assert env["object"]["error"]["message"] == CFG.load_error_message


def test_apply_error_refuses_with_its_code():
    def apply(document):
        return (
            document,
            {"added": [], "replaced": [], "removed": []},
            {"code": "plugin_bad_op", "message": "nope", "details": {"i": 1}},
        )

    env, post = _run(apply=apply)
    assert _code(env) == "plugin_bad_op"
    assert post.call_count == 0


def test_preview_apply_posts_nothing():
    env, post = _run(apply=_apply_profile, operations=[{"op": "noop"}])
    obj = env["object"]
    assert obj["outcome"] == "preview"
    assert "preview_token" in obj
    assert obj["operation"] == "set_test_root_inputs"
    assert obj["posted_at_root"] is True
    assert obj["before_sha256"] == SHA
    assert obj["after_sha256"] != SHA
    assert obj["entries_added"] == ["profile:x"]
    assert obj["request_body"]["key"]["path"] == {"values": []}
    assert obj["request_body"]["key"]["studioId"] == "studio-test-root"
    assert post.call_count == 0


def test_scope_violation_refused():
    def apply(document):
        after = dict(document)
        after["customFlags"] = [{"x": 1}]
        return after, {"added": [], "replaced": [], "removed": []}, None

    env, post = _run(apply=apply)
    assert _code(env) == "tree_diff_outside_test_scope"
    assert env["object"]["error"]["details"]["outside"]
    assert post.call_count == 0


def test_unchanged_document_warns():
    env, _ = _run()
    assert env["object"]["outcome"] == "preview"
    assert "inputs_unchanged" in env["warnings"]


def test_early_warnings_and_extra_fields_pass_through():
    env, _ = _run(
        apply=_apply_profile,
        early_warnings=["early_note"],
        extra_fields={"custom": 7},
    )
    assert "early_note" in env["warnings"]
    assert env["object"]["custom"] == 7


def test_confirm_without_token_refused():
    env, post = _run(apply=_apply_profile, confirm=True)
    assert post.call_count == 0
    assert env["object"]["outcome"] != "accepted"
    assert "error" in env["object"]


def test_confirm_with_token_posts_once():
    preview, _ = _run(apply=_apply_profile)
    token = preview["object"]["preview_token"]
    env, post = _run(apply=_apply_profile, confirm=True, preview_token_value=token)
    assert env["object"]["outcome"] == "accepted"
    assert post.call_count == 1
    body = post.call_args.args[2]
    assert body["key"]["path"] == {"values": []}


def test_token_bound_to_operations():
    preview, _ = _run(apply=_apply_profile, operations=[{"op": "a"}])
    token = preview["object"]["preview_token"]
    env, post = _run(
        apply=_apply_profile,
        operations=[{"op": "b"}],
        confirm=True,
        preview_token_value=token,
    )
    assert post.call_count == 0
    assert "error" in env["object"]


def test_post_failure_refused():
    preview, _ = _run(apply=_apply_profile)
    token = preview["object"]["preview_token"]
    env, post = _run(
        apply=_apply_profile,
        confirm=True,
        preview_token_value=token,
        post_return=(None, "boom"),
    )
    assert post.call_count == 1
    assert _code(env) == "resource_write_failed"


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["$.a", "$.a[0].x", "$.b"], ["$.b"]),
        (["$.ab", "$.a.b"], ["$.ab", "$.a.b"]),
    ],
)
def test_scope_violations(paths, expected):
    assert root_cas.scope_violations(paths, ("a",)) == expected
