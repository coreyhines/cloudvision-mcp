"""Studios Phase 2.1 generic Inputs write: scoped-path POST with a leaf allowlist.

Library only. Nothing here is registered as an MCP tool; registration stays
behind the env gate in ``cloudvision_mcp.py`` (see ``docs/studios-phase2-spec.md``
§``set_cvp_studio_inputs``).

This is the *generic* Inputs writer and it deliberately refuses the root path: a
root POST replaces the studio's whole input tree, and the only sanctioned root
write is the Phase 2.0 description CAS in :mod:`cvp_mcp.grpc.studios_write`.

Order of checks, all of them before any mutating request is built:

* ``writes_enabled()``, else ``writes_disabled``;
* draft workspace id (``ws-mcp-``, never ``builtin-``);
* empty ``path_values`` → ``root_path_forbidden``;
* workspace exists and is ``WORKSPACE_STATE_PENDING``; studio is neither
  ``immutable`` nor ``from_package``;
* GET the current document at exactly that path, diff it against the proposed
  ``inputs``, and refuse ``input_key_not_allowed`` unless **every** changed leaf
  is named in ``allowed_input_keys`` (default ``["description"]``);
* leaves meaning admin/forwarding/power state are refused whatever the caller
  puts in ``allowed_input_keys``;
* ``confirm=False`` returns a ``preview_token`` and performs no mutating HTTP.

There is no ``replace_all_inputs`` escape hatch, by design.
"""

from __future__ import annotations

import json
from typing import Any

from cvp_mcp.grpc.resource_write import post_resource_config
from cvp_mcp.grpc.studio_crud import _read_studio_anywhere
from cvp_mcp.grpc.studios import get_cvp_studio_inputs
from cvp_mcp.grpc.studios_write import (
    _INPUTS_SOURCE,
    INPUTS_CONFIG_PATH,
    WORKSPACE_STATE_PENDING,
    _as_str,
    _changed_leaf_paths,
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

TOOL_NAME = "set_cvp_studio_inputs"

DEFAULT_ALLOWED_INPUT_KEYS: tuple[str, ...] = ("description",)

# Leaf names that mean admin / forwarding / power state. A studio turns these
# into `shutdown`, a VLAN move or a PoE cut without the word ever appearing in a
# value, so they are refused even when a caller lists them in
# ``allowed_input_keys``. Matched as substrings of the normalized key so
# ``portProfile``, ``poe_enabled`` and ``vlans`` are all caught.
FORBIDDEN_LEAF_TOKENS: tuple[str, ...] = (
    "enabled",
    "disabled",
    "shutdown",
    "vlan",
    "poe",
    "profile",
    "mode",
)

# Diff paths reported in a refusal, so a huge accidental diff cannot flood the
# envelope. ``changed_count`` always reports the true total.
_MAX_REPORTED_PATHS = 10

_ROOT_INPUTS_HINT = (
    "Use set_cvp_access_interface_description for this studio’s only Resource "
    "row (path_values []). Generic Inputs cannot POST the root."
)


def _normalize_key(name: str) -> str:
    """Lowercase, alphanumerics only: ``port_Profile`` and ``PoE`` normalize."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _forbidden_tokens(name: str) -> list[str]:
    normalized = _normalize_key(name)
    return [token for token in FORBIDDEN_LEAF_TOKENS if token in normalized]


def _path_segments(path: str) -> list[str]:
    """Key names in a ``$.a.b[0].c`` diff path, list indices and ``$`` stripped."""
    segments: list[str] = []
    for raw in path.split("."):
        name = raw.split("[", 1)[0]
        if name and name != "$":
            segments.append(name)
    return segments


def _leaf_violations(
    changed_paths: list[str], allowed: set[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Split changed paths into allowlist misses and forbidden-key hits.

    The allowlist is checked against the *leaf* name only, matching the spec.
    The forbidden tokens are checked against **every** segment: a description
    nested under ``vlans`` or ``portProfile`` is still a change to admin state.
    """
    not_allowed: list[str] = []
    forbidden: list[dict[str, Any]] = []
    for path in changed_paths:
        segments = _path_segments(path)
        leaf = segments[-1] if segments else ""
        if _normalize_key(leaf) not in allowed:
            not_allowed.append(path)
        hits = sorted({t for s in segments for t in _forbidden_tokens(s)})
        if hits:
            forbidden.append({"path": path, "matched": hits})
    return not_allowed, forbidden


def _resolve_allowed_keys(
    allowed_input_keys: Any,
) -> tuple[set[str], list[str], str | None]:
    """Return ``(normalized_allowed, echoed_keys, error_code)``.

    A caller may narrow the default but never widen it onto an admin key: an
    ``allowed_input_keys`` entry that hits :data:`FORBIDDEN_LEAF_TOKENS` refuses
    the whole call rather than being silently dropped.
    """
    keys = (
        list(DEFAULT_ALLOWED_INPUT_KEYS)
        if allowed_input_keys is None
        else allowed_input_keys
    )
    if not isinstance(keys, (list, tuple)) or not keys:
        return set(), [], "input_key_not_allowed"
    echoed: list[str] = []
    normalized: set[str] = set()
    for key in keys:
        if not isinstance(key, str) or not key.strip():
            return set(), [], "input_key_not_allowed"
        name = key.strip()
        if _forbidden_tokens(name):
            return set(), [], "input_key_not_allowed"
        echoed.append(name)
        normalized.add(_normalize_key(name))
    return normalized, echoed, None


def _validate_path_values(path_values: Any) -> tuple[list[str], str | None]:
    """Return ``(values, error_code)``; the root path is never writable here."""
    if path_values is None:
        return [], "root_path_forbidden"
    if not isinstance(path_values, (list, tuple)):
        return [], "inputs_path_unresolved"
    values = list(path_values)
    if not values:
        return [], "root_path_forbidden"
    for value in values:
        if not isinstance(value, str) or not value.strip():
            return [], "inputs_path_unresolved"
    return values, None


def _read_path_document(
    datadict: dict[str, Any],
    studio_id: str,
    workspace_id: str,
    path_values: list[str],
) -> tuple[Any, str, str | None, list[str], list[list[Any]]]:
    """GET the Inputs row keyed at exactly ``path_values``.

    Prefers the workspace overlay and falls back to mainline (``workspaceId``
    ``""``), matching "first write copies mainline, later writes read the
    overlay". Any warning on the read fails closed: a truncated or partly
    skipped NDJSON stream would diff the proposal against a partial document.
    Returns ``(document, source_workspace_id, error, warnings,
    available_path_values)``. Resource paths come only from the selected row
    set; JSON keys inside an ``inputs`` body are never treated as paths.
    """
    warnings: list[str] = []
    for source in (workspace_id, ""):
        env = get_cvp_studio_inputs(datadict, studio_id, source)
        env_warnings = [w for w in (env.get("warnings") or []) if isinstance(w, str)]
        warnings.extend(env_warnings)
        if env_warnings:
            return None, "", "preflight_failed", warnings, []
        items = [item for item in (env.get("items") or []) if isinstance(item, dict)]
        if not items:
            continue

        available: list[list[Any]] = []
        seen: set[str] = set()
        for item in items:
            resource_path = item.get("path_values")
            if not isinstance(resource_path, list):
                continue
            identity = json.dumps(resource_path, sort_keys=True, default=str)
            if identity in seen:
                continue
            seen.add(identity)
            available.append(resource_path)
        reported_available = available[:_MAX_REPORTED_PATHS]
        matches = [item for item in items if item.get("path_values") == path_values]
        if len(matches) > 1:
            if len(available) > _MAX_REPORTED_PATHS:
                warnings.append(
                    f"available_path_values_truncated_to_{_MAX_REPORTED_PATHS}"
                )
            return (
                None,
                source,
                "inputs_path_not_found",
                warnings,
                reported_available,
            )
        if not matches:
            if len(available) > _MAX_REPORTED_PATHS:
                warnings.append(
                    f"available_path_values_truncated_to_{_MAX_REPORTED_PATHS}"
                )
            return (
                None,
                source,
                "inputs_path_not_found",
                warnings,
                reported_available,
            )
        document = matches[0].get("inputs")
        if not isinstance(document, (dict, list)):
            return (
                None,
                source,
                "inputs_path_unresolved",
                warnings,
                reported_available,
            )
        return document, source, None, warnings, reported_available
    return None, "", "inputs_path_not_found", warnings, []


def set_cvp_studio_inputs(
    datadict: dict[str, Any],
    studio_id: str,
    workspace_id: str,
    path_values: list[str],
    inputs: Any,
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
    allowed_input_keys: list[str] | None = None,
) -> dict[str, Any]:
    """POST one studio Inputs subtree at a non-root path.

    ``inputs`` is the full replacement document for ``path_values`` — it is
    diffed leaf-by-leaf against the current document at the same path, and the
    write is refused unless every changed leaf is named in
    ``allowed_input_keys`` (default ``["description"]``).
    """
    tool = TOOL_NAME
    if not writes_enabled():
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "writes_disabled",
            "Writes are disabled; set CLOUDVISION_MCP_ALLOW_WRITES=1 and restart.",
        )

    studio = (studio_id or "").strip()
    if not studio:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "studio_not_found",
            "studio_id is required.",
        )

    workspace = (workspace_id or "").strip()
    id_error = validate_workspace_id(workspace)
    if id_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            id_error,
            "Workspace id must be a non-builtin draft id starting with 'ws-mcp-'.",
            details={"studio_id": studio},
            workspace_id=workspace or None,
        )

    path, path_error = _validate_path_values(path_values)
    if path_error == "root_path_forbidden":
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "root_path_forbidden",
            "Generic Inputs writes require a non-empty path; the root path would "
            "replace the studio's whole input tree. Use "
            "set_cvp_access_interface_description for the root description CAS.",
            details={"studio_id": studio},
            workspace_id=workspace,
        )
    if path_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            path_error,
            "path_values must be a list of non-empty strings.",
            details={"studio_id": studio},
            workspace_id=workspace,
        )

    allowed, allowed_echo, allowed_error = _resolve_allowed_keys(allowed_input_keys)
    if allowed_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            allowed_error,
            "allowed_input_keys must be a non-empty list of input key names, and "
            "may never name an admin, forwarding or power key.",
            details={"forbidden_tokens": list(FORBIDDEN_LEAF_TOKENS)},
            workspace_id=workspace,
        )

    if not isinstance(inputs, (dict, list)):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_path_unresolved",
            "inputs must be the JSON object or array to store at path_values.",
            details={"studio_id": studio, "path_values": path},
            workspace_id=workspace,
        )

    _, _, missing = _credentials(datadict)
    if missing:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "preflight_failed",
            "CloudVision credentials are incomplete; no preflight GET was made.",
            details={"reason": missing},
            workspace_id=workspace,
        )

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
    state = _as_str((summary or {}).get("state")).strip()
    if state != WORKSPACE_STATE_PENDING:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "workspace_not_pending" if state else "workspace_state_unknown",
            "Inputs writes require a pending draft workspace with a known state.",
            details={"state": state},
            workspace_id=workspace,
            warnings=ws_warnings,
        )

    studio_obj, _, studio_status, studio_warnings = _read_studio_anywhere(
        datadict, studio, workspace
    )
    if studio_status is not None or not isinstance(studio_obj, dict):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "preflight_failed",
            "Studio GET failed; refusing Inputs write.",
            details={"studio_id": studio},
            workspace_id=workspace,
            warnings=studio_warnings,
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
            details={"studio_id": studio},
            workspace_id=workspace,
        )

    current, source_workspace, load_error, warnings, available_path_values = (
        _read_path_document(datadict, studio, workspace, path)
    )
    if load_error:
        details: dict[str, Any] = {"studio_id": studio, "path_values": path}
        if load_error == "inputs_path_not_found":
            details["available_path_values"] = available_path_values
            if available_path_values == [[]]:
                details["hint"] = _ROOT_INPUTS_HINT
        return _refused(
            tool,
            _INPUTS_SOURCE,
            load_error,
            "Could not read the current Inputs document at path_values.",
            details=details,
            workspace_id=workspace,
            warnings=warnings,
        )

    # Serialize both sides (sorted keys) and diff the reparsed objects: this
    # catches shared references and any value that does not round-trip, not just
    # the leaves the caller meant to touch.
    before_json = json.dumps(current, sort_keys=True, default=str)
    after_json = json.dumps(inputs, sort_keys=True, default=str)
    changed = _changed_leaf_paths(json.loads(before_json), json.loads(after_json))

    not_allowed, forbidden = _leaf_violations(changed, allowed)
    if forbidden or not_allowed:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "input_key_not_allowed",
            "The proposed inputs change leaves outside allowed_input_keys.",
            details={
                "studio_id": studio,
                "path_values": path,
                "allowed_input_keys": allowed_echo,
                "changed_count": len(changed),
                "not_allowed": not_allowed[:_MAX_REPORTED_PATHS],
                "forbidden": forbidden[:_MAX_REPORTED_PATHS],
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
            "The proposed inputs document introduces EOS-disruptive text.",
            details={"matched": introduced},
            workspace_id=workspace,
            warnings=warnings,
        )

    # A no-op POST is honest about being one rather than inventing a refusal
    # code the spec does not list; it rewrites byte-identical content.
    if not changed:
        warnings = [*warnings, "inputs_unchanged"]

    body = {
        "key": {
            "studioId": studio,
            "workspaceId": workspace,
            "path": {"values": path},
        },
        "inputs": json.dumps(inputs),
    }
    token_args = {
        "studio_id": studio,
        "workspace_id": workspace,
        "path_values": path,
        "inputs": json.loads(after_json),
        "allowed_input_keys": allowed_echo,
    }
    fields: dict[str, Any] = {
        "operation": "set_studio_inputs",
        "studio_id": studio,
        "path_values": path,
        "allowed_input_keys": allowed_echo,
        "inputs_source_workspace_id": source_workspace,
        "changed_leaves": len(changed),
        "changed_leaf_paths": changed[:_MAX_REPORTED_PATHS],
        "posted_at_root": False,
        "request_body": body,
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
        next_action="get_cvp_studio_inputs",
        warnings=warnings,
    )
