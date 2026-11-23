"""Compare-and-set Campus Fabric SVI entries under campusServices.

Library only. Registration stays behind the writes env gate. Pattern mirrors
``studio_mss_inputs``: digest CAS on the root Inputs row, bounded ops vocab,
scoped structural diff, draft ``ws-mcp-*`` only, never submits Change Control.

Campus Fabric (``studio-avd-campus-fabric``) keeps the whole tree in one root
row at ``path.values []``. The generic ``set_inputs`` writer refuses the root
and its description allowlist cannot express an SVI upsert. This module edits
only ``campusServices`` → ``campusPodsServices`` → ``services.svis``, matched
by campus / pod tag query (defaults: ``Campus:campus-1709``, ``Campus-Pod:1``).

Ops: ``upsert_svi`` (match by integer ``id``) and ``remove_svi``.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from cvp_mcp.grpc.inputs_digest import canonical_json
from cvp_mcp.grpc.studio_root_cas import RootCasConfig, run_root_inputs_cas
from cvp_mcp.grpc.studios_write import (
    _INPUTS_SOURCE,
    _changed_leaf_paths,
    _disruptive_hits,
    _refused,
)
from cvp_mcp.write_access import validate_workspace_id, writes_enabled

TOOL_NAME = "set_cvp_campus_svi_inputs"
CAMPUS_STUDIO_ID = "studio-avd-campus-fabric"

DEFAULT_CAMPUS_TAG = "Campus:campus-1709"
DEFAULT_POD_TAG = "Campus-Pod:1"

_CAS_CONFIG = RootCasConfig(
    tool_name=TOOL_NAME,
    studio_id=CAMPUS_STUDIO_ID,
    operation="set_campus_svi_inputs",
    writable_top_level_keys=("campusServices",),
    scope_error_code="tree_diff_outside_campus_svi_scope",
    scope_error_message="The applied operations changed leaves outside campusServices SVI lists.",
    load_error_message="Could not read the Campus Fabric root Inputs document.",
    digest_mismatch_message=(
        "The current Campus Fabric document does not match expected_inputs_sha256; "
        "re-read and retry."
    ),
)

MAX_OPERATIONS = 10
_MAX_REPORTED = 10

_OPS: frozenset[str] = frozenset({"upsert_svi", "remove_svi"})

# Keys observed on live campus-1709 SVIs (2026-10-18). Optional fields may be
# omitted; extras refuse. IPv4 ``ipVirtualRouterSubnet`` / ``dhcpHelpers`` may
# be null / empty for IPv6-only entries driven by ``eosCli``.
_SVI_KEYS = frozenset(
    {
        "id",
        "name",
        "enabled",
        "routed",
        "vrf",
        "multicast",
        "nodes",
        "devices",
        "dhcpHelpers",
        "eosCli",
        "ipVirtualRouterSubnet",
    }
)
_SVI_REQUIRED = ("id", "name", "enabled", "routed")

_DEVICE_KEYS = frozenset({"enabled", "ipVirtualRouterSubnet", "tagQuery"})
_HELPER_KEYS = frozenset({"dhcpServer", "sourceInterface", "sourceVrf"})

# Diff must stay inside some campusServices[*]...svis[*] path.
_SVI_PATH_RE = re.compile(r"^\$\.campusServices\[\d+\].*\.svis(\[|$)")


class _Invalid(Exception):
    """One validation failure with the JSON-ish path of the offending value."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _require_keys(
    entry: Any, allowed: frozenset[str], required: tuple[str, ...], path: str
) -> None:
    if not isinstance(entry, dict):
        raise _Invalid(path, "must be an object")
    extra = sorted(set(entry) - allowed)
    if extra:
        raise _Invalid(f"{path}.{extra[0]}", "key not allowed")
    for key in required:
        if key not in entry:
            raise _Invalid(f"{path}.{key}", "required")


def _require_name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(path, "name must be a non-empty string")
    if value != value.strip():
        raise _Invalid(path, "name must not have surrounding whitespace")
    return value


def _require_vlan_id(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid(path, "must be an integer VLAN id")
    if not 1 <= value <= 4094:
        raise _Invalid(path, "must be in 1..4094")
    return value


def _validate_tag_query(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise _Invalid(path, "must be an object")
    tags = value.get("tags")
    if not isinstance(tags, dict):
        raise _Invalid(f"{path}.tags", "must be an object")
    query = tags.get("query")
    if not isinstance(query, str):
        raise _Invalid(f"{path}.tags.query", "must be a string")


def _validate_devices(devices: Any, path: str) -> None:
    if not isinstance(devices, list):
        raise _Invalid(path, "must be a list")
    for index, device in enumerate(devices):
        dpath = f"{path}[{index}]"
        _require_keys(device, _DEVICE_KEYS, ("tagQuery",), dpath)
        _validate_tag_query(device["tagQuery"], f"{dpath}.tagQuery")
        if "enabled" in device and device["enabled"] is not None:
            if not isinstance(device["enabled"], str):
                raise _Invalid(f"{dpath}.enabled", "must be null or a string")
        if "ipVirtualRouterSubnet" in device:
            subnet = device["ipVirtualRouterSubnet"]
            if subnet is not None and not isinstance(subnet, str):
                raise _Invalid(
                    f"{dpath}.ipVirtualRouterSubnet", "must be null or a CIDR string"
                )


def _validate_helpers(helpers: Any, path: str) -> None:
    if not isinstance(helpers, list):
        raise _Invalid(path, "must be a list")
    for index, helper in enumerate(helpers):
        hpath = f"{path}[{index}]"
        _require_keys(helper, _HELPER_KEYS, ("dhcpServer",), hpath)
        if not isinstance(helper["dhcpServer"], str) or not helper["dhcpServer"]:
            raise _Invalid(f"{hpath}.dhcpServer", "must be a non-empty string")


def _validate_svi_entry(entry: Any, path: str) -> None:
    _require_keys(entry, _SVI_KEYS, _SVI_REQUIRED, path)
    _require_vlan_id(entry["id"], f"{path}.id")
    _require_name(entry["name"], f"{path}.name")
    enabled = entry["enabled"]
    if not isinstance(enabled, str) or enabled not in ("Yes", "No"):
        raise _Invalid(f"{path}.enabled", "must be 'Yes' or 'No'")
    if not isinstance(entry["routed"], bool):
        raise _Invalid(f"{path}.routed", "must be a boolean")
    if "vrf" in entry and not isinstance(entry["vrf"], str):
        raise _Invalid(f"{path}.vrf", "must be a string")
    if "multicast" in entry and not isinstance(entry["multicast"], dict):
        raise _Invalid(f"{path}.multicast", "must be an object")
    if "nodes" in entry and not isinstance(entry["nodes"], list):
        raise _Invalid(f"{path}.nodes", "must be a list")
    if "devices" in entry:
        _validate_devices(entry["devices"], f"{path}.devices")
    if "dhcpHelpers" in entry:
        _validate_helpers(entry["dhcpHelpers"], f"{path}.dhcpHelpers")
    if "eosCli" in entry and entry["eosCli"] is not None:
        if not isinstance(entry["eosCli"], str):
            raise _Invalid(f"{path}.eosCli", "must be null or a string")
    if "ipVirtualRouterSubnet" in entry:
        subnet = entry["ipVirtualRouterSubnet"]
        if subnet is not None and not isinstance(subnet, str):
            raise _Invalid(f"{path}.ipVirtualRouterSubnet", "must be null or a string")


def _normalize_operations(
    operations: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not isinstance(operations, list) or not operations:
        return [], {
            "code": "campus_svi_operations_required",
            "message": "operations must be a non-empty list.",
            "details": {},
        }
    if len(operations) > MAX_OPERATIONS:
        return [], {
            "code": "campus_svi_operations_too_many",
            "message": f"At most {MAX_OPERATIONS} operations per call.",
            "details": {"count": len(operations), "max": MAX_OPERATIONS},
        }

    normalized: list[dict[str, Any]] = []
    try:
        for index, raw in enumerate(operations):
            path = f"operations[{index}]"
            if not isinstance(raw, dict):
                raise _Invalid(path, "must be an object")
            op = raw.get("op")
            if not isinstance(op, str) or op not in _OPS:
                raise _Invalid(f"{path}.op", f"must be one of {sorted(_OPS)}")

            if op == "remove_svi":
                _require_keys(raw, frozenset({"op", "id"}), ("id",), path)
                vlan_id = _require_vlan_id(raw["id"], f"{path}.id")
                normalized.append({"op": op, "id": vlan_id})
                continue

            _require_keys(raw, frozenset({"op", "entry"}), ("entry",), path)
            entry = raw["entry"]
            _validate_svi_entry(entry, f"{path}.entry")
            normalized.append({"op": op, "entry": copy.deepcopy(entry)})
    except _Invalid as exc:
        return [], {
            "code": "campus_svi_operation_invalid",
            "message": "An operation failed validation.",
            "details": {"path": exc.path, "reason": exc.message},
        }
    return normalized, None


def _tag_query(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    tags = block.get("tags")
    if not isinstance(tags, dict):
        return None
    query = tags.get("query")
    return query if isinstance(query, str) else None


def _find_svis_list(
    document: dict[str, Any], campus_tag: str, pod_tag: str
) -> tuple[list[Any] | None, dict[str, Any] | None]:
    """Locate the mutable ``svis`` list. Returns ``(list, refusal)``."""

    def bad(path: str, reason: str) -> dict[str, Any]:
        return {
            "code": "campus_svi_target_not_found",
            "message": "Could not locate the campusServices SVI list for the given tags.",
            "details": {
                "path": path,
                "reason": reason,
                "campus_tag": campus_tag,
                "pod_tag": pod_tag,
            },
        }

    services = document.get("campusServices")
    if not isinstance(services, list) or not services:
        return None, bad("$.campusServices", "missing or empty")

    campus_block = None
    campus_index = None
    for index, block in enumerate(services):
        if _tag_query(block) == campus_tag:
            campus_block = block
            campus_index = index
            break
    if campus_block is None:
        return None, bad("$.campusServices", f"no entry with tags.query={campus_tag!r}")

    try:
        pods = campus_block["inputs"]["campusServicesGroup"]["campusPodsServices"]
    except (KeyError, TypeError):
        return None, bad(
            f"$.campusServices[{campus_index}].inputs.campusServicesGroup.campusPodsServices",
            "missing",
        )
    if not isinstance(pods, list) or not pods:
        return None, bad(
            f"$.campusServices[{campus_index}].inputs.campusServicesGroup.campusPodsServices",
            "missing or empty",
        )

    pod_block = None
    pod_index = None
    for index, block in enumerate(pods):
        if _tag_query(block) == pod_tag:
            pod_block = block
            pod_index = index
            break
    if pod_block is None:
        return None, bad(
            f"$.campusServices[{campus_index}].inputs.campusServicesGroup.campusPodsServices",
            f"no entry with tags.query={pod_tag!r}",
        )

    try:
        svis = pod_block["inputs"]["services"]["svis"]
    except (KeyError, TypeError):
        return None, bad(
            (
                f"$.campusServices[{campus_index}].inputs.campusServicesGroup"
                f".campusPodsServices[{pod_index}].inputs.services.svis"
            ),
            "missing",
        )
    if not isinstance(svis, list):
        return None, bad(
            (
                f"$.campusServices[{campus_index}].inputs.campusServicesGroup"
                f".campusPodsServices[{pod_index}].inputs.services.svis"
            ),
            "not a list",
        )
    return svis, None


def _svi_id(item: Any) -> int | None:
    if not isinstance(item, dict):
        return None
    value = item.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _validate_document_shape(
    document: dict[str, Any], campus_tag: str, pod_tag: str
) -> dict[str, Any] | None:
    svis, err = _find_svis_list(document, campus_tag, pod_tag)
    if err:
        return err
    assert svis is not None
    seen: set[int] = set()
    for index, item in enumerate(svis):
        vlan_id = _svi_id(item)
        if vlan_id is None:
            return {
                "code": "campus_svi_document_malformed",
                "message": "An existing SVI entry has a missing or non-integer id.",
                "details": {"path": f"svis[{index}].id", "reason": "bad id"},
            }
        if vlan_id in seen:
            return {
                "code": "campus_svi_document_malformed",
                "message": "Duplicate SVI ids in the current document.",
                "details": {"path": f"svis[{index}].id", "id": vlan_id},
            }
        seen.add(vlan_id)
    return None


def _apply_operations(
    document: dict[str, Any],
    ops: list[dict[str, Any]],
    campus_tag: str,
    pod_tag: str,
) -> tuple[dict[str, Any], dict[str, list[str]], dict[str, Any] | None]:
    after = copy.deepcopy(document)
    svis, err = _find_svis_list(after, campus_tag, pod_tag)
    if err or svis is None:
        return after, {"added": [], "replaced": [], "removed": []}, err

    summary: dict[str, list[str]] = {"added": [], "replaced": [], "removed": []}
    for op in ops:
        kind = op["op"]
        if kind == "remove_svi":
            vlan_id = op["id"]
            before_len = len(svis)
            svis[:] = [item for item in svis if _svi_id(item) != vlan_id]
            if len(svis) == before_len:
                return (
                    after,
                    summary,
                    {
                        "code": "campus_svi_entry_not_found",
                        "message": "remove_svi names an id that does not exist.",
                        "details": {"id": vlan_id},
                    },
                )
            summary["removed"].append(f"svi:{vlan_id}")
            continue

        entry = op["entry"]
        vlan_id = entry["id"]
        label = f"svi:{vlan_id}"
        for position, existing in enumerate(svis):
            if _svi_id(existing) == vlan_id:
                svis[position] = {**existing, **copy.deepcopy(entry)}
                if label not in summary["added"] and label not in summary["replaced"]:
                    summary["replaced"].append(label)
                break
        else:
            svis.append(copy.deepcopy(entry))
            summary["added"].append(label)

    return after, summary, None


def _scope_violations(changed_paths: list[str]) -> list[str]:
    return [path for path in changed_paths if not _SVI_PATH_RE.match(path)]


def _digest_error(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return True
    return any(ch not in "0123456789abcdef" for ch in value)


def set_cvp_campus_svi_inputs(
    datadict: dict[str, Any],
    workspace_id: str,
    expected_inputs_sha256: str,
    operations: list[dict[str, Any]],
    confirm: bool = False,
    *,
    campus_tag: str = DEFAULT_CAMPUS_TAG,
    pod_tag: str = DEFAULT_POD_TAG,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Compare-and-set Campus Fabric SVI entries under campusServices.

    ``expected_inputs_sha256`` is the ``inputs_sha256`` from
    ``get_cvp_studio_inputs`` for the document the caller read. Dry-run unless
    ``confirm=True`` with the matching ``preview_token``. Never submits.
    """
    tool = TOOL_NAME
    campus = (campus_tag or "").strip() or DEFAULT_CAMPUS_TAG
    pod = (pod_tag or "").strip() or DEFAULT_POD_TAG

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
            details={"studio_id": CAMPUS_STUDIO_ID},
            workspace_id=workspace or None,
        )

    if _digest_error(expected_inputs_sha256):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "expected_inputs_sha256_required",
            "expected_inputs_sha256 must be the 64-hex inputs_sha256 from get_cvp_studio_inputs.",
            details={"studio_id": CAMPUS_STUDIO_ID},
            workspace_id=workspace,
        )

    ops, op_error = _normalize_operations(operations)
    if op_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            op_error["code"],
            op_error["message"],
            details={"studio_id": CAMPUS_STUDIO_ID, **op_error["details"]},
            workspace_id=workspace,
        )

    lint_hits = _disruptive_hits(canonical_json(ops))
    if lint_hits:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "disruptive_content_forbidden",
            "The operations contain EOS-disruptive text.",
            details={"matched": lint_hits},
            workspace_id=workspace,
        )

    def apply(
        document: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        shape_error = _validate_document_shape(document, campus, pod)
        if shape_error:
            return document, {}, shape_error

        after, entry_summary, apply_error = _apply_operations(
            document, ops, campus, pod
        )
        if apply_error:
            return after, entry_summary, apply_error

        # The original Campus guard is narrower than the shared top-level
        # guard: only leaves inside an SVI list may change.
        before_canonical = json.loads(canonical_json(document))
        after_canonical = json.loads(canonical_json(after))
        changed = _changed_leaf_paths(before_canonical, after_canonical)
        outside = _scope_violations(changed)
        if outside:
            return (
                after,
                entry_summary,
                {
                    "code": _CAS_CONFIG.scope_error_code,
                    "message": _CAS_CONFIG.scope_error_message,
                    "details": {
                        "outside": outside[:_MAX_REPORTED],
                        "changed_count": len(changed),
                    },
                },
            )
        return after, entry_summary, None

    return run_root_inputs_cas(
        datadict,
        workspace,
        expected_inputs_sha256,
        config=_CAS_CONFIG,
        operations=ops,
        apply=apply,
        confirm=confirm,
        preview_token_value=preview_token_value,
        extra_fields={"campus_tag": campus, "pod_tag": pod},
    )
