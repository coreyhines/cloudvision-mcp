"""Studios AssignedTags read + compare-and-set assign (Phase 2.1 bucket 1a).

Library functions only; nothing here registers an MCP tool. See
``docs/studios-phase2-spec.md`` ("get_cvp_studio_assigned_tags",
"assign_cvp_studio_tags").

:func:`get_cvp_studio_assigned_tags` is a **read**: it runs without the writes
env gate. ``GET AssignedTags/all`` is still unprobed on this tenant, so a 404 or
an empty result is reported as ``coverage="none"`` plus an
``assigned_tags_unavailable`` warning. No query is ever invented.

:func:`assign_cvp_studio_tags` **replaces** a studio's whole tag query and so
follows the same fail-close shape as ``studios_write``:

* refuses with ``writes_disabled`` unless :func:`writes_enabled`,
* refuses an empty ``query`` (2.1 has no unassign-all),
* requires ``expected_current_query`` and compares it against a fresh GET —
  any mismatch, or a GET that did not resolve, refuses before the POST body is
  built,
* returns a ``preview_token`` when ``confirm=False`` (no mutating HTTP at all),
* performs **one** POST when ``confirm=True`` and the caller echoes back the
  matching ``preview_token``.

Envelope helpers are copied from ``studios_write`` on purpose: that module owns
the 2.0 slice and is not edited by this one.
"""

from __future__ import annotations

import logging
from typing import Any

from cvp_mcp.grpc.config import _cvp_https_base
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.resource_write import post_resource_config
from cvp_mcp.grpc.studios import get_cvp_workspace
from cvp_mcp.grpc.uri_fetch import get_ndjson_all_values_with_bearer
from cvp_mcp.write_access import (
    check_preview_token,
    preview_token,
    validate_workspace_id,
    writes_enabled,
)

ASSIGNED_TAGS_ALL_PATH = "/api/resources/studio/v1/AssignedTags/all"
ASSIGNED_TAGS_CONFIG_PATH = "/api/resources/studio/v1/AssignedTagsConfig"

WORKSPACE_STATE_PENDING = "WORKSPACE_STATE_PENDING"

_MAINLINE_WORKSPACE_ID = ""
_TAGS_SOURCE = "resource_api:studio.v1.assigned_tags"

# Same ceiling studios.py uses for Resource API ``/all`` streams on this tenant.
_NDJSON_MAX_BYTES = 96_000_000

# Wire field names seen for the assigned query. Checked in order.
_QUERY_FIELDS: tuple[str, ...] = ("query", "tagQuery", "tag_query")

# ndjson helper errors that mean "the endpoint told us nothing", as opposed to
# a transport/auth failure. Both map to ``assigned_tags_unavailable``.
_UNAVAILABLE_ERRORS: frozenset[str] = frozenset({"http_error:404", "empty_response"})


# --- envelope helpers (copied from studios_write; that module is not edited) -


def _refused(
    tool: str,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Refusal envelope. ``coverage`` is ``none`` and no HTTP mutate happened."""
    logging.info("%s: workspace=%s outcome=refused error=%s", tool, workspace_id, code)
    return tool_envelope(
        data_source=_TAGS_SOURCE,
        coverage="none",
        obj={
            "outcome": "refused",
            "dry_run": True,
            "error": {
                "code": code,
                "message": message,
                "details": dict(details or {}),
            },
            "workspace_id": workspace_id,
            "next_action": None,
        },
        warnings=list(warnings or []),
    )


def _outcome(
    tool: str,
    *,
    outcome: str,
    workspace_id: str,
    fields: dict[str, Any],
    next_action: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Preview (``dry_run``) or accepted envelope."""
    logging.info("%s: workspace=%s outcome=%s", tool, workspace_id, outcome)
    obj: dict[str, Any] = {
        "outcome": outcome,
        "dry_run": outcome == "preview",
        "error": None,
        "workspace_id": workspace_id,
        "next_action": next_action,
    }
    obj.update(fields)
    return tool_envelope(
        data_source=_TAGS_SOURCE,
        coverage="full",
        obj=obj,
        warnings=list(warnings or []),
    )


def _credentials(datadict: dict[str, Any]) -> tuple[str, str, str | None]:
    """Return ``(token, base_url, missing_code)``; no HTTP when a piece is absent."""
    token = (datadict.get("cvtoken") or "").strip()
    base = _cvp_https_base(str(datadict.get("cvp") or ""))
    if not token:
        return "", base, "missing_token"
    if not base:
        return token, "", "missing_cvp"
    return token, base, None


def _resource_time(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    for scope in (response, response.get("result")):
        if isinstance(scope, dict) and isinstance(scope.get("time"), str):
            return scope["time"]
    return None


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return value["value"]
    return ""


def _read_workspace(
    datadict: dict[str, Any], workspace_id: str
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """GET one workspace.

    Returns ``(summary, status, warnings)`` where ``status`` is ``None`` when
    found, ``"not_found"`` on HTTP 404, else ``"read_failed"``. Anything but a
    clean 200 fails closed: callers must not mutate on a failed preflight.
    """
    env = get_cvp_workspace(datadict, workspace_id)
    obj = env.get("object") or {}
    warnings = [w for w in (env.get("warnings") or []) if isinstance(w, str)]
    if (
        env.get("coverage") == "full"
        and isinstance(obj, dict)
        and obj.get("workspace_id")
    ):
        return obj, None, warnings
    if any(w == "http_error:404" for w in warnings):
        return None, "not_found", warnings
    return None, "read_failed", warnings


# --- AssignedTags read ------------------------------------------------------


def _row_query(value: dict[str, Any]) -> str:
    """The assigned query on one AssignedTags row, ``""`` when absent."""
    for field in _QUERY_FIELDS:
        text = _as_str(value.get(field))
        if text:
            return text
    return ""


def _fetch_assigned_tags(
    datadict: dict[str, Any], studio_id: str, workspace_id: str
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    """GET ``AssignedTags/all`` and client-filter to one studio + workspace.

    Returns ``(items, status, warnings)``. ``status`` is ``None`` when at least
    one row matched, ``"unavailable"`` on 404/empty/no match, and
    ``"read_failed"`` for any other transport error. A query is never
    synthesized: an unavailable read yields no items.
    """
    token, base, missing = _credentials(datadict)
    if missing:
        return [], "read_failed", [missing]

    uri = f"{base}{ASSIGNED_TAGS_ALL_PATH}"
    values, err, warnings = get_ndjson_all_values_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    warnings = [w for w in (warnings or []) if isinstance(w, str)]
    if err:
        warnings.append(err)
        status = "unavailable" if err in _UNAVAILABLE_ERRORS else "read_failed"
        return [], status, warnings

    items: list[dict[str, Any]] = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        key = value.get("key")
        if not isinstance(key, dict):
            continue
        row_sid = _as_str(key.get("studioId") or key.get("studio_id"))
        row_wid = _as_str(key.get("workspaceId") or key.get("workspace_id"))
        if row_sid != studio_id or row_wid != workspace_id:
            continue
        items.append(
            {
                "studio_id": row_sid,
                "workspace_id": row_wid,
                "query": _row_query(value),
            }
        )

    if not items:
        return [], "unavailable", warnings
    return items, None, warnings


def get_cvp_studio_assigned_tags(
    datadict: dict[str, Any],
    studio_id: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Assigned tag query for one studio in one workspace (mainline is ``""``).

    Best-effort read: ``GET AssignedTags/all`` is unprobed, so a 404, an empty
    stream, or no matching row all return ``coverage="none"`` with an
    ``assigned_tags_unavailable`` warning and ``items: []``.
    """
    warnings: list[str] = []
    sid = (studio_id or "").strip()
    wid = _MAINLINE_WORKSPACE_ID if workspace_id is None else str(workspace_id)
    if not sid:
        return tool_envelope(
            data_source=_TAGS_SOURCE,
            coverage="none",
            items=[],
            warnings=["missing_studio_id"],
        )

    items, status, read_warnings = _fetch_assigned_tags(datadict, sid, wid)
    warnings.extend(read_warnings)
    if status == "unavailable":
        warnings.append("assigned_tags_unavailable")
    if status:
        return tool_envelope(
            data_source=_TAGS_SOURCE,
            coverage="none",
            items=[],
            warnings=warnings,
        )
    return tool_envelope(
        data_source=_TAGS_SOURCE,
        coverage="full",
        items=items,
        warnings=warnings,
    )


# --- assign CAS -------------------------------------------------------------


def assign_cvp_studio_tags(
    datadict: dict[str, Any],
    studio_id: str,
    workspace_id: str,
    query: str,
    expected_current_query: str,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Compare-and-set a studio's whole assigned tag query.

    ``expected_current_query`` is required and is checked against a fresh
    ``AssignedTags`` GET; a mismatch, an ambiguous result, or a GET that did not
    resolve refuses before the POST body exists. An empty ``query`` is refused
    outright — 2.1 deliberately has no unassign-all. The write target must be a
    pending ``ws-mcp-*`` draft.
    """
    tool = "assign_cvp_studio_tags"
    if not writes_enabled():
        return _refused(
            tool,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    sid = (studio_id or "").strip()
    if not sid:
        return _refused(
            tool,
            "studio_id_required",
            "studio_id is required to assign tags.",
        )

    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            tool,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            workspace_id=workspace or None,
        )

    if not isinstance(query, str) or not query.strip():
        return _refused(
            tool,
            "empty_query_forbidden",
            "An empty tag query would unassign every device; refusing.",
            details={"studio_id": sid},
            workspace_id=workspace,
        )
    new_query = query.strip()

    if not isinstance(expected_current_query, str) or not expected_current_query:
        return _refused(
            tool,
            "expected_current_query_required",
            "expected_current_query is required; read it with "
            "get_cvp_studio_assigned_tags first.",
            details={"studio_id": sid},
            workspace_id=workspace,
        )
    expected = expected_current_query

    _, _, missing = _credentials(datadict)
    if missing:
        return _refused(
            tool,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

    summary, ws_status, ws_warnings = _read_workspace(datadict, workspace)
    if ws_status == "not_found":
        return _refused(
            tool,
            "workspace_not_found",
            "Workspace does not exist; create a draft first.",
            workspace_id=workspace,
            warnings=ws_warnings,
        )
    if ws_status == "read_failed":
        return _refused(
            tool,
            "workspace_read_failed",
            "Workspace GET failed; refusing to assign tags.",
            workspace_id=workspace,
            warnings=ws_warnings,
        )
    state = _as_str((summary or {}).get("state")).strip()
    if state != WORKSPACE_STATE_PENDING:
        return _refused(
            tool,
            "workspace_not_pending",
            "Tag assignment is only allowed on pending draft workspaces.",
            details={"state": state},
            workspace_id=workspace,
            warnings=ws_warnings,
        )

    items, tag_status, tag_warnings = _fetch_assigned_tags(datadict, sid, workspace)
    warnings = ws_warnings + tag_warnings
    if tag_status == "unavailable":
        warnings.append("assigned_tags_unavailable")
        return _refused(
            tool,
            "assigned_tags_unavailable",
            "AssignedTags GET returned nothing for this studio and workspace; "
            "the current query cannot be confirmed, so the assign is refused.",
            details={"studio_id": sid},
            workspace_id=workspace,
            warnings=warnings,
        )
    if tag_status:
        return _refused(
            tool,
            "assigned_tags_read_failed",
            "AssignedTags GET failed; refusing to assign tags.",
            details={"studio_id": sid},
            workspace_id=workspace,
            warnings=warnings,
        )
    if len(items) != 1:
        return _refused(
            tool,
            "assigned_tags_ambiguous",
            f"Expected exactly one AssignedTags row, found {len(items)}.",
            details={"studio_id": sid, "matches": len(items)},
            workspace_id=workspace,
            warnings=warnings,
        )

    current = items[0]["query"]
    if current != expected:
        return _refused(
            tool,
            "current_query_mismatch",
            "Current tag query does not match expected_current_query.",
            details={
                "studio_id": sid,
                "current_query": current,
                "expected_current_query": expected,
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    body = {
        "key": {"studioId": sid, "workspaceId": workspace},
        "query": new_query,
    }
    token_args = {
        "studio_id": sid,
        "workspace_id": workspace,
        "query": new_query,
        "expected_current_query": expected,
    }
    fields: dict[str, Any] = {
        "operation": "assign_tags",
        "studio_id": sid,
        "before_query": current,
        "after_query": new_query,
        # Resolving a tag query to devices is unprobed; never guess the blast
        # radius. Callers preview the targets in the CVP UI.
        "target_preview": None,
        "request_body": body,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            outcome="preview",
            workspace_id=workspace,
            fields=fields,
            next_action="Re-call with confirm=True and this preview_token.",
            warnings=warnings + ["target_preview_unresolved"],
        )

    token_error = check_preview_token(tool, token_args, preview_token_value)
    if token_error:
        return _refused(
            tool,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
            warnings=warnings,
        )

    cvtoken, base, _ = _credentials(datadict)
    response, err = post_resource_config(
        base,
        ASSIGNED_TAGS_CONFIG_PATH,
        body,
        cvtoken,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        return _refused(
            tool,
            "resource_write_failed",
            "AssignedTagsConfig POST failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    return _outcome(
        tool,
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action="get_cvp_studio_assigned_tags then build_cvp_workspace",
        warnings=warnings,
    )
