"""Studios Phase 2.2 write library: studio create / delete (bucket 1d).

Library functions only. Nothing here is registered as an MCP tool; registration
stays behind the env gate in ``cloudvision_mcp.py`` (bucket W). Both functions
follow the Phase 2 write contract from ``docs/studios-phase2-spec.md``:

* refuse with ``writes_disabled`` unless :func:`writes_enabled`,
* refuse before any HTTP request is built,
* return a ``preview_token`` and perform no mutating HTTP when ``confirm=False``,
* perform **one** ``POST StudioConfig`` when ``confirm=True`` and the caller
  echoes back the matching ``preview_token``.

Slice-specific rules:

* Mainline (``workspaceId=""``) is never a write target and the target workspace
  must be ``WORKSPACE_STATE_PENDING``.
* The studio is keyed-GET in **both** the target workspace and mainline
  (RR2-I9). ``immutable`` / ``from_package`` studios are refused; create also
  refuses an id that already exists rather than silently upserting over it.
  There is no ``overwrite_existing`` escape hatch in this slice — replace a
  studio's content with the Inputs tools, not by re-creating it.
* Caller free text (template body, display name, description) is linted for
  EOS-disruptive config. There is deliberately no ``allow_disruptive`` flag.
* Delete is a ``StudioConfig`` POST carrying ``remove: true``. ChangeControl is
  never touched here; the operator still has to build, then review and submit in the CVP UI.

The envelope, preflight and lint helpers are imported from
:mod:`cvp_mcp.grpc.studios_write` rather than copied so that 2.0 and 2.2
refusals stay identical in shape and the EOS lint has a single definition.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from cvp_mcp.grpc.resource_write import post_resource_config
from cvp_mcp.grpc.studios import get_cvp_studio
from cvp_mcp.grpc.studios_write import (
    WORKSPACE_STATE_PENDING,
    _credentials,
    _disruptive_hits,
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

STUDIO_CONFIG_PATH = "/api/resources/studio/v1/StudioConfig"

MAINLINE_WORKSPACE_ID = ""

# Only template type observed on this tenant (tests/fixtures/
# studio_mainline_event_handler.json). Do not add guessed enum names.
TEMPLATE_TYPE_MAKO = "TEMPLATE_TYPE_MAKO"
ALLOWED_TEMPLATE_TYPES: frozenset[str] = frozenset({TEMPLATE_TYPE_MAKO})

# Studio ids seen on this tenant look like ``studio-campus-access-interfaces``
# or a UUID. Reject anything that could smuggle a query separator into the
# preflight GET or a path segment into the POST key.
_STUDIO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

# Caller-supplied free text that ends up in generated device config or in the
# CVP UI. The studio *id* is deliberately not linted: an id such as
# ``studio-reload-helper`` is not EOS config and must stay deletable.
_LINTED_FIELDS: tuple[str, ...] = ("template_body", "display_name", "description")

_STUDIO_SOURCE = "resource_api:studio.v1.config"


# --- preflight --------------------------------------------------------------


def _read_studio(
    datadict: dict[str, Any], studio_id: str, workspace_id: str
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Keyed studio GET.

    Returns ``(summary, status, warnings)`` where ``status`` is ``None`` when
    found, ``"not_found"`` on HTTP 404, else ``"read_failed"``. Anything but a
    clean 200 fails closed, including a 200 with no ``value``: the caller must
    not write on an ambiguous preflight.
    """
    env = get_cvp_studio(datadict, studio_id, workspace_id)
    obj = env.get("object") or {}
    warnings = [w for w in (env.get("warnings") or []) if isinstance(w, str)]
    if env.get("coverage") == "full" and isinstance(obj, dict) and obj.get("studio_id"):
        return obj, None, warnings
    if any(w == "http_error:404" for w in warnings):
        return None, "not_found", warnings
    return None, "read_failed", warnings


def _read_studio_anywhere(
    datadict: dict[str, Any], studio_id: str, workspace_id: str
) -> tuple[dict[str, Any] | None, str, str | None, list[str]]:
    """GET the studio in the target workspace, then mainline.

    Returns ``(summary, source_workspace_id, status, warnings)``. A studio
    already copied into the workspace shadows mainline, so the overlay is
    checked first; a failed read on either scope short-circuits to
    ``"read_failed"`` so nothing is written on a partial view.
    """
    warnings: list[str] = []
    for source in (workspace_id, MAINLINE_WORKSPACE_ID):
        summary, status, warns = _read_studio(datadict, studio_id, source)
        warnings.extend(warns)
        if status == "read_failed":
            return None, source, "read_failed", warnings
        if summary is not None:
            return summary, source, None, warnings
    return None, "", "not_found", warnings


def _flag_refusal(summary: dict[str, Any]) -> str | None:
    """Refusal code when a studio is packaged or immutable, else ``None``."""
    if summary.get("from_package") is True:
        return "studio_from_package"
    if summary.get("immutable") is True:
        return "studio_immutable"
    return None


def _lint_refusal(fields: dict[str, str]) -> dict[str, Any] | None:
    """Return refusal ``details`` when any linted field carries EOS-disruptive text."""
    matched: dict[str, list[str]] = {}
    for name in _LINTED_FIELDS:
        hits = _disruptive_hits(fields.get(name, ""))
        if hits:
            matched[name] = hits
    if not matched:
        return None
    return {
        "fields": sorted(matched),
        "matched": sorted({hit for hits in matched.values() for hit in hits}),
    }


def _validate_ids(
    tool: str, workspace_id: str, studio_id: str
) -> tuple[str, str, dict[str, Any] | None]:
    """Validate workspace and studio ids. Returns ``(workspace, studio, refusal)``."""
    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return (
            workspace,
            "",
            _refused(
                tool,
                _STUDIO_SOURCE,
                id_error,
                "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
                workspace_id=workspace or None,
            ),
        )

    studio = (studio_id or "").strip() if isinstance(studio_id, str) else ""
    if not studio:
        return (
            workspace,
            studio,
            _refused(
                tool,
                _STUDIO_SOURCE,
                "studio_id_required",
                "studio_id must be a non-empty string.",
                workspace_id=workspace,
            ),
        )
    if not _STUDIO_ID_RE.match(studio):
        return (
            workspace,
            studio,
            _refused(
                tool,
                _STUDIO_SOURCE,
                "invalid_studio_id",
                "studio_id may only contain letters, digits and '.', '_', ':', '-'.",
                workspace_id=workspace,
            ),
        )
    return workspace, studio, None


def _pending_workspace_refusal(
    tool: str, datadict: dict[str, Any], workspace: str, verb: str
) -> tuple[list[str], dict[str, Any] | None]:
    """Credential + workspace preflight. Returns ``(warnings, refusal)``."""
    _, _, missing = _credentials(datadict)
    if missing:
        return [], _refused(
            tool,
            _STUDIO_SOURCE,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

    summary, status, warnings = _read_workspace(datadict, workspace)
    if status == "not_found":
        return warnings, _refused(
            tool,
            _STUDIO_SOURCE,
            "workspace_not_found",
            f"Workspace does not exist; refusing to {verb} a studio.",
            workspace_id=workspace,
            warnings=warnings,
        )
    if summary is None:
        return warnings, _refused(
            tool,
            _STUDIO_SOURCE,
            "workspace_read_failed",
            f"Workspace preflight GET did not return 200; refusing to {verb}.",
            details={"warnings": warnings},
            workspace_id=workspace,
            warnings=warnings,
        )

    state = summary.get("state")
    state_text = state.strip() if isinstance(state, str) else ""
    if state_text != WORKSPACE_STATE_PENDING:
        return warnings, _refused(
            tool,
            _STUDIO_SOURCE,
            "workspace_not_pending",
            "Studio writes are only allowed on pending draft workspaces.",
            details={"state": state_text},
            workspace_id=workspace,
            warnings=warnings,
        )
    return warnings, None


def _post_studio_config(
    tool: str,
    datadict: dict[str, Any],
    workspace: str,
    body: dict[str, Any],
    fields: dict[str, Any],
    *,
    next_action: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Perform the single mutating POST and build the accepted envelope."""
    cvtoken, base, _ = _credentials(datadict)
    response, err = post_resource_config(
        base,
        STUDIO_CONFIG_PATH,
        body,
        cvtoken,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "resource_write_failed",
            "StudioConfig POST failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    return _outcome(
        tool,
        _STUDIO_SOURCE,
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action=next_action,
        warnings=warnings,
    )


# --- create -----------------------------------------------------------------


def create_cvp_studio(
    datadict: dict[str, Any],
    workspace_id: str,
    studio_id: str,
    display_name: str,
    template_body: str = "",
    description: str = "",
    template_type: str = TEMPLATE_TYPE_MAKO,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Create a studio in a pending draft workspace (``POST StudioConfig``).

    The template is linted for EOS-disruptive config **before** any preflight
    GET, so a bad template costs no HTTP at all. An id that already exists in
    the workspace or in mainline refuses with ``studio_exists`` (the refusal
    reports both template digests so the caller can see what an upsert would
    have replaced); ``immutable`` / ``from_package`` refuse ahead of that.
    """
    tool = "create_cvp_studio"
    if not writes_enabled():
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    workspace, studio, refusal = _validate_ids(tool, workspace_id, studio_id)
    if refusal is not None:
        return refusal

    name = display_name if isinstance(display_name, str) else ""
    if not name.strip():
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "display_name_required",
            "display_name must be a non-empty string.",
            workspace_id=workspace,
        )

    if not isinstance(template_body, str) or not isinstance(description, str):
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "invalid_template",
            "template_body and description must be strings.",
            workspace_id=workspace,
        )
    if template_type not in ALLOWED_TEMPLATE_TYPES:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "invalid_template_type",
            "template_type must be TEMPLATE_TYPE_MAKO.",
            details={"template_type": template_type},
            workspace_id=workspace,
        )

    lint_details = _lint_refusal(
        {
            "template_body": template_body,
            "display_name": name,
            "description": description,
        }
    )
    if lint_details is not None:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "disruptive_content_forbidden",
            "The studio template or text contains EOS-disruptive config.",
            details=lint_details,
            workspace_id=workspace,
        )

    warnings, refusal = _pending_workspace_refusal(tool, datadict, workspace, "create")
    if refusal is not None:
        return refusal

    existing, source, status, studio_warnings = _read_studio_anywhere(
        datadict, studio, workspace
    )
    warnings = warnings + studio_warnings
    if status == "read_failed":
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "studio_read_failed",
            "Studio preflight GET did not return 200; refusing to create.",
            details={"studio_id": studio, "workspace_id": source},
            workspace_id=workspace,
            warnings=warnings,
        )

    template_bytes = template_body.encode("utf-8")
    template_sha256 = hashlib.sha256(template_bytes).hexdigest()

    if existing is not None:
        flag = _flag_refusal(existing)
        if flag:
            return _refused(
                tool,
                _STUDIO_SOURCE,
                flag,
                "Refusing to create over an immutable or packaged studio.",
                details={"studio_id": studio, "found_in_workspace_id": source},
                workspace_id=workspace,
                warnings=warnings,
            )
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "studio_exists",
            "A studio with this id already exists; refusing to replace it.",
            details={
                "studio_id": studio,
                "found_in_workspace_id": source,
                "existing_template_sha256": existing.get("template_sha256"),
                "new_template_sha256": template_sha256,
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    body = {
        "key": {"studioId": studio, "workspaceId": workspace},
        "displayName": name,
        "description": description,
        "template": {"type": template_type, "body": template_body},
    }
    token_args = {
        "workspace_id": workspace,
        "studio_id": studio,
        "display_name": name,
        "description": description,
        "template_type": template_type,
        "template_sha256": template_sha256,
    }
    fields: dict[str, Any] = {
        "operation": "create_studio",
        "studio_id": studio,
        "display_name": name,
        "description": description,
        "template_type": template_type,
        "template_bytes": len(template_bytes),
        "template_sha256": template_sha256,
        "disruptive": False,
        "request_body": body,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            _STUDIO_SOURCE,
            outcome="preview",
            workspace_id=workspace,
            fields=fields,
            next_action="Re-call with confirm=True and this preview_token.",
            warnings=warnings,
        )

    token_error = check_preview_token(tool, token_args, preview_token_value)
    if token_error:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
            warnings=warnings,
        )

    return _post_studio_config(
        tool,
        datadict,
        workspace,
        body,
        fields,
        next_action="build_cvp_workspace",
        warnings=warnings,
    )


# --- delete -----------------------------------------------------------------


def delete_cvp_studio(
    datadict: dict[str, Any],
    workspace_id: str,
    studio_id: str,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Remove a studio in a pending draft workspace (``POST StudioConfig``).

    The removal is expressed as ``remove: true`` on ``StudioConfig`` — never a
    ChangeControl write. The studio must exist (workspace overlay or mainline),
    must not be ``immutable`` / ``from_package``, and must not be ``in_use``:
    the documented sequence is unassign tags → remove studio in the *same*
    workspace → build → review and submit in the CVP UI.
    """
    tool = "delete_cvp_studio"
    if not writes_enabled():
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    workspace, studio, refusal = _validate_ids(tool, workspace_id, studio_id)
    if refusal is not None:
        return refusal

    warnings, refusal = _pending_workspace_refusal(tool, datadict, workspace, "delete")
    if refusal is not None:
        return refusal

    existing, source, status, studio_warnings = _read_studio_anywhere(
        datadict, studio, workspace
    )
    warnings = warnings + studio_warnings
    if status == "read_failed":
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "studio_read_failed",
            "Studio preflight GET did not return 200; refusing to delete.",
            details={"studio_id": studio, "workspace_id": source},
            workspace_id=workspace,
            warnings=warnings,
        )
    if existing is None:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "studio_not_found",
            "Studio does not exist in the workspace or mainline; nothing to remove.",
            details={"studio_id": studio},
            workspace_id=workspace,
            warnings=warnings,
        )

    flag = _flag_refusal(existing)
    if flag:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            flag,
            "Refusing to remove an immutable or packaged studio.",
            details={"studio_id": studio, "found_in_workspace_id": source},
            workspace_id=workspace,
            warnings=warnings,
        )
    if existing.get("in_use") is True:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            "studio_in_use",
            "Studio is in use; unassign its tags in this workspace before removing it.",
            details={"studio_id": studio, "found_in_workspace_id": source},
            workspace_id=workspace,
            warnings=warnings,
        )

    body = {
        "key": {"studioId": studio, "workspaceId": workspace},
        "remove": True,
    }
    token_args = {"workspace_id": workspace, "studio_id": studio}
    fields: dict[str, Any] = {
        "operation": "delete_studio",
        "studio_id": studio,
        "found_in_workspace_id": source,
        "display_name": existing.get("display_name"),
        "template_sha256": existing.get("template_sha256"),
        "request_body": body,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            _STUDIO_SOURCE,
            outcome="preview",
            workspace_id=workspace,
            fields=fields,
            next_action="Re-call with confirm=True and this preview_token.",
            warnings=warnings,
        )

    token_error = check_preview_token(tool, token_args, preview_token_value)
    if token_error:
        return _refused(
            tool,
            _STUDIO_SOURCE,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
            warnings=warnings,
        )

    return _post_studio_config(
        tool,
        datadict,
        workspace,
        body,
        fields,
        next_action="build_cvp_workspace",
        warnings=warnings,
    )
