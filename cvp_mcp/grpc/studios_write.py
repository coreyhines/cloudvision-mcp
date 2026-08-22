"""Studios Phase 2.0 write library: workspace drafts + description CAS.

Library functions only. Nothing here is registered as an MCP tool; registration
stays behind the env gate in ``cloudvision_mcp.py`` (see
``docs/studios-phase2-spec.md``). Every function:

* refuses with ``writes_disabled`` unless :func:`writes_enabled`,
* performs read-only preflights and returns a ``preview_token`` when
  ``confirm=False`` (no mutating HTTP at all),
* performs **one** mutating HTTP request when ``confirm=True`` and the caller
  echoes back the matching ``preview_token``.

Refusals happen before any mutate request is built. Mainline (``workspaceId``
``""``) is never a write target: :func:`validate_workspace_id` requires the
``ws-mcp-`` prefix.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any

from cvp_mcp.grpc.config import _cvp_https_base
from cvp_mcp.grpc.envelope import tool_envelope
from cvp_mcp.grpc.resource_write import (
    REQUEST_START_BUILD,
    delete_resource_config,
    post_resource_config,
)
from cvp_mcp.grpc.studios import get_cvp_studio, get_cvp_workspace
from cvp_mcp.grpc.uri_fetch import get_ndjson_all_values_with_bearer
from cvp_mcp.write_access import (
    check_preview_token,
    preview_token,
    validate_workspace_id,
    writes_enabled,
)

# The one studio 2.0 may patch. Not a caller argument on purpose.
ACCESS_INTERFACE_STUDIO_ID = "studio-campus-access-interfaces"

WORKSPACE_CONFIG_PATH = "/api/resources/workspace/v1/WorkspaceConfig"
INPUTS_CONFIG_PATH = "/api/resources/studio/v1/InputsConfig"

WORKSPACE_STATE_PENDING = "WORKSPACE_STATE_PENDING"

# Only states observed on this tenant. Anything else is treated as in-flight so
# an unknown state can never unblock a second build.
TERMINAL_BUILD_STATES: frozenset[str] = frozenset(
    {
        "BUILD_STATE_SUCCESS",
        "BUILD_STATE_FAIL",
        "BUILD_STATE_CANCELED",
    }
)
_TERMINAL_RESPONSE_STATUSES: frozenset[str] = frozenset(
    {
        "RESPONSE_STATUS_SUCCESS",
        "RESPONSE_STATUS_FAIL",
        "RESPONSE_STATUS_FAILURE",
    }
)
_RESPONSE_STATE_KEYS: tuple[str, ...] = (
    "status",
    "state",
    "buildState",
    "build_state",
    "buildStatus",
    "build_status",
)

# EOS lint. A description-only patch must never introduce any of these.
_DISRUPTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("no_shutdown", r"no\s+shutdown"),
    ("shutdown", r"shutdown"),
    ("no_interface", r"no\s+interface"),
    ("reload", r"reload"),
    ("write_erase", r"write\s+erase"),
)

_WORKSPACE_SOURCE = "resource_api:workspace.v1"
_INPUTS_SOURCE = "resource_api:studio.v1.inputs"

_NDJSON_MAX_BYTES = 96_000_000

# Sentinel for "key absent on one side" in the structural tree diff.
_MISSING = object()


# --- envelope helpers -------------------------------------------------------


def _refused(
    tool: str,
    data_source: str,
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
        data_source=data_source,
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
    data_source: str,
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
        data_source=data_source,
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


# --- workspace preflight ----------------------------------------------------


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


def _response_is_terminal(entry: Any) -> bool:
    """True only when a ``responses.values`` entry names a terminal build state."""
    if not isinstance(entry, dict):
        return False
    for key in _RESPONSE_STATE_KEYS:
        value = entry.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text in TERMINAL_BUILD_STATES or text in _TERMINAL_RESPONSE_STATUSES:
            return True
    return False


def _non_terminal_responses(summary: dict[str, Any]) -> list[str]:
    """Request ids in ``responses.values`` whose build is not terminal."""
    responses = summary.get("responses")
    if not isinstance(responses, dict):
        return []
    values = responses.get("values")
    if not isinstance(values, dict):
        return []
    return [rid for rid, entry in values.items() if not _response_is_terminal(entry)]


# --- inputs document --------------------------------------------------------


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return value["value"]
    return ""


def _is_root_path(key: dict[str, Any]) -> bool:
    """True for the root Inputs key: ``path`` absent, ``{}`` or ``values: []``."""
    path = key.get("path")
    if path is None or path == {}:
        return True
    if not isinstance(path, dict):
        return False
    values = path.get("values")
    return values is None or (isinstance(values, list) and not values)


def _parse_inputs(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def _load_root_inputs(
    datadict: dict[str, Any], workspace_id: str
) -> tuple[dict[str, Any] | None, str, str | None, list[str]]:
    """Fetch the root Inputs document for the access-interface studio.

    Prefers the target workspace overlay and falls back to mainline
    (``workspaceId=""``), matching "first write copies mainline, later writes
    read the overlay". Returns ``(document, source_workspace_id, error, warnings)``.
    """
    token, base, missing = _credentials(datadict)
    if missing:
        return None, "", "preflight_failed", [missing]
    uri = f"{base}/api/resources/studio/v1/Inputs/all"
    values, err, warnings = get_ndjson_all_values_with_bearer(
        uri,
        token,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
        max_bytes=_NDJSON_MAX_BYTES,
    )
    warnings = list(warnings or [])
    if err:
        warnings.append(err)
        return None, "", "preflight_failed", warnings
    # Spec: a warning is never enough to proceed. Truncation or skipped NDJSON
    # would POST a partial tree (full-document replace).
    for warning in warnings:
        if "truncated_to_" in warning or "ndjson_skip_invalid_line" in warning:
            return None, "", "preflight_failed", warnings

    rows: dict[str, Any] = {}
    for value in values or []:
        if not isinstance(value, dict):
            continue
        key = value.get("key")
        if not isinstance(key, dict):
            continue
        studio_id = _as_str(key.get("studioId") or key.get("studio_id"))
        row_ws = _as_str(key.get("workspaceId") or key.get("workspace_id"))
        if studio_id != ACCESS_INTERFACE_STUDIO_ID:
            continue
        if row_ws not in (workspace_id, ""):
            continue
        if not _is_root_path(key):
            continue
        rows[row_ws] = value.get("inputs")

    for source in (workspace_id, ""):
        if source not in rows:
            continue
        document = _parse_inputs(rows[source])
        if not isinstance(document, (dict, list)):
            return None, source, "inputs_path_unresolved", warnings
        return document, source, None, warnings
    return None, "", "inputs_path_unresolved", warnings


def _find_locator_rows(
    obj: Any, locator: str, path: str = "$"
) -> list[tuple[str, dict[str, Any]]]:
    """Every dict in the tree whose ``tags.query`` equals ``locator``."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(obj, dict):
        tags = obj.get("tags")
        if isinstance(tags, dict) and _as_str(tags.get("query")) == locator:
            found.append((path, obj))
        for key, value in obj.items():
            found.extend(_find_locator_rows(value, locator, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            found.extend(_find_locator_rows(item, locator, f"{path}[{index}]"))
    return found


def _resolve_adapter_details(
    row: dict[str, Any], row_path: str
) -> tuple[dict[str, Any] | None, str]:
    """Locate ``adapterDetails`` on a matched row (directly or under ``inputs``)."""
    for prefix in ((), ("inputs",), ("input",)):
        scope: Any = row
        scope_path = row_path
        for step in prefix:
            if not isinstance(scope, dict):
                scope = None
                break
            scope = scope.get(step)
            scope_path = f"{scope_path}.{step}"
        if not isinstance(scope, dict):
            continue
        for name in ("adapterDetails", "adapter_details"):
            details = scope.get(name)
            if isinstance(details, dict):
                return details, f"{scope_path}.{name}"
    return None, row_path


def _changed_leaf_paths(
    before: Any, after: Any, path: str = "$", out: list[str] | None = None
) -> list[str]:
    """Structural diff: JSON paths of every leaf that differs."""
    if out is None:
        out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            _changed_leaf_paths(
                before.get(key, _MISSING),
                after.get(key, _MISSING),
                f"{path}.{key}",
                out,
            )
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            out.append(path)
        else:
            for index, (b, a) in enumerate(zip(before, after)):
                _changed_leaf_paths(b, a, f"{path}[{index}]", out)
    elif before != after:
        out.append(path)
    return out


def _disruptive_hits(text: str) -> list[str]:
    """EOS lint names present in ``text`` (case-insensitive)."""
    hits: list[str] = []
    for name, pattern in _DISRUPTIVE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(name)
    return hits


# --- create -----------------------------------------------------------------


def create_cvp_workspace(
    datadict: dict[str, Any],
    workspace_id: str,
    display_name: str,
    description: str = "",
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Create a draft workspace (``POST WorkspaceConfig``).

    Preflight GETs the workspace: an existing id refuses with
    ``workspace_id_exists`` and a failed GET refuses with
    ``workspace_read_failed`` — neither reaches POST.
    """
    tool = "create_cvp_workspace"
    if not writes_enabled():
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            workspace_id=workspace or None,
        )

    _, _, missing = _credentials(datadict)
    if missing:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

    summary, status, warnings = _read_workspace(datadict, workspace)
    if status == "read_failed":
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_read_failed",
            "Workspace preflight GET did not return 200; refusing to create.",
            details={"warnings": warnings},
            workspace_id=workspace,
            warnings=warnings,
        )
    if summary is not None:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_id_exists",
            "A workspace with this id already exists.",
            details={"state": summary.get("state")},
            workspace_id=workspace,
            warnings=warnings,
        )

    body = {
        "key": {"workspaceId": workspace},
        "displayName": display_name,
        "description": description,
    }
    token_args = {
        "workspace_id": workspace,
        "display_name": display_name,
        "description": description,
    }
    fields: dict[str, Any] = {
        "operation": "create_workspace",
        "display_name": display_name,
        "description": description,
        "request_body": body,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            _WORKSPACE_SOURCE,
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
            _WORKSPACE_SOURCE,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
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
            tool,
            _WORKSPACE_SOURCE,
            "resource_write_failed",
            "WorkspaceConfig POST failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    return _outcome(
        tool,
        _WORKSPACE_SOURCE,
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action="get_cvp_workspace",
        warnings=warnings,
    )


# --- delete -----------------------------------------------------------------


def delete_cvp_workspace(
    datadict: dict[str, Any],
    workspace_id: str,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Delete a *pending* draft workspace (``DELETE WorkspaceConfig``).

    A missing workspace, an unknown state, or any state other than
    ``WORKSPACE_STATE_PENDING`` refuses before the DELETE is built.
    """
    tool = "delete_cvp_workspace"
    if not writes_enabled():
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            workspace_id=workspace or None,
        )

    _, _, missing = _credentials(datadict)
    if missing:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

    summary, status, warnings = _read_workspace(datadict, workspace)
    if status == "not_found":
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_not_found",
            "Workspace does not exist; nothing to delete.",
            workspace_id=workspace,
            warnings=warnings,
        )
    if summary is None:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_read_failed",
            "Workspace preflight GET did not return 200; refusing to delete.",
            details={"warnings": warnings},
            workspace_id=workspace,
            warnings=warnings,
        )

    state = _as_str(summary.get("state")).strip()
    if not state:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_state_unknown",
            "Workspace state is unknown; refusing to delete.",
            workspace_id=workspace,
            warnings=warnings,
        )
    if state != WORKSPACE_STATE_PENDING:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_not_pending",
            "Only WORKSPACE_STATE_PENDING drafts may be deleted.",
            details={"state": state},
            workspace_id=workspace,
            warnings=warnings,
        )

    token_args = {"workspace_id": workspace}
    fields: dict[str, Any] = {
        "operation": "delete_workspace",
        "state": state,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            _WORKSPACE_SOURCE,
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
            _WORKSPACE_SOURCE,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
            warnings=warnings,
        )

    cvtoken, base, _ = _credentials(datadict)
    response, err = delete_resource_config(
        base,
        WORKSPACE_CONFIG_PATH,
        {"key.workspaceId": workspace},
        cvtoken,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "resource_write_failed",
            "WorkspaceConfig DELETE failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    return _outcome(
        tool,
        _WORKSPACE_SOURCE,
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action="get_cvp_workspaces",
        warnings=warnings,
    )


# --- build ------------------------------------------------------------------


def build_cvp_workspace(
    datadict: dict[str, Any],
    workspace_id: str,
    request_id: str | None = None,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Start a workspace build (``REQUEST_START_BUILD``, hard-coded here).

    The preview generates a UUIDv4 ``request_id`` so the caller can pass the
    same id back on confirm. That id is bound into the ``preview_token``;
    ``confirm=True`` must echo both the token and that ``request_id``. HTTP 200
    is not build success: poll with the Phase 1 read tools.
    """
    tool = "build_cvp_workspace"
    if not writes_enabled():
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            workspace_id=workspace or None,
        )

    if request_id is not None and (
        not isinstance(request_id, str) or not request_id.strip()
    ):
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "invalid_request_id",
            "request_id must be a non-empty string when supplied.",
            workspace_id=workspace,
        )

    _, _, missing = _credentials(datadict)
    if missing:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

    summary, status, warnings = _read_workspace(datadict, workspace)
    if status == "not_found":
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_not_found",
            "Workspace does not exist; nothing to build.",
            workspace_id=workspace,
            warnings=warnings,
        )
    if summary is None:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_read_failed",
            "Workspace preflight GET did not return 200; refusing to build.",
            details={"warnings": warnings},
            workspace_id=workspace,
            warnings=warnings,
        )

    state = _as_str(summary.get("state")).strip()
    if not state:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_state_unknown",
            "Workspace state is unknown; refusing to build.",
            workspace_id=workspace,
            warnings=warnings,
        )
    if state != WORKSPACE_STATE_PENDING:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "workspace_not_pending",
            "Only WORKSPACE_STATE_PENDING drafts may be built.",
            details={"state": state},
            workspace_id=workspace,
            warnings=warnings,
        )

    pending = _non_terminal_responses(summary)
    if pending:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "build_in_progress",
            "A build for this workspace has not reached a terminal state.",
            details={"request_ids": pending},
            workspace_id=workspace,
            warnings=warnings,
        )

    # Generated on preview only so the caller can echo it back; a dry-run id is
    # never reused implicitly on confirm.
    if confirm and not (request_id or "").strip():
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            "invalid_request_id",
            "confirm=True must pass the request_id from the preview.",
            workspace_id=workspace,
            warnings=warnings,
        )
    effective_request_id = (request_id or "").strip() or str(uuid.uuid4())
    body = {
        "key": {"workspaceId": workspace},
        "request": REQUEST_START_BUILD,
        "requestParams": {"requestId": effective_request_id},
    }
    token_args = {"workspace_id": workspace, "request_id": effective_request_id}
    fields: dict[str, Any] = {
        "operation": "build",
        "done": False,
        "request": REQUEST_START_BUILD,
        "request_id": effective_request_id,
        "request_body": body,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            _WORKSPACE_SOURCE,
            outcome="preview",
            workspace_id=workspace,
            fields=fields,
            next_action="Re-call with confirm=True, this preview_token and request_id.",
            warnings=warnings,
        )

    token_error = check_preview_token(tool, token_args, preview_token_value)
    if token_error:
        return _refused(
            tool,
            _WORKSPACE_SOURCE,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
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
            tool,
            _WORKSPACE_SOURCE,
            "resource_write_failed",
            "WorkspaceConfig build POST failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    return _outcome(
        tool,
        _WORKSPACE_SOURCE,
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action="poll get_cvp_workspace then get_cvp_workspace_build",
        warnings=warnings,
    )


# --- description CAS --------------------------------------------------------


def set_cvp_access_interface_description(
    datadict: dict[str, Any],
    workspace_id: str,
    device_id: str,
    interface: str,
    expected_current_description: str,
    new_description: str,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Compare-and-set one ``adapterDetails.description`` in the access studio.

    Follows the five-step write shape: GET the root Inputs document, locate the
    unique row whose ``tags.query`` is ``interface:<interface>@<device_id>``,
    CAS the description, patch a deep copy, prove the structural diff is exactly
    that one leaf, then POST the whole tree back at ``path.values: []`` (this
    studio has no per-port Inputs key).
    """
    tool = "set_cvp_access_interface_description"
    if not writes_enabled():
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            workspace_id=workspace or None,
        )

    device = (device_id or "").strip()
    port = (interface or "").strip()
    if not device or not port:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_path_unresolved",
            "device_id and interface are both required to build the locator.",
            details={"device_id": device, "interface": port},
            workspace_id=workspace,
        )
    locator = f"interface:{port}@{device}"

    summary, ws_status, ws_warnings = _read_workspace(datadict, workspace)
    if ws_status == "not_found":
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "workspace_not_found",
            "Workspace does not exist; create a draft first.",
            workspace_id=workspace,
            warnings=ws_warnings,
        )
    if ws_status == "read_failed":
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "workspace_read_failed",
            "Workspace GET failed; refusing to write Inputs.",
            workspace_id=workspace,
            warnings=ws_warnings,
        )
    state = (summary or {}).get("state") or ""
    if state != WORKSPACE_STATE_PENDING:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "workspace_not_pending",
            "Description writes are only allowed on pending draft workspaces.",
            details={"state": state},
            workspace_id=workspace,
            warnings=ws_warnings,
        )

    studio_env = get_cvp_studio(datadict, ACCESS_INTERFACE_STUDIO_ID, "")
    studio_obj = studio_env.get("object") or {}
    if studio_env.get("coverage") != "full" or not isinstance(studio_obj, dict):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "preflight_failed",
            "Studio GET failed; refusing Inputs write.",
            workspace_id=workspace,
            warnings=list(studio_env.get("warnings") or []),
        )
    if studio_obj.get("immutable") is True or studio_obj.get("from_package") is True:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            (
                "studio_from_package"
                if studio_obj.get("from_package") is True
                else "studio_immutable"
            ),
            "Refusing Inputs write on an immutable or packaged studio.",
            workspace_id=workspace,
        )

    expected = (
        "" if expected_current_description is None else expected_current_description
    )
    replacement = "" if new_description is None else new_description
    if not isinstance(expected, str) or not isinstance(replacement, str):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "current_description_mismatch",
            "Descriptions must be strings.",
            workspace_id=workspace,
        )

    lint_hits = _disruptive_hits(replacement)
    if lint_hits:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "disruptive_content_forbidden",
            "The new description contains EOS-disruptive text.",
            details={"matched": lint_hits},
            workspace_id=workspace,
        )

    document, source_workspace, load_error, warnings = _load_root_inputs(
        datadict, workspace
    )
    if load_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            load_error,
            "Could not read the access-interface Inputs document.",
            details={"studio_id": ACCESS_INTERFACE_STUDIO_ID},
            workspace_id=workspace,
            warnings=warnings,
        )

    matches = _find_locator_rows(document, locator)
    if len(matches) != 1:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_path_not_found",
            f"Expected exactly one row for {locator}, found {len(matches)}.",
            details={"locator": locator, "matches": len(matches)},
            workspace_id=workspace,
            warnings=warnings,
        )
    row_path, row = matches[0]

    details, details_path = _resolve_adapter_details(row, row_path)
    if details is None:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_path_unresolved",
            "Matched row has no adapterDetails object.",
            details={"locator": locator, "row_path": row_path},
            workspace_id=workspace,
            warnings=warnings,
        )

    raw_current = details.get("description")
    current = "" if raw_current is None else raw_current
    if not isinstance(current, str):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "current_description_mismatch",
            "Current description is not a string.",
            details={"locator": locator},
            workspace_id=workspace,
            warnings=warnings,
        )
    if current != expected:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "current_description_mismatch",
            "Current description does not match expected_current_description.",
            details={
                "locator": locator,
                "current_description": current,
                "expected_current_description": expected,
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    leaf_path = f"{details_path}.description"
    patched = copy.deepcopy(document)
    patched_matches = _find_locator_rows(patched, locator)
    patched_details = None
    if len(patched_matches) == 1:
        patched_row_path, patched_row = patched_matches[0]
        patched_details, _ = _resolve_adapter_details(patched_row, patched_row_path)
    if patched_details is None:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_path_unresolved",
            "Could not re-locate adapterDetails in the copied tree.",
            details={"locator": locator, "row_path": row_path},
            workspace_id=workspace,
            warnings=warnings,
        )
    patched_details["description"] = replacement

    # Serialize both sides (sorted keys) and diff the reparsed objects: this
    # catches shared references and any non-round-tripping value, not just the
    # leaf we meant to touch.
    before_json = json.dumps(document, sort_keys=True, default=str)
    after_json = json.dumps(patched, sort_keys=True, default=str)
    changed = _changed_leaf_paths(json.loads(before_json), json.loads(after_json))
    if changed != [leaf_path]:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "tree_diff_not_description_only",
            "The patched tree differs from the current tree in more than the description leaf.",
            details={
                "locator": locator,
                "expected_leaf": leaf_path,
                "changed_leaves": changed[:10],
                "changed_count": len(changed),
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    introduced = sorted(
        set(_disruptive_hits(after_json)) - set(_disruptive_hits(before_json))
    )
    if introduced:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "disruptive_content_forbidden",
            "The patched inputs document introduces EOS-disruptive text.",
            details={"matched": introduced},
            workspace_id=workspace,
            warnings=warnings,
        )

    body = {
        "key": {
            "studioId": ACCESS_INTERFACE_STUDIO_ID,
            "workspaceId": workspace,
            "path": {"values": []},
        },
        "inputs": json.dumps(patched),
    }
    token_args = {
        "workspace_id": workspace,
        "device_id": device,
        "interface": port,
        "expected_current_description": expected,
        "new_description": replacement,
    }
    fields: dict[str, Any] = {
        "operation": "set_description",
        "studio_id": ACCESS_INTERFACE_STUDIO_ID,
        "device_id": device,
        "interface": port,
        "locator": locator,
        "inputs_source_workspace_id": source_workspace,
        "before_description": current,
        "after_description": replacement,
        "changed_leaves": 1,
        "changed_leaf_path": leaf_path,
        "posted_at_root": True,
        "disruptive": False,
        "resource_time": None,
    }

    if not confirm:
        fields["preview_token"] = preview_token(tool, token_args)
        return _outcome(
            tool,
            _INPUTS_SOURCE,
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
            _INPUTS_SOURCE,
            token_error,
            "confirm=True requires the preview_token from a matching dry run.",
            workspace_id=workspace,
            warnings=warnings,
        )

    cvtoken, base, _ = _credentials(datadict)
    response, err = post_resource_config(
        base,
        INPUTS_CONFIG_PATH,
        body,
        cvtoken,
        cafile=datadict.get("cert"),
        cvp_endpoint=str(datadict.get("cvp") or ""),
    )
    if err:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "resource_write_failed",
            "InputsConfig POST failed.",
            details={"reason": err},
            workspace_id=workspace,
            warnings=warnings,
        )
    fields["resource_time"] = _resource_time(response)
    return _outcome(
        tool,
        _INPUTS_SOURCE,
        outcome="accepted",
        workspace_id=workspace,
        fields=fields,
        next_action="build_cvp_workspace",
        warnings=warnings,
    )
