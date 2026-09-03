"""Studios Phase 2.3: compare-and-set MSS Service policy inputs at the root row.

Library only. Registration stays behind the writes env gate in
``cloudvision_mcp.py``. Spec: ``docs/studios-phase2-final-spec.md`` §D.

MSS Service (``studio-mss-service``) keeps its whole input tree in **one**
Inputs Resource row at ``path.values []``. The 2.1 generic writer refuses the
root on purpose, and its description-only allowlist could never express "add a
drop rule". This module is the 2.0 description-CAS shape applied to that root
row, with three differences:

* the edit is a bounded **operation vocabulary** (``upsert`` / ``remove`` /
  ``set_policy_rules`` over ``staticGroups`` / ``services`` / ``rules`` /
  ``policies``) instead of a single leaf;
* the CAS is a **digest** of the whole current document
  (``expected_inputs_sha256``, from ``get_cvp_studio_inputs``) instead of one
  leaf's current value;
* after the ops are applied the structural diff must stay inside the four
  writable collections (``tree_diff_outside_mss_scope``), which is the defence
  against a buggy applier touching ``securityDomains`` or a ``hidden*Mapper``.

Order of checks, everything before the first HTTP request:

1. ``writes_enabled()``, draft workspace id, well-formed digest, operations
   (shape, per-collection entry schema, EOS lint on the ops themselves);
2. workspace GET (pending), studio GET (overlay then mainline; not immutable /
   packaged);
3. root read (overlay else mainline; any truncation fails closed), digest CAS;
4. apply ops to a deep copy, referential integrity, blast-radius refusal,
   diff scope;
5. ``confirm=False`` → preview with a ``preview_token`` bound to the after
   digest; ``confirm=True`` → token check, one POST at ``path.values []``.

Never submits. The human reviews the built workspace in the CVP UI.
"""

from __future__ import annotations

import copy
import ipaddress
import json
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
    _disruptive_hits,
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

TOOL_NAME = "set_cvp_mss_policy_inputs"
MSS_STUDIO_ID = "studio-mss-service"

# The only top-level keys an operation may change. Everything else in the root
# document (AGNI groups, security domains, monitor objects, hidden mappers, …)
# is copied through untouched and guarded by the diff scope check.
WRITABLE_COLLECTIONS: tuple[str, ...] = (
    "staticGroups",
    "services",
    "rules",
    "policies",
)

ANY = "<any>"
MAX_OPERATIONS = 20
_MAX_REPORTED = 10

_OPS: frozenset[str] = frozenset({"upsert", "remove", "set_policy_rules"})
_ACTIONS: frozenset[str] = frozenset({"forward", "drop"})
_PROTOCOLS: frozenset[str] = frozenset({"TCP/UDP", "ICMP"})
_CONFIG_PROTOCOLS: frozenset[str] = frozenset({"tcp", "udp", "icmp"})

# Accepted keys per collection. Derived from the live root row captured
# 2026-09-02 (tests/fixtures/inputs_mss_service_root_2026-09-02.json); the
# fixture wins over the draft spec wherever they disagreed (``monitorName`` is
# stored on drop rules too, rules carry no ``description``, service
# configurations may omit ``icmpTypes``).
_GROUP_KEYS = frozenset({"name", "membership"})
_SERVICE_KEYS = frozenset({"name", "protocols", "configurations"})
_SERVICE_CONFIG_KEYS = frozenset(
    {"protocol", "sourceports", "destinationports", "icmpTypes"}
)
_SERVICE_CONFIG_REQUIRED = ("protocol", "sourceports", "destinationports")
_RULE_KEYS = frozenset(
    {
        "name",
        "action",
        "sources",
        "destinations",
        "services",
        "packet",
        "direction",
        "monitorName",
    }
)
_RULE_REQUIRED = (
    "name",
    "action",
    "sources",
    "destinations",
    "services",
    "packet",
    "direction",
)
_POLICY_KEYS = frozenset({"name", "description", "policyRules"})


class _Invalid(Exception):
    """One validation failure with the JSON-ish path of the offending value."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


# --- small validators -------------------------------------------------------


def _is_any(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == ANY


def _require_name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(path, "name must be a non-empty string")
    if value != value.strip():
        raise _Invalid(path, "name must not have surrounding whitespace")
    if _is_any(value):
        raise _Invalid(path, f"{ANY!r} is a reserved name")
    return value


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


def _require_name_list(value: Any, path: str, *, allow_any: bool) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _Invalid(path, "must be a non-empty list of names")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise _Invalid(f"{path}[{index}]", "must be a non-empty string")
        if _is_any(item) and not allow_any:
            raise _Invalid(f"{path}[{index}]", f"{ANY!r} not allowed here")
        out.append(item)
    return out


def _validate_ports(value: Any, path: str) -> None:
    """``all``, a single port, ``a-b``, or a comma list of those (1–65535)."""
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(path, "must be 'all', a port, a range a-b, or a comma list")
    if value.strip() == "all":
        return
    for token in value.split(","):
        token = token.strip()
        parts = token.split("-")
        if len(parts) not in (1, 2) or not all(p.isdigit() for p in parts):
            raise _Invalid(path, f"bad port token {token!r}")
        numbers = [int(p) for p in parts]
        if any(not 1 <= n <= 65535 for n in numbers) or numbers != sorted(numbers):
            raise _Invalid(path, f"bad port token {token!r}")


def _validate_icmp_types(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(path, "must be 'all' or a comma list of 0-255")
    if value.strip() == "all":
        return
    for token in value.split(","):
        token = token.strip()
        if not token.isdigit() or not 0 <= int(token) <= 255:
            raise _Invalid(path, f"bad icmp type {token!r}")


def _validate_cidr(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise _Invalid(path, "must be a CIDR string")
    try:
        network = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise _Invalid(path, f"bad CIDR: {exc}") from None
    # ``0.0.0.0/0`` is ``<any>`` under another name and would defeat the
    # blast-radius refusal on drop rules.
    if network.prefixlen == 0:
        raise _Invalid(path, f"prefix length 0 is not allowed; use {ANY!r} in the rule")


# --- entry schema -----------------------------------------------------------


def _validate_entry(collection: str, entry: Any, path: str) -> None:
    """Shape and type checks for one ``upsert`` entry. Raises :class:`_Invalid`."""
    if collection == "staticGroups":
        _require_keys(entry, _GROUP_KEYS, ("name", "membership"), path)
        _require_name(entry["name"], f"{path}.name")
        membership = entry["membership"]
        _require_keys(
            membership, frozenset({"members"}), ("members",), f"{path}.membership"
        )
        members = membership["members"]
        if not isinstance(members, list) or not members:
            raise _Invalid(
                f"{path}.membership.members", "must be a non-empty list of CIDRs"
            )
        for index, member in enumerate(members):
            _validate_cidr(member, f"{path}.membership.members[{index}]")
        return

    if collection == "services":
        _require_keys(
            entry, _SERVICE_KEYS, ("name", "protocols", "configurations"), path
        )
        _require_name(entry["name"], f"{path}.name")
        if entry["protocols"] not in _PROTOCOLS:
            raise _Invalid(f"{path}.protocols", f"must be one of {sorted(_PROTOCOLS)}")
        configurations = entry["configurations"]
        if not isinstance(configurations, list) or not configurations:
            raise _Invalid(f"{path}.configurations", "must be a non-empty list")
        for index, config in enumerate(configurations):
            cpath = f"{path}.configurations[{index}]"
            _require_keys(config, _SERVICE_CONFIG_KEYS, _SERVICE_CONFIG_REQUIRED, cpath)
            if config["protocol"] not in _CONFIG_PROTOCOLS:
                raise _Invalid(
                    f"{cpath}.protocol", f"must be one of {sorted(_CONFIG_PROTOCOLS)}"
                )
            _validate_ports(config["sourceports"], f"{cpath}.sourceports")
            _validate_ports(config["destinationports"], f"{cpath}.destinationports")
            if "icmpTypes" in config:
                _validate_icmp_types(config["icmpTypes"], f"{cpath}.icmpTypes")
        return

    if collection == "rules":
        _require_keys(entry, _RULE_KEYS, _RULE_REQUIRED, path)
        _require_name(entry["name"], f"{path}.name")
        if entry["action"] not in _ACTIONS:
            raise _Invalid(f"{path}.action", f"must be one of {sorted(_ACTIONS)}")
        _require_name_list(entry["sources"], f"{path}.sources", allow_any=True)
        _require_name_list(
            entry["destinations"], f"{path}.destinations", allow_any=True
        )
        _require_name_list(entry["services"], f"{path}.services", allow_any=True)
        if entry["packet"] != "any":
            raise _Invalid(f"{path}.packet", "must be 'any'")
        if not isinstance(entry["direction"], bool):
            raise _Invalid(f"{path}.direction", "must be a boolean")
        if "monitorName" in entry and (
            not isinstance(entry["monitorName"], str)
            or not entry["monitorName"].strip()
        ):
            raise _Invalid(f"{path}.monitorName", "must be a non-empty string")
        return

    if collection == "policies":
        _require_keys(entry, _POLICY_KEYS, ("name", "policyRules"), path)
        _require_name(entry["name"], f"{path}.name")
        if "description" in entry and not isinstance(
            entry["description"], (str, type(None))
        ):
            raise _Invalid(f"{path}.description", "must be a string or null")
        _validate_policy_rules(entry["policyRules"], f"{path}.policyRules")
        return

    raise _Invalid(f"{path}", "collection not allowed")


def _validate_policy_rules(value: Any, path: str) -> list[str]:
    names = _require_name_list(value, path, allow_any=False)
    if len(set(names)) != len(names):
        raise _Invalid(path, "rule names must be unique")
    return names


# --- operations -------------------------------------------------------------


def _normalize_operations(
    operations: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Structural + schema validation of ``operations`` (no document needed).

    Returns ``(normalized_ops, refusal)`` where ``refusal`` is
    ``{"code", "message", "details"}`` or ``None``.
    """
    if not isinstance(operations, list) or not operations:
        return [], {
            "code": "mss_operations_required",
            "message": "operations must be a non-empty list.",
            "details": {},
        }
    if len(operations) > MAX_OPERATIONS:
        return [], {
            "code": "mss_operations_too_many",
            "message": f"At most {MAX_OPERATIONS} operations per call; this is a hand-edit tool.",
            "details": {"count": len(operations), "max": MAX_OPERATIONS},
        }

    normalized: list[dict[str, Any]] = []
    try:
        for index, raw in enumerate(operations):
            path = f"operations[{index}]"
            if not isinstance(raw, dict):
                raise _Invalid(path, "must be an object")
            op = raw.get("op")
            if op not in _OPS:
                raise _Invalid(f"{path}.op", f"must be one of {sorted(_OPS)}")

            if op == "set_policy_rules":
                _require_keys(
                    raw,
                    frozenset({"op", "policy", "policy_rules"}),
                    ("policy", "policy_rules"),
                    path,
                )
                policy = _require_name(raw["policy"], f"{path}.policy")
                rules = _validate_policy_rules(
                    raw["policy_rules"], f"{path}.policy_rules"
                )
                normalized.append({"op": op, "policy": policy, "policy_rules": rules})
                continue

            collection = raw.get("collection")
            if collection not in WRITABLE_COLLECTIONS:
                return [], {
                    "code": "mss_collection_not_allowed",
                    "message": f"collection must be one of {list(WRITABLE_COLLECTIONS)}.",
                    "details": {"path": f"{path}.collection", "collection": collection},
                }

            if op == "remove":
                _require_keys(
                    raw, frozenset({"op", "collection", "name"}), ("name",), path
                )
                if collection == "policies":
                    raise _Invalid(
                        path,
                        "policies cannot be removed here (hiddenPolicyIdMapper is out of scope)",
                    )
                name = _require_name(raw["name"], f"{path}.name")
                normalized.append({"op": op, "collection": collection, "name": name})
                continue

            _require_keys(
                raw, frozenset({"op", "collection", "entry"}), ("entry",), path
            )
            entry = raw["entry"]
            _validate_entry(collection, entry, f"{path}.entry")
            normalized.append(
                {"op": op, "collection": collection, "entry": copy.deepcopy(entry)}
            )
    except _Invalid as exc:
        return [], {
            "code": "mss_operation_invalid",
            "message": "An operation failed validation.",
            "details": {"path": exc.path, "reason": exc.message},
        }
    return normalized, None


def _entries(document: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    value = document.get(collection)
    if not isinstance(value, list):
        value = []
        document[collection] = value
    return value


def _names(document: dict[str, Any], collection: str) -> list[str]:
    return [
        _as_str(item.get("name"))
        for item in document.get(collection) or []
        if isinstance(item, dict)
    ]


def _apply_operations(
    document: dict[str, Any], operations: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, list[str]], dict[str, Any] | None]:
    """Apply validated ops to a deep copy. Returns ``(after, summary, refusal)``."""
    after = copy.deepcopy(document)
    summary: dict[str, list[str]] = {"added": [], "replaced": [], "removed": []}
    accepted_names = set(_names(after, "acceptedGroups"))

    for index, op in enumerate(operations):
        path = f"operations[{index}]"
        kind = op["op"]

        if kind == "set_policy_rules":
            policies = _entries(after, "policies")
            for policy in policies:
                if isinstance(policy, dict) and policy.get("name") == op["policy"]:
                    policy["policyRules"] = list(op["policy_rules"])
                    summary["replaced"].append(f"policies:{op['policy']}")
                    break
            else:
                return (
                    after,
                    summary,
                    {
                        "code": "mss_entry_not_found",
                        "message": "set_policy_rules names a policy that does not exist.",
                        "details": {"path": f"{path}.policy", "policy": op["policy"]},
                    },
                )
            continue

        collection = op["collection"]
        entries = _entries(after, collection)

        if kind == "remove":
            before = len(entries)
            entries[:] = [
                e
                for e in entries
                if not (isinstance(e, dict) and e.get("name") == op["name"])
            ]
            if len(entries) == before:
                return (
                    after,
                    summary,
                    {
                        "code": "mss_entry_not_found",
                        "message": "remove names an entry that does not exist.",
                        "details": {
                            "path": f"{path}.name",
                            "collection": collection,
                            "name": op["name"],
                        },
                    },
                )
            summary["removed"].append(f"{collection}:{op['name']}")
            continue

        entry = op["entry"]
        name = entry["name"]
        if collection == "staticGroups" and name in accepted_names:
            return (
                after,
                summary,
                {
                    "code": "mss_operation_invalid",
                    "message": "A static group may not share a name with an AGNI accepted group.",
                    "details": {
                        "path": f"{path}.entry.name",
                        "reason": "collides with acceptedGroups",
                        "name": name,
                    },
                },
            )
        for position, existing in enumerate(entries):
            if isinstance(existing, dict) and existing.get("name") == name:
                entries[position] = copy.deepcopy(entry)
                summary["replaced"].append(f"{collection}:{name}")
                break
        else:
            if collection == "policies":
                return (
                    after,
                    summary,
                    {
                        "code": "mss_entry_not_found",
                        "message": "New policies cannot be created here (hiddenPolicyIdMapper is out of scope); upsert an existing policy or use set_policy_rules.",
                        "details": {"path": f"{path}.entry.name", "policy": name},
                    },
                )
            entries.append(copy.deepcopy(entry))
            summary["added"].append(f"{collection}:{name}")

    return after, summary, None


# --- result checks ----------------------------------------------------------


def _rule_is_all_any(rule: dict[str, Any]) -> bool:
    return all(
        isinstance(rule.get(field), list)
        and len(rule[field]) == 1
        and _is_any(rule[field][0])
        for field in ("sources", "destinations", "services")
    )


def _endpoints_any(rule: dict[str, Any]) -> bool:
    return all(
        isinstance(rule.get(field), list)
        and len(rule[field]) == 1
        and _is_any(rule[field][0])
        for field in ("sources", "destinations")
    )


def _check_result(
    after: dict[str, Any], touched: set[str]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Referential integrity, blast radius and advisory warnings on the result.

    ``touched`` holds ``rules:<name>`` for rules this call added or replaced;
    the ``mss_rule_broad`` warning is scoped to those so a pre-existing broad
    rule does not nag on every unrelated edit. Refusals look at everything.
    """
    groups = set(_names(after, "staticGroups")) | set(_names(after, "acceptedGroups"))
    services = set(_names(after, "services"))
    rules = {
        r["name"]: r
        for r in after.get("rules") or []
        if isinstance(r, dict) and "name" in r
    }
    monitors = set(_names(after, "monitorObjects"))

    unresolved: list[dict[str, str]] = []
    for rule_name, rule in rules.items():
        for field, universe in (
            ("sources", groups),
            ("destinations", groups),
            ("services", services),
        ):
            for ref in rule.get(field) or []:
                if not _is_any(ref) and ref not in universe:
                    unresolved.append(
                        {
                            "referrer": f"rules:{rule_name}",
                            "field": field,
                            "missing": ref,
                        }
                    )
        monitor = rule.get("monitorName")
        if monitor is not None and monitor not in monitors:
            return {
                "code": "mss_operation_invalid",
                "message": "monitorName must name an existing monitor object.",
                "details": {
                    "path": f"rules:{rule_name}.monitorName",
                    "reason": "unknown monitor object",
                    "monitorName": monitor,
                },
            }, []
    for policy in after.get("policies") or []:
        if not isinstance(policy, dict):
            continue
        policy_rules = policy.get("policyRules") or []
        if not policy_rules:
            return {
                "code": "mss_operation_invalid",
                "message": "A policy must keep at least one rule.",
                "details": {
                    "path": f"policies:{policy.get('name')}.policyRules",
                    "reason": "empty",
                },
            }, []
        for ref in policy_rules:
            if ref not in rules:
                unresolved.append(
                    {
                        "referrer": f"policies:{policy.get('name')}",
                        "field": "policyRules",
                        "missing": ref,
                    }
                )
    if unresolved:
        return {
            "code": "mss_reference_unresolved",
            "message": "The result document references names that do not exist.",
            "details": {
                "unresolved": unresolved[:_MAX_REPORTED],
                "count": len(unresolved),
            },
        }, []

    for rule_name, rule in rules.items():
        if rule.get("action") == "drop" and _rule_is_all_any(rule):
            return {
                "code": "mss_rule_too_broad",
                "message": "A drop rule with <any> sources, destinations and services drops the fabric at ingress.",
                "details": {"rule": rule_name},
            }, []

    warnings: list[str] = []
    for rule_name, rule in rules.items():
        if (
            f"rules:{rule_name}" in touched
            and rule.get("action") == "drop"
            and _endpoints_any(rule)
        ):
            warnings.append(f"mss_rule_broad:{rule_name}")
    for policy in after.get("policies") or []:
        if not isinstance(policy, dict):
            continue
        ordered = [rules[n] for n in policy.get("policyRules") or [] if n in rules]
        for position, rule in enumerate(ordered):
            if rule.get("action") == "forward" and _rule_is_all_any(rule):
                if any(
                    later.get("action") == "drop" for later in ordered[position + 1 :]
                ):
                    warnings.append(
                        f"mss_rule_shadowed:{policy.get('name')}:{rule['name']}"
                    )
    return None, warnings


def _scope_violations(changed_paths: list[str]) -> list[str]:
    out: list[str] = []
    for path in changed_paths:
        if any(
            path == f"$.{c}" or path.startswith(f"$.{c}[") for c in WRITABLE_COLLECTIONS
        ):
            continue
        out.append(path)
    return out


def _digest_error(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return True
    return any(ch not in "0123456789abcdef" for ch in value)


# --- tool -------------------------------------------------------------------


def set_cvp_mss_policy_inputs(
    datadict: dict[str, Any],
    workspace_id: str,
    expected_inputs_sha256: str,
    operations: list[dict[str, Any]],
    confirm: bool = False,
    *,
    preview_token_value: str | None = None,
) -> dict[str, Any]:
    """Compare-and-set MSS Service groups / services / rules / policy order.

    ``expected_inputs_sha256`` is the ``inputs_sha256`` reported by
    ``get_cvp_studio_inputs`` for the document the caller read. Dry-run unless
    ``confirm=True`` with the matching ``preview_token``. Never submits.
    """
    tool = TOOL_NAME
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
            details={"studio_id": MSS_STUDIO_ID},
            workspace_id=workspace or None,
        )

    if _digest_error(expected_inputs_sha256):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "expected_inputs_sha256_required",
            "expected_inputs_sha256 must be the 64-hex inputs_sha256 from get_cvp_studio_inputs.",
            details={"studio_id": MSS_STUDIO_ID},
            workspace_id=workspace,
        )

    ops, op_error = _normalize_operations(operations)
    if op_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            op_error["code"],
            op_error["message"],
            details={"studio_id": MSS_STUDIO_ID, **op_error["details"]},
            workspace_id=workspace,
        )

    # The ops are the only source of new text in the document, so they are
    # what the EOS lint reads. Linting the after-document and subtracting the
    # before-document's hits would mask a new ``shutdown`` whenever any
    # pre-existing string already matched (review F-C1).
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
        datadict, MSS_STUDIO_ID, workspace
    )
    if studio_status is not None or not isinstance(studio_obj, dict):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "preflight_failed",
            "Studio GET failed; refusing Inputs write.",
            details={"studio_id": MSS_STUDIO_ID},
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
            details={"studio_id": MSS_STUDIO_ID},
            workspace_id=workspace,
        )

    document, source_workspace, load_error, warnings = _load_root_inputs(
        datadict, workspace, studio_id=MSS_STUDIO_ID
    )
    if load_error or not isinstance(document, dict):
        return _refused(
            tool,
            _INPUTS_SOURCE,
            load_error or "inputs_path_unresolved",
            "Could not read the MSS Service root Inputs document.",
            details={"studio_id": MSS_STUDIO_ID},
            workspace_id=workspace,
            warnings=warnings,
        )

    before_sha256 = inputs_sha256(document)
    if before_sha256 != expected_inputs_sha256:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "inputs_digest_mismatch",
            "The current MSS Service document does not match expected_inputs_sha256; re-read and retry.",
            details={
                "studio_id": MSS_STUDIO_ID,
                "current_inputs_sha256": before_sha256,
                "expected_inputs_sha256": expected_inputs_sha256,
                "inputs_source_workspace_id": source_workspace,
            },
            workspace_id=workspace,
            warnings=warnings,
        )

    after, entry_summary, apply_error = _apply_operations(document, ops)
    if apply_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            apply_error["code"],
            apply_error["message"],
            details={"studio_id": MSS_STUDIO_ID, **apply_error["details"]},
            workspace_id=workspace,
            warnings=warnings,
        )

    touched = {
        name
        for name in entry_summary["added"] + entry_summary["replaced"]
        if name.startswith("rules:")
    }
    result_error, result_warnings = _check_result(after, touched)
    if result_error:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            result_error["code"],
            result_error["message"],
            details={"studio_id": MSS_STUDIO_ID, **result_error["details"]},
            workspace_id=workspace,
            warnings=warnings,
        )
    warnings = [*warnings, *result_warnings]

    # Diff the reparsed canonical forms (sorted keys) so shared references and
    # non-round-tripping values are caught, same as the 2.0 / 2.1 writers.
    before_canonical = json.loads(canonical_json(document))
    after_canonical = json.loads(canonical_json(after))
    changed = _changed_leaf_paths(before_canonical, after_canonical)
    outside = _scope_violations(changed)
    if outside:
        return _refused(
            tool,
            _INPUTS_SOURCE,
            "tree_diff_outside_mss_scope",
            "The applied operations changed leaves outside the writable MSS collections.",
            details={
                "studio_id": MSS_STUDIO_ID,
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
            "studioId": MSS_STUDIO_ID,
            "workspaceId": workspace,
            "path": {"values": []},
        },
        # Wire key order is preserved on purpose; only the digest sorts keys.
        "inputs": json.dumps(after),
    }
    token_args = {
        "studio_id": MSS_STUDIO_ID,
        "workspace_id": workspace,
        "expected_inputs_sha256": expected_inputs_sha256,
        "operations": ops,
        "after_sha256": after_sha256,
    }
    fields: dict[str, Any] = {
        "operation": "set_mss_policy_inputs",
        "studio_id": MSS_STUDIO_ID,
        "inputs_source_workspace_id": source_workspace,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "operations_applied": len(ops),
        "operations": ops,
        "entries_added": entry_summary["added"],
        "entries_replaced": entry_summary["replaced"],
        "entries_removed": entry_summary["removed"],
        "changed_leaves": len(changed),
        "changed_leaf_paths": changed[:_MAX_REPORTED],
        "posted_at_root": True,
        "disruptive": False,
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
        next_action="build_cvp_workspace, then review the workspace diff in the CVP UI.",
        warnings=warnings,
    )
