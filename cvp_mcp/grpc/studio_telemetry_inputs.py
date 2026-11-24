"""Compare-and-set Streaming Telemetry Agent onboarding profiles and assignments.

Library only. MCP registration lives elsewhere, behind the writes env gate.

``studio-telemetry-config`` keeps its whole Inputs tree in one root row at
``path.values []``. The generic ``set_inputs`` writer refuses that root, so this
module offers a bounded operation vocabulary (``ops_only``; no full-document
replace) and delegates load / digest CAS / scoped diff / preview / POST to
:func:`cvp_mcp.grpc.studio_root_cas.run_root_inputs_cas`.

Only ``onboardingProfiles`` and ``profileAssignment`` may change. Tag-resolved
lists such as ``sourceVrfAssignment``, ``disableAAAResolver`` (top level) and
``customFlags`` are out of scope in v1.

Ops:

- ``upsert_onboarding_profile`` ``{profile}``: replace the profile with the same
  ``profileName`` wholesale, else append.
- ``remove_onboarding_profile`` ``{profileName}``.
- ``set_profile_assignment`` ``{query, onboardingProfile, default_config?}``:
  upsert the ``profileAssignment`` row whose ``tags.query`` equals ``query``.
- ``patch_onboarding_profile`` ``{profileName, patch, cluster_profile_name?}``:
  set named leaves on an existing profile. ``cluster_profile_name`` picks the
  cluster / device-collection entry when the profile has more than one.

Never submits. The human reviews the built workspace in the CVP UI.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from cvp_mcp.grpc.inputs_digest import canonical_json
from cvp_mcp.grpc.studio_root_cas import (
    RootCasConfig,
    digest_malformed,
    run_root_inputs_cas,
)
from cvp_mcp.grpc.studios_write import _INPUTS_SOURCE, _disruptive_hits, _refused
from cvp_mcp.write_access import validate_workspace_id, writes_enabled

TOOL_NAME = "set_cvp_telemetry_inputs"
TELEMETRY_STUDIO_ID = "studio-telemetry-config"
WRITABLE_TOP_LEVEL_KEYS = ("onboardingProfiles", "profileAssignment")
MAX_OPERATIONS = 20

_CAS_CONFIG = RootCasConfig(
    tool_name=TOOL_NAME,
    studio_id=TELEMETRY_STUDIO_ID,
    operation="set_telemetry_inputs",
    writable_top_level_keys=WRITABLE_TOP_LEVEL_KEYS,
    scope_error_code="tree_diff_outside_telemetry_scope",
    scope_error_message=(
        "The applied operations changed leaves outside onboardingProfiles / "
        "profileAssignment."
    ),
    load_error_message="Could not read the Streaming Telemetry root Inputs document.",
    digest_mismatch_message=(
        "The current Streaming Telemetry document does not match "
        "expected_inputs_sha256; re-read and retry."
    ),
)

_OPS: frozenset[str] = frozenset(
    {
        "upsert_onboarding_profile",
        "remove_onboarding_profile",
        "set_profile_assignment",
        "patch_onboarding_profile",
    }
)

# Keys observed on the live campus-1709 profile (2026-11-23). Extras refuse.
_PROFILE_KEYS = frozenset(
    {
        "profileName",
        "cluster_profiles",
        "customFlags",
        "dev_collection",
        "disableAAAResolver",
        "multi_cluster",
        "select_active_cluster",
        "sourceInterfaceAndVrf",
    }
)
_PROFILE_REQUIRED = ("profileName", "cluster_profiles", "dev_collection")

_CLUSTER_KEYS = frozenset(
    {"cvopt_name", "cvopt_cvaddr_group", "cvopt_cvaddr_v6_group", "ingestPort"}
)
_DEV_COLLECTION_KEYS = frozenset(
    {
        "cluster_profile_name",
        "cvopt_auth_token",
        "cvopt_auth_type",
        "cvopt_proxy",
        "cvopt_sourceintf",
        "cvopt_vrf",
    }
)
_SOURCE_INTF_VRF_KEYS = frozenset({"source_interface", "source_vrf"})
_DISABLE_AAA_KEYS = frozenset({"disableAAA"})

_PATCH_KEYS = frozenset(
    {
        "cvopt_auth_token",
        "cvopt_vrf",
        "disableAAA",
        "multi_cluster",
        "addr1",
        "ingestPort",
    }
)
# Patch keys that land on a dev_collection entry vs a cluster_profiles entry.
_DEV_PATCH_KEYS = frozenset({"cvopt_auth_token", "cvopt_vrf"})
_CLUSTER_PATCH_KEYS = frozenset({"addr1", "ingestPort"})


class _Invalid(Exception):
    """One validation failure with the JSON-ish path of the offending value."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


# --- value validators -------------------------------------------------------


def _require_keys(
    entry: Any, allowed: frozenset[str], required: tuple[str, ...], path: str
) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise _Invalid(path, "must be an object")
    extra = sorted(set(entry) - allowed)
    if extra:
        raise _Invalid(f"{path}.{extra[0]}", "key not allowed")
    for key in required:
        if key not in entry:
            raise _Invalid(f"{path}.{key}", "required")
    return entry


def _require_text(value: Any, path: str) -> str:
    """Non-empty single-line string without surrounding whitespace."""
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(path, "must be a non-empty string")
    if value != value.strip():
        raise _Invalid(path, "must not have surrounding whitespace")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise _Invalid(path, "must not contain control characters")
    return value


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _require_text(value, path)


def _optional_bool(value: Any, path: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise _Invalid(path, "must be null or a boolean")
    return value


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _Invalid(path, "must be a boolean")
    return value


def _optional_port(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid(path, "must be null or an integer port")
    if not 1 <= value <= 65535:
        raise _Invalid(path, "must be in 1..65535")
    return value


# Values below are rendered into TerminAttr daemon arguments, so each is a
# single whitespace-free token of a field-specific alphabet (no '=', ',', ' ').
_NAME_RE = re.compile(r"^[A-Za-z0-9_.:/\-]+$")
_ADDR_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]+$")
_TOKEN_PATH_RE = re.compile(r"^/[A-Za-z0-9_./\-]+$")
_PROXY_RE = re.compile(r"^[A-Za-z0-9_.:/@\-\[\]]+$")
_ADDR_KEY_RE = re.compile(r"^addr[0-9]+$")

# Field -> pattern for dev_collection string leaves.
_DEV_FIELD_RE = {
    "cvopt_auth_token": (_TOKEN_PATH_RE, "an absolute path without whitespace"),
    "cvopt_auth_type": (_NAME_RE, "a name token"),
    "cvopt_proxy": (_PROXY_RE, "a proxy URL without whitespace"),
    "cvopt_sourceintf": (_NAME_RE, "an interface name"),
    "cvopt_vrf": (_NAME_RE, "a VRF name"),
}


def _require_token(value: Any, path: str, pattern: re.Pattern[str], what: str) -> str:
    _require_text(value, path)
    if not pattern.fullmatch(value):
        raise _Invalid(path, f"must be {what}")
    return value


def _optional_token(
    value: Any, path: str, pattern: re.Pattern[str], what: str
) -> str | None:
    return None if value is None else _require_token(value, path, pattern, what)


def _require_name_token(value: Any, path: str) -> str:
    return _require_token(value, path, _NAME_RE, "a name token")


def _require_empty_object(value: Any, path: str) -> None:
    """Free-form flag maps are out of scope in v1; only ``{}`` is accepted."""
    if value != {}:
        raise _Invalid(path, "must be an empty object in v1")


def _validate_addr_group(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise _Invalid(path, "must be an object")
    for key, item in value.items():
        if not isinstance(key, str) or not _ADDR_KEY_RE.fullmatch(key):
            raise _Invalid(f"{path}.{key}", "key not allowed")
        _optional_token(item, f"{path}.{key}", _ADDR_RE, "a host, IP or host:port")


def _validate_profile(profile: Any, path: str) -> None:
    _require_keys(profile, _PROFILE_KEYS, _PROFILE_REQUIRED, path)
    _require_name_token(profile["profileName"], f"{path}.profileName")

    clusters = profile["cluster_profiles"]
    if not isinstance(clusters, list) or not clusters:
        raise _Invalid(f"{path}.cluster_profiles", "must be a non-empty list")
    cluster_names: set[str] = set()
    for index, cluster in enumerate(clusters):
        cpath = f"{path}.cluster_profiles[{index}]"
        _require_keys(cluster, _CLUSTER_KEYS, ("cvopt_name",), cpath)
        name = _require_name_token(cluster["cvopt_name"], f"{cpath}.cvopt_name")
        if name in cluster_names:
            raise _Invalid(f"{cpath}.cvopt_name", "duplicate cluster profile name")
        cluster_names.add(name)
        for key in ("cvopt_cvaddr_group", "cvopt_cvaddr_v6_group"):
            if key in cluster:
                _validate_addr_group(cluster[key], f"{cpath}.{key}")
        if "ingestPort" in cluster:
            _optional_port(cluster["ingestPort"], f"{cpath}.ingestPort")

    devs = profile["dev_collection"]
    if not isinstance(devs, list) or not devs:
        raise _Invalid(f"{path}.dev_collection", "must be a non-empty list")
    for index, dev in enumerate(devs):
        dpath = f"{path}.dev_collection[{index}]"
        _require_keys(dev, _DEV_COLLECTION_KEYS, ("cluster_profile_name",), dpath)
        ref = _require_name_token(
            dev["cluster_profile_name"], f"{dpath}.cluster_profile_name"
        )
        if ref not in cluster_names:
            raise _Invalid(
                f"{dpath}.cluster_profile_name",
                "must name a cvopt_name in cluster_profiles",
            )
        for key, (pattern, what) in _DEV_FIELD_RE.items():
            if key in dev:
                _optional_token(dev[key], f"{dpath}.{key}", pattern, what)

    if "disableAAAResolver" in profile:
        resolver = _require_keys(
            profile["disableAAAResolver"],
            _DISABLE_AAA_KEYS,
            (),
            f"{path}.disableAAAResolver",
        )
        if "disableAAA" in resolver:
            _optional_bool(
                resolver["disableAAA"], f"{path}.disableAAAResolver.disableAAA"
            )
    if "multi_cluster" in profile:
        _optional_bool(profile["multi_cluster"], f"{path}.multi_cluster")
    if "sourceInterfaceAndVrf" in profile:
        siv = _require_keys(
            profile["sourceInterfaceAndVrf"],
            _SOURCE_INTF_VRF_KEYS,
            (),
            f"{path}.sourceInterfaceAndVrf",
        )
        for key, item in siv.items():
            _optional_token(
                item, f"{path}.sourceInterfaceAndVrf.{key}", _NAME_RE, "a name token"
            )
    for key in ("customFlags", "select_active_cluster"):
        if key in profile:
            _require_empty_object(profile[key], f"{path}.{key}")


def _validate_patch(patch: Any, path: str) -> None:
    if not isinstance(patch, dict) or not patch:
        raise _Invalid(path, "must be a non-empty object")
    extra = sorted(set(patch) - _PATCH_KEYS)
    if extra:
        raise _Invalid(f"{path}.{extra[0]}", f"must be one of {sorted(_PATCH_KEYS)}")
    for key in _DEV_PATCH_KEYS & patch.keys():
        pattern, what = _DEV_FIELD_RE[key]
        _optional_token(patch[key], f"{path}.{key}", pattern, what)
    if "disableAAA" in patch:
        _optional_bool(patch["disableAAA"], f"{path}.disableAAA")
    if "multi_cluster" in patch:
        _require_bool(patch["multi_cluster"], f"{path}.multi_cluster")
    if "addr1" in patch:
        _require_token(
            patch["addr1"], f"{path}.addr1", _ADDR_RE, "a host, IP or host:port"
        )
    if "ingestPort" in patch:
        if patch["ingestPort"] is None:
            raise _Invalid(f"{path}.ingestPort", "must be an integer port")
        _optional_port(patch["ingestPort"], f"{path}.ingestPort")


# --- operations -------------------------------------------------------------


def normalize_operations(
    operations: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Validate and deep-copy ``operations``. Returns ``(ops, error_or_None)``."""
    if not isinstance(operations, list) or not operations:
        return [], _error(
            "telemetry_operations_required", "operations must be a non-empty list."
        )
    if len(operations) > MAX_OPERATIONS:
        return [], _error(
            "telemetry_operations_too_many",
            f"At most {MAX_OPERATIONS} operations per call.",
            count=len(operations),
            max=MAX_OPERATIONS,
        )

    normalized: list[dict[str, Any]] = []
    try:
        for index, raw in enumerate(operations):
            path = f"operations[{index}]"
            if not isinstance(raw, dict):
                raise _Invalid(path, "must be an object")
            op = raw.get("op")
            if not isinstance(op, str) or op not in _OPS:
                raise _Invalid(f"{path}.op", f"must be one of {sorted(_OPS)}")

            if op == "upsert_onboarding_profile":
                _require_keys(raw, frozenset({"op", "profile"}), ("profile",), path)
                _validate_profile(raw["profile"], f"{path}.profile")
                normalized.append({"op": op, "profile": copy.deepcopy(raw["profile"])})
            elif op == "remove_onboarding_profile":
                _require_keys(
                    raw, frozenset({"op", "profileName"}), ("profileName",), path
                )
                name = _require_text(raw["profileName"], f"{path}.profileName")
                normalized.append({"op": op, "profileName": name})
            elif op == "set_profile_assignment":
                _require_keys(
                    raw,
                    frozenset({"op", "query", "onboardingProfile", "default_config"}),
                    ("query", "onboardingProfile"),
                    path,
                )
                item: dict[str, Any] = {
                    "op": op,
                    "query": _require_text(raw["query"], f"{path}.query"),
                    "onboardingProfile": _optional_text(
                        raw["onboardingProfile"], f"{path}.onboardingProfile"
                    ),
                }
                if "default_config" in raw:
                    item["default_config"] = _require_bool(
                        raw["default_config"], f"{path}.default_config"
                    )
                normalized.append(item)
            else:  # patch_onboarding_profile
                _require_keys(
                    raw,
                    frozenset({"op", "profileName", "patch", "cluster_profile_name"}),
                    ("profileName", "patch"),
                    path,
                )
                item = {
                    "op": op,
                    "profileName": _require_text(
                        raw["profileName"], f"{path}.profileName"
                    ),
                }
                if "cluster_profile_name" in raw:
                    item["cluster_profile_name"] = _require_text(
                        raw["cluster_profile_name"], f"{path}.cluster_profile_name"
                    )
                _validate_patch(raw["patch"], f"{path}.patch")
                item["patch"] = copy.deepcopy(raw["patch"])
                normalized.append(item)
    except _Invalid as exc:
        return [], _error(
            "telemetry_operation_invalid",
            "An operation failed validation.",
            path=exc.path,
            reason=exc.message,
        )
    return normalized, None


def _assignment_query(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    tags = row.get("tags")
    if not isinstance(tags, dict):
        return None
    query = tags.get("query")
    return query if isinstance(query, str) else None


def _assigned_profile(row: dict[str, Any]) -> Any:
    group = (row.get("inputs") or {}).get("profile_assignment_group") or {}
    return group.get("onboardingProfile") if isinstance(group, dict) else None


def _validate_document_shape(document: dict[str, Any]) -> dict[str, Any] | None:
    def bad(path: str, reason: str) -> dict[str, Any]:
        return _error(
            "telemetry_document_malformed",
            "The current Streaming Telemetry document has an unexpected shape.",
            path=path,
            reason=reason,
        )

    profiles = document.get("onboardingProfiles")
    if not isinstance(profiles, list):
        return bad("$.onboardingProfiles", "missing or not a list")
    seen: set[str] = set()
    for index, profile in enumerate(profiles):
        name = profile.get("profileName") if isinstance(profile, dict) else None
        if not isinstance(name, str) or not name:
            return bad(f"$.onboardingProfiles[{index}].profileName", "missing")
        if name in seen:
            return bad(f"$.onboardingProfiles[{index}].profileName", "duplicate")
        seen.add(name)

    rows = document.get("profileAssignment")
    if not isinstance(rows, list):
        return bad("$.profileAssignment", "missing or not a list")
    queries: set[str] = set()
    for index, row in enumerate(rows):
        query = _assignment_query(row)
        if query is None:
            return bad(f"$.profileAssignment[{index}].tags.query", "missing")
        if query in queries:
            return bad(f"$.profileAssignment[{index}].tags.query", "duplicate")
        queries.add(query)
        inputs = row.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            return bad(f"$.profileAssignment[{index}].inputs", "not an object")
        group = (inputs or {}).get("profile_assignment_group")
        if group is not None and not isinstance(group, dict):
            return bad(
                f"$.profileAssignment[{index}].inputs.profile_assignment_group",
                "not an object",
            )
    return None


def _dangling_refs(document: dict[str, Any]) -> set[tuple[str, str]]:
    names = {p["profileName"] for p in document["onboardingProfiles"]}
    refs: set[tuple[str, str]] = set()
    for row in document["profileAssignment"]:
        target = _assigned_profile(row)
        if isinstance(target, str) and target not in names:
            refs.add((_assignment_query(row) or "", target))
    return refs


def _pick_entry(
    entries: Any, key: str, wanted: str | None, what: str, profile_name: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pick the one entry ``wanted`` names, or the only entry when unnamed."""
    if not isinstance(entries, list) or not entries:
        return None, _error(
            "telemetry_patch_target_missing",
            f"Profile has no {what} entries to patch.",
            profileName=profile_name,
            target=what,
        )
    if wanted is None:
        if len(entries) != 1:
            return None, _error(
                "telemetry_patch_ambiguous",
                f"Profile has several {what} entries; pass cluster_profile_name.",
                profileName=profile_name,
                target=what,
                count=len(entries),
            )
        entry = entries[0]
    else:
        matches = [e for e in entries if isinstance(e, dict) and e.get(key) == wanted]
        if len(matches) != 1:
            return None, _error(
                "telemetry_patch_target_missing",
                f"No single {what} entry matches cluster_profile_name.",
                profileName=profile_name,
                target=what,
                cluster_profile_name=wanted,
                matches=len(matches),
            )
        entry = matches[0]
    if not isinstance(entry, dict):
        return None, _error(
            "telemetry_document_malformed",
            f"A {what} entry is not an object.",
            profileName=profile_name,
        )
    return entry, None


def _apply_patch(profile: dict[str, Any], op: dict[str, Any]) -> dict[str, Any] | None:
    patch = op["patch"]
    name = op["profileName"]
    wanted = op.get("cluster_profile_name")

    if _DEV_PATCH_KEYS & patch.keys():
        dev, err = _pick_entry(
            profile.get("dev_collection"),
            "cluster_profile_name",
            wanted,
            "dev_collection",
            name,
        )
        if err:
            return err
        assert dev is not None
        for key in _DEV_PATCH_KEYS & patch.keys():
            dev[key] = patch[key]

    if _CLUSTER_PATCH_KEYS & patch.keys():
        cluster, err = _pick_entry(
            profile.get("cluster_profiles"),
            "cvopt_name",
            wanted,
            "cluster_profiles",
            name,
        )
        if err:
            return err
        assert cluster is not None
        if "addr1" in patch:
            group = cluster.get("cvopt_cvaddr_group")
            if group is None:
                group = cluster["cvopt_cvaddr_group"] = {}
            if not isinstance(group, dict):
                return _error(
                    "telemetry_document_malformed",
                    "cvopt_cvaddr_group is not an object.",
                    profileName=name,
                )
            group["addr1"] = patch["addr1"]
        if "ingestPort" in patch:
            cluster["ingestPort"] = patch["ingestPort"]

    if "disableAAA" in patch:
        resolver = profile.get("disableAAAResolver")
        if resolver is None:
            resolver = profile["disableAAAResolver"] = {}
        if not isinstance(resolver, dict):
            return _error(
                "telemetry_document_malformed",
                "disableAAAResolver is not an object.",
                profileName=name,
            )
        resolver["disableAAA"] = patch["disableAAA"]

    if "multi_cluster" in patch:
        profile["multi_cluster"] = patch["multi_cluster"]
    return None


def apply_operations(
    document: dict[str, Any], ops: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Apply normalized ``ops`` to a deep copy of ``document``.

    Returns ``(after, summary, error_or_None)`` where ``summary`` has
    ``added`` / ``replaced`` / ``removed`` label lists (``profile:<name>``,
    ``assignment:<query>``).
    """
    summary: dict[str, list[str]] = {"added": [], "replaced": [], "removed": []}
    shape_error = _validate_document_shape(document)
    if shape_error:
        return document, summary, shape_error

    after = copy.deepcopy(document)
    profiles: list[dict[str, Any]] = after["onboardingProfiles"]
    rows: list[dict[str, Any]] = after["profileAssignment"]

    def note(kind: str, label: str) -> None:
        if label in summary["added"] or label in summary["replaced"]:
            return
        summary[kind].append(label)

    for op in ops:
        kind = op["op"]
        if kind == "upsert_onboarding_profile":
            profile = copy.deepcopy(op["profile"])
            name = profile["profileName"]
            label = f"profile:{name}"
            for position, existing in enumerate(profiles):
                if existing.get("profileName") == name:
                    profiles[position] = profile
                    note("replaced", label)
                    break
            else:
                profiles.append(profile)
                summary["added"].append(label)
                if label in summary["removed"]:
                    summary["removed"].remove(label)

        elif kind == "remove_onboarding_profile":
            name = op["profileName"]
            before_len = len(profiles)
            profiles[:] = [p for p in profiles if p.get("profileName") != name]
            if len(profiles) == before_len:
                return (
                    after,
                    summary,
                    _error(
                        "telemetry_profile_not_found",
                        "remove_onboarding_profile names a profile that does not exist.",
                        profileName=name,
                    ),
                )
            label = f"profile:{name}"
            for bucket in ("added", "replaced"):
                if label in summary[bucket]:
                    summary[bucket].remove(label)
            summary["removed"].append(label)

        elif kind == "patch_onboarding_profile":
            name = op["profileName"]
            target = next((p for p in profiles if p.get("profileName") == name), None)
            if target is None:
                return (
                    after,
                    summary,
                    _error(
                        "telemetry_profile_not_found",
                        "patch_onboarding_profile names a profile that does not exist.",
                        profileName=name,
                    ),
                )
            patch_error = _apply_patch(target, op)
            if patch_error:
                return after, summary, patch_error
            note("replaced", f"profile:{name}")

        else:  # set_profile_assignment
            query = op["query"]
            label = f"assignment:{query}"
            row = next((r for r in rows if _assignment_query(r) == query), None)
            if row is None:
                rows.append(
                    {
                        "inputs": {
                            "profile_assignment_group": {
                                # Live rows all carry default_config true.
                                "default_config": op.get("default_config", True),
                                "onboardingProfile": op["onboardingProfile"],
                            }
                        },
                        "tags": {"query": query},
                    }
                )
                summary["added"].append(label)
                continue
            inputs = row.get("inputs")
            if inputs is None:
                inputs = row["inputs"] = {}
            group = inputs.get("profile_assignment_group")
            if group is None:
                group = inputs["profile_assignment_group"] = {}
            group["onboardingProfile"] = op["onboardingProfile"]
            if "default_config" in op:
                group["default_config"] = op["default_config"]
            note("replaced", label)

    # Refuse references this call broke or introduced; pre-existing dangling
    # rows are left alone so they cannot block unrelated edits.
    new_dangling = _dangling_refs(after) - _dangling_refs(document)
    if new_dangling:
        return (
            after,
            summary,
            _error(
                "telemetry_profile_reference_dangling",
                "A profileAssignment row would name a profile that does not exist.",
                references=[
                    {"query": query, "onboardingProfile": target}
                    for query, target in sorted(new_dangling)
                ],
            ),
        )
    return after, summary, None


# --- entry point ------------------------------------------------------------


def set_cvp_telemetry_inputs(
    datadict: dict[str, Any],
    workspace_id: str,
    expected_inputs_sha256: str,
    operations: list[dict[str, Any]],
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Compare-and-set Streaming Telemetry onboarding profiles / assignments.

    ``expected_inputs_sha256`` is the ``inputs_sha256`` from
    ``get_cvp_studio_inputs`` for the document the caller read. Dry-run unless
    ``confirm=True`` with the matching ``preview_token``. Never submits.
    """
    tool = TOOL_NAME

    # Same early order as the MSS / Campus writers: gate, id, digest, ops,
    # lint, then the shared pipeline (which re-checks the first three).
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
            details={"studio_id": TELEMETRY_STUDIO_ID},
            workspace_id=workspace or None,
        )

    if digest_malformed(expected_inputs_sha256):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "expected_inputs_sha256_required",
            "expected_inputs_sha256 must be the 64-hex inputs_sha256 from get_cvp_studio_inputs.",
            details={"studio_id": TELEMETRY_STUDIO_ID},
            workspace_id=workspace,
        )

    ops, op_error = normalize_operations(operations)
    if op_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            op_error["code"],
            op_error["message"],
            details={"studio_id": TELEMETRY_STUDIO_ID, **op_error["details"]},
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
        return apply_operations(document, ops)

    return run_root_inputs_cas(
        datadict,
        workspace,
        expected_inputs_sha256,
        config=_CAS_CONFIG,
        operations=ops,
        apply=apply,
        confirm=confirm,
        preview_token_value=preview_token_value,
    )
