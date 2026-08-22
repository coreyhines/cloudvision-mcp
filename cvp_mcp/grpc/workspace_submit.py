"""Studios Phase 2.1 submit library: workspace submit behind a staleness CAS.

Nothing here is registered as an MCP tool, and nothing here may be registered
until ``cvp_mcp.write_access.SUBMIT_STALENESS_FIELD`` is set to a
human-confirmed Workspace staleness field. While that constant is ``None``,
:func:`submit_cvp_workspace` refuses with ``submit_disabled`` **before** any
HTTP request is made, even when both write env vars are ``"1"``.

Submit is the one operation that changes mainline designed config, so it takes
a stricter path than the Phase 2.0 writes in
:mod:`cvp_mcp.grpc.studios_write`:

* a second opt-in argument (``allow_submit``) on top of the env gates,
* a compare-and-set against the workspace ``last_modified_at`` the caller saw,
* a re-GET of both the workspace and the named build immediately before the
  POST, so an edit that lands between preview and confirm is caught.

The CAS proves the workspace is *unchanged*, not that a human reviewed it. The
resulting change control is left pending: this module never touches
ChangeControlConfig and never reports ``outcome: "succeeded"``. See
``docs/studios-phase2-spec.md``.
"""

from __future__ import annotations

import uuid
from typing import Any

from cvp_mcp import write_access
from cvp_mcp.grpc.resource_write import REQUEST_SUBMIT, post_resource_config
from cvp_mcp.grpc.studios import get_cvp_workspace_build

# Private helpers are shared with the Phase 2.0 write library on purpose: the
# envelope, credential and workspace-preflight shapes are normative and must
# not drift between write slices.
from cvp_mcp.grpc.studios_write import (
    WORKSPACE_CONFIG_PATH,
    WORKSPACE_STATE_PENDING,
    _as_str,
    _credentials,
    _non_terminal_responses,
    _outcome,
    _read_workspace,
    _refused,
    _resource_time,
)
from cvp_mcp.write_access import (
    check_preview_token,
    preview_token,
    validate_workspace_id,
    writes_enabled,
)

TOOL_NAME = "submit_cvp_workspace"

_WORKSPACE_SOURCE = "resource_api:workspace.v1"

# The only build state a submit may follow. Anything else (including an
# unknown state) fails closed.
BUILD_STATE_SUCCESS = "BUILD_STATE_SUCCESS"

# Wire and snake_case spellings of the one field this module knows how to read
# as a staleness anchor. If ``SUBMIT_STALENESS_FIELD`` is ever set to something
# else, submit stays refused rather than comparing an unknown field.
STALENESS_FIELD_ALIASES: frozenset[str] = frozenset(
    {"lastModifiedAt", "last_modified_at"}
)


def _staleness_field_supported() -> bool:
    """True only when ``SUBMIT_STALENESS_FIELD`` names a field this module reads.

    ``None`` (the production default) and any unrecognized name are both False:
    comparing an unknown field would be worse than refusing.
    """
    field = (write_access.SUBMIT_STALENESS_FIELD or "").strip()
    return field in STALENESS_FIELD_ALIASES


def _cc_ids(response: Any) -> list[str] | None:
    """Change control ids carried by the POST response, else ``None``.

    An absent or empty ``ccIds`` means *unknown*, never "no change control",
    so it is reported as ``None`` rather than ``[]``.
    """
    scopes: list[Any] = [response]
    if isinstance(response, dict):
        scopes.append(response.get("value"))
        result = response.get("result")
        scopes.append(result)
        if isinstance(result, dict):
            scopes.append(result.get("value"))
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for name in ("ccIds", "cc_ids"):
            value = scope.get(name)
            if isinstance(value, dict):
                value = value.get("values")
            if isinstance(value, list) and value:
                return [str(item) for item in value]
    return None


def _verify_submit_target(
    datadict: dict[str, Any],
    workspace: str,
    build_id: str,
    staleness_token: str,
) -> tuple[dict[str, Any] | None, tuple[str, str, dict[str, Any]] | None, list[str]]:
    """GET the workspace and its build and prove the submit is still safe.

    Returns ``(facts, refusal, warnings)`` where ``refusal`` is
    ``(code, message, details)`` and ``facts`` is the snapshot the caller
    reports back. Every check fails closed, so an unknown or unreadable state
    refuses rather than falling through to the POST.
    """
    summary, status, warnings = _read_workspace(datadict, workspace)
    if status == "not_found":
        return (
            None,
            ("workspace_not_found", "Workspace does not exist; nothing to submit.", {}),
            warnings,
        )
    if summary is None:
        return (
            None,
            (
                "workspace_read_failed",
                "Workspace GET did not return 200; refusing to submit.",
                {"warnings": warnings},
            ),
            warnings,
        )

    state = _as_str(summary.get("state")).strip()
    if not state:
        return (
            None,
            (
                "workspace_state_unknown",
                "Workspace state is unknown; refusing to submit.",
                {},
            ),
            warnings,
        )
    if state != WORKSPACE_STATE_PENDING:
        return (
            None,
            (
                "workspace_not_pending",
                "Only WORKSPACE_STATE_PENDING drafts may be submitted.",
                {"state": state},
            ),
            warnings,
        )

    pending = _non_terminal_responses(summary)
    if pending:
        return (
            None,
            (
                "build_in_progress",
                "A request on this workspace has not reached a terminal state.",
                {"request_ids": pending},
            ),
            warnings,
        )

    observed_token = _as_str(summary.get("last_modified_at")).strip()
    if not observed_token or observed_token != staleness_token:
        return (
            None,
            (
                "staleness_token_mismatch",
                "Workspace has changed since the caller read it; re-read and retry.",
                {"observed": observed_token or None},
            ),
            warnings,
        )

    last_build_id = _as_str(summary.get("last_build_id")).strip()
    needs_build = bool(summary.get("needs_build"))
    if last_build_id != build_id or needs_build:
        return (
            None,
            (
                "workspace_modified_after_build",
                "The named build is not the workspace's current build.",
                {"last_build_id": last_build_id or None, "needs_build": needs_build},
            ),
            warnings,
        )

    build_env = get_cvp_workspace_build(datadict, workspace, build_id)
    build = build_env.get("object") or {}
    warnings = warnings + [
        w for w in (build_env.get("warnings") or []) if isinstance(w, str)
    ]
    if build_env.get("coverage") != "full" or not isinstance(build, dict):
        return (
            None,
            (
                "build_not_found",
                "Build GET did not return 200; refusing to submit.",
                {"warnings": warnings},
            ),
            warnings,
        )
    build_state = _as_str(build.get("state")).strip()
    if build_state != BUILD_STATE_SUCCESS:
        return (
            None,
            (
                "build_not_successful",
                f"Build state must be {BUILD_STATE_SUCCESS}.",
                {"state": build_state or None, "error": build.get("error") or None},
            ),
            warnings,
        )

    facts = {
        "workspace_state": state,
        "last_build_id": last_build_id,
        "needs_build": needs_build,
        "workspace_last_modified_at": observed_token,
        "build_state": build_state,
        "build_time": _as_str(build.get("time")).strip(),
    }
    return facts, None, warnings


def submit_cvp_workspace(
    datadict: dict[str, Any],
    workspace_id: str,
    build_id: str,
    workspace_staleness_token: str,
    request_id: str | None = None,
    confirm: bool = False,
    allow_submit: bool = False,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Submit a built draft workspace (``REQUEST_SUBMIT``, hard-coded here).

    ``build_id`` and ``workspace_staleness_token`` have no defaults: the caller
    must echo back the build it reviewed and the workspace ``last_modified_at``
    it read, and both are re-checked against a fresh GET immediately before the
    POST. Empty values refuse with ``staleness_token_required``.

    HTTP 200 does not mean the submit landed: the returned outcome is
    ``accepted`` with ``done=False``, and any resulting change control still
    needs human approval in the CVP UI.
    """
    workspace = (workspace_id or "").strip()

    # Ordered so the two process-level gates run before argument parsing: a
    # disabled submit must look identical no matter what the caller passed.
    if not writes_enabled():
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )
    if not _staleness_field_supported() or not write_access.submit_enabled():
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "submit_disabled",
            "Submit is disabled until SUBMIT_STALENESS_FIELD is registered and "
            "CLOUDVISION_MCP_ALLOW_SUBMIT=1.",
            details={"staleness_field": write_access.SUBMIT_STALENESS_FIELD},
            workspace_id=workspace or None,
        )

    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            workspace_id=workspace or None,
        )

    build = (build_id or "").strip() if isinstance(build_id, str) else ""
    staleness = (
        (workspace_staleness_token or "").strip()
        if isinstance(workspace_staleness_token, str)
        else ""
    )
    if not build or not staleness:
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "staleness_token_required",
            "build_id and workspace_staleness_token are required and must be "
            "the values read from get_cvp_workspace.",
            details={
                "build_id": bool(build),
                "workspace_staleness_token": bool(staleness),
            },
            workspace_id=workspace,
        )

    if request_id is not None and (
        not isinstance(request_id, str) or not request_id.strip()
    ):
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "invalid_request_id",
            "request_id must be a non-empty string when supplied.",
            workspace_id=workspace,
        )

    # The second opt-in. Checked before any GET so a caller who forgot it
    # cannot even probe the workspace through this path.
    if confirm and not allow_submit:
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "submit_not_allowed",
            "confirm=True also requires allow_submit=True.",
            workspace_id=workspace,
        )

    if confirm and not (request_id or "").strip():
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "invalid_request_id",
            "confirm=True must pass the request_id from the preview.",
            workspace_id=workspace,
        )
    effective_request_id = (request_id or "").strip() or str(uuid.uuid4())

    body = {
        "key": {"workspaceId": workspace},
        "request": REQUEST_SUBMIT,
        "requestParams": {"requestId": effective_request_id},
    }
    token_args = {
        "workspace_id": workspace,
        "build_id": build,
        "workspace_staleness_token": staleness,
        "request_id": effective_request_id,
    }
    if confirm:
        token_error = check_preview_token(TOOL_NAME, token_args, preview_token_value)
        if token_error:
            return _refused(
                TOOL_NAME,
                _WORKSPACE_SOURCE,
                token_error,
                "confirm=True requires the preview_token from a matching dry run.",
                workspace_id=workspace,
            )

    _, _, missing = _credentials(datadict)
    if missing:
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

    # The compare-and-set. On the confirm path this is the re-GET: every gate
    # above is pure computation, so nothing can land between this snapshot and
    # the POST below except a change this read would have seen.
    facts, refusal, warnings = _verify_submit_target(
        datadict, workspace, build, staleness
    )
    if refusal is not None:
        code, message, details = refusal
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            code,
            message,
            details=details,
            workspace_id=workspace,
            warnings=warnings,
        )

    fields: dict[str, Any] = {
        "operation": "submit",
        "done": False,
        "request": REQUEST_SUBMIT,
        "request_id": effective_request_id,
        "build_id": build,
        "workspace_staleness_token": staleness,
        "staleness": facts,
        "request_body": body,
        "cc_ids": None,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(TOOL_NAME, token_args)
        return _outcome(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            outcome="preview",
            workspace_id=workspace,
            fields=fields,
            next_action=(
                "Review the build diff in the CVP UI, then re-call with "
                "confirm=True, allow_submit=True, this preview_token and request_id."
            ),
            warnings=warnings,
        )

    cvtoken, base, _ = _credentials(datadict)
    response, err = post_resource_config(
        base,
        WORKSPACE_CONFIG_PATH,
        body,
        cvtoken,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        return _refused(
            TOOL_NAME,
            _WORKSPACE_SOURCE,
            "resource_write_failed",
            "WorkspaceConfig submit POST failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    fields["cc_ids"] = _cc_ids(response)
    return _outcome(
        TOOL_NAME,
        _WORKSPACE_SOURCE,
        # Never "succeeded": the POST only queues the submit, and any change
        # control it creates stays pending human approval.
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action=(
            "poll get_cvp_workspace, then approve the change control in the "
            "CVP UI (never automated)"
        ),
        warnings=warnings,
    )
