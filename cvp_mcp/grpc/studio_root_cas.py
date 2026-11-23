"""Shared compare-and-set for studios whose whole Inputs tree is one root row.

Library only. A studio such as ``studio-mss-service`` or
``studio-telemetry-config`` keeps its input tree in a single Inputs Resource row
at ``path.values []``. The generic writer refuses that root on purpose, so each
plugin ships a bounded operation vocabulary and delegates the pipeline here.

This module owns everything that is *not* plugin-specific, in this order, all
before the first HTTP write:

1. ``writes_enabled()``, draft workspace id, 64-hex ``expected_inputs_sha256``;
2. credentials, workspace GET (pending), studio GET (not immutable/packaged);
3. root read for ``config.studio_id``, digest CAS;
4. ``apply(document)`` (plugin ops, plugin validation);
5. canonical diff must stay inside ``config.writable_top_level_keys``;
6. ``confirm=False`` -> preview with a ``preview_token`` bound to the tool, the
   operations and the after digest; ``confirm=True`` -> token check, one POST.

Operation validation (shape, EOS lint, referential checks) stays in the plugin:
normalize ops before calling, and do the rest inside ``apply``.

Never submits. The human reviews the built workspace in the CVP UI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cvp_mcp.grpc.inputs_digest import canonical_json, inputs_sha256
from cvp_mcp.grpc.resource_write import post_resource_config
from cvp_mcp.grpc.studio_crud import _read_studio_anywhere
from cvp_mcp.grpc.studios_write import (
    _INPUTS_SOURCE,
    INPUTS_CONFIG_PATH,
    WORKSPACE_STATE_PENDING,
    _as_str,
    _changed_leaf_paths,
    _credentials,
    _load_root_inputs,
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

_MAX_REPORTED = 10

ApplyFn = Callable[
    [dict[str, Any]],
    tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None],
]


@dataclass
class RootCasConfig:
    """Per-studio parameters for :func:`run_root_inputs_cas`."""

    tool_name: str
    studio_id: str
    operation: str
    writable_top_level_keys: tuple[str, ...]
    scope_error_code: str
    scope_error_message: str
    load_error_message: str
    digest_mismatch_message: str


def digest_malformed(value: Any) -> bool:
    """True unless ``value`` is a 64-char lowercase hex string."""
    if not isinstance(value, str) or len(value) != 64:
        return True
    return any(ch not in "0123456789abcdef" for ch in value)


def scope_violations(
    changed_paths: list[str], writable_top_level_keys: tuple[str, ...]
) -> list[str]:
    """Changed leaf paths that are not under a writable top-level key."""
    return [
        path
        for path in changed_paths
        if not any(
            path == f"$.{key}" or path.startswith(f"$.{key}[")
            for key in writable_top_level_keys
        )
    ]


def run_root_inputs_cas(
    datadict: dict[str, Any],
    workspace_id: str,
    expected_inputs_sha256: str,
    *,
    config: RootCasConfig,
    operations: list[dict[str, Any]],
    apply: ApplyFn,
    confirm: bool = False,
    preview_token_value: str | None = None,
    extra_fields: dict[str, Any] | None = None,
    early_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Compare-and-set the root Inputs row of ``config.studio_id``.

    ``apply(document)`` receives the current root document and returns
    ``(after_document, entry_summary, error_or_None)``. ``entry_summary`` has
    ``added`` / ``replaced`` / ``removed`` string lists and may carry an
    optional ``warnings`` list that is appended to the envelope warnings. An
    error dict (``code``, ``message``, optional ``details``) refuses with that
    code.
    """
    tool = config.tool_name
    studio_id = config.studio_id

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
            details={"studio_id": studio_id},
            workspace_id=workspace or None,
        )

    if digest_malformed(expected_inputs_sha256):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "expected_inputs_sha256_required",
            "expected_inputs_sha256 must be the 64-hex inputs_sha256 from get_cvp_studio_inputs.",
            details={"studio_id": studio_id},
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
        datadict, studio_id, workspace
    )
    if studio_status is not None or not isinstance(studio_obj, dict):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "preflight_failed",
            (
                "Studio not found in the draft or mainline; refusing Inputs write."
                if studio_status == "not_found"
                else "Studio GET failed; refusing Inputs write."
            ),
            details={
                "studio_id": studio_id,
                "studio_status": studio_status or "malformed",
            },
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
            details={"studio_id": studio_id},
            workspace_id=workspace,
        )

    document, source_workspace, load_error, load_warnings = _load_root_inputs(
        datadict, workspace, studio_id=studio_id
    )
    warnings = [
        *(early_warnings or []),
        *ws_warnings,
        *studio_warnings,
        *load_warnings,
    ]
    if load_error or not isinstance(document, dict):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            load_error or "inputs_path_unresolved",
            config.load_error_message,
            details={
                "studio_id": studio_id,
                "inputs_source_workspace_id": source_workspace,
                "reason": (
                    "root document is not a JSON object"
                    if load_error is None
                    else "root row missing, unparsable, or the stream was incomplete"
                ),
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    before_sha256 = inputs_sha256(document)
    if before_sha256 != expected_inputs_sha256:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_digest_mismatch",
            config.digest_mismatch_message,
            details={
                "studio_id": studio_id,
                "current_inputs_sha256": before_sha256,
                "expected_inputs_sha256": expected_inputs_sha256,
                "inputs_source_workspace_id": source_workspace,
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    after, entry_summary, apply_error = apply(document)
    if apply_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            apply_error["code"],
            apply_error["message"],
            details={"studio_id": studio_id, **(apply_error.get("details") or {})},
            workspace_id=workspace,
            warnings=warnings,
        )
    warnings = [*warnings, *(entry_summary.get("warnings") or [])]

    # Diff the reparsed canonical forms (sorted keys) so shared references and
    # non-round-tripping values are caught.
    before_canonical = json.loads(canonical_json(document))
    after_canonical = json.loads(canonical_json(after))
    changed = _changed_leaf_paths(before_canonical, after_canonical)
    outside = scope_violations(changed, config.writable_top_level_keys)
    if outside:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            config.scope_error_code,
            config.scope_error_message,
            details={
                "studio_id": studio_id,
                "outside": outside[:_MAX_REPORTED],
                "changed_count": len(changed),
            },
            workspace_id=workspace,
            warnings=warnings,
        )
    if not changed:
        warnings = [*warnings, "inputs_unchanged"]

    after_sha256 = inputs_sha256(after)
    body = {
        "key": {
            "studioId": studio_id,
            "workspaceId": workspace,
            "path": {"values": []},
        },
        # Wire key order is preserved on purpose; only the digest sorts keys.
        "inputs": json.dumps(after),
    }
    token_args = {
        "studio_id": studio_id,
        "workspace_id": workspace,
        "expected_inputs_sha256": expected_inputs_sha256,
        "operations": operations,
        "after_sha256": after_sha256,
    }
    fields: dict[str, Any] = {
        "operation": config.operation,
        "studio_id": studio_id,
        "inputs_source_workspace_id": source_workspace,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "operations_applied": len(operations),
        "operations": operations,
        "entries_added": entry_summary.get("added", []),
        "entries_replaced": entry_summary.get("replaced", []),
        "entries_removed": entry_summary.get("removed", []),
        "changed_leaves": len(changed),
        "changed_leaf_paths": changed[:_MAX_REPORTED],
        "posted_at_root": True,
        "disruptive": False,
        "request_body": body,
        "resource_time": None,
    }
    if extra_fields:
        fields.update(extra_fields)

    if confirm is not True:
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
        next_action="build_cvp_workspace, then review the workspace diff in the CVP UI.",
        warnings=warnings,
    )
