# Phase 2 spec — adversarial review (2026-08-22)

Reviewers: coordinator (this file) plus three hostile subagents (safety, Phase 1
contract, implementability). Prior residual: `studios-support-rereview-RR2.md`.

**Verdict: not-ready as written.** The safety *intent* is right. The document would
still let an implementer (or an agent) ship the wrong env vars, the wrong enums, a
mainline-shaped write, or a root-tree wipe. The first operator job does not need
studio create/delete or submit.

## Critical (applied in spec rewrite)

| ID | Finding | Spec fix |
| --- | --- | --- |
| P2-C1 | Identifier soup: env vars (`CLOUDVISION_MCP_ALLOW_WRITES` vs `CLOUDVISION_MCP_ALLOW_WRITES`), envelope (`tool_envelope` vs `tool_envelope`), enums (`BUILD_STATE_SUCCESS` vs fixture `BUILD_STATE_SUCCESS`), studio id / serials / hostnames vs live Phase 1. | Canonical names table. One string per concept. |
| P2-C2 | RR2-C2 residual: `replace_all_inputs` still exists in the first drop; agents will pass it. | **2.0: flag does not exist.** Root path always refused. |
| P2-C3 | Helper denylist `start`/`schedule`/`change` at any depth will reject legitimate Inputs JSON (and miss a CC `start` if it is nested under a different key). | Denylist applies only to WorkspaceConfig/StudioConfig **envelope** keys, not InputsConfig `inputs` string. CC paths stay off the path allowlist. |
| P2-C4 | Worked example is the whole point of 2.0, but Inputs path encoding is still “fail if unknown.” Generic `set_cvp_studio_inputs` cannot enforce description-only. | **2.0 is `set_cvp_access_interface_description` (CAS + preserve siblings).** Blocked until Ethernet6 GET+POST fixture exists. Generic Inputs POST is 2.1. |
| P2-C5 | `create_cvp_studio` + `allow_disruptive` is a shutdown hole. Description edits do not need it. | Studio create/delete are **2.2**. No `allow_disruptive` in 2.0. |
| P2-C6 | Submit proof still cannot prove human review (RR2-C6). | Submit stays **2.1 and unregistered** until `lastModifiedAt` (or equal) is confirmed on live Workspace GET. |

## Important (applied)

| ID | Finding | Spec fix |
| --- | --- | --- |
| P2-I1 | First slice mixed description job with tag replace, studio upsert, submit. | Explicit 2.0 / 2.1 / 2.2. |
| P2-I2 | Delete “draft” has no enum. Live: `WORKSPACE_STATE_PENDING`. | Delete only that state. |
| P2-I3 | “Build in progress” unspecified. | Refuse build if `responses.values` has a non-terminal entry for this workspace. |
| P2-I4 | Envelope `obj=` vs `object` vs invented `tool_envelope`. | Phase 1 `tool_envelope(..., obj=)` → JSON key `object`. |
| P2-I5 | Tag assign still a full replace (RR2-C3). | 2.1 only; requires `expected_current_query`; `unassign_all` not in 2.0. |
| P2-I6 | Files `cvp_mcp/` vs repo `cvp_mcp/`. | Match repo. |
| P2-I7 | Audit “never log full inputs” but no redaction rule for secrets inside inputs. | Redact keys matching `password|secret|token|key` (names, not studio `key` ids). |
| P2-I8 | `confirm=True` on first call still skips the human reading the dry-run. | Spec cannot force two-step MCP; require dry-run output to include `disruptive` and `full_tree_replace`. 2.0 has no full-tree path. |

## Closed from RR2 (do not re-litigate)

- CC create/start/execute tools: still out.
- `request_id` UUIDv4, no `"b1"`.
- Submit ≠ success on HTTP 200.
- Client-side refuse of empty `workspace_id` (mainline): kept and helper-enforced.
- Path allowlist on helper: kept (was RR2-C4 hole; this draft had it, now scoped).

## Split

| Slice | Ships | Does not |
| --- | --- | --- |
| **2.0** | assigned-tags **read**; workspace create/delete-draft; **description CAS** (`set_cvp_access_interface_description`); build | generic Inputs POST, replace-all, tag assign, submit, studio CRUD |
| **2.1** | tag assign with expected-current (no unassign-all); generic Inputs POST; submit if `lastModifiedAt` confirmed | studio CRUD |
| **2.2** | studio create/delete; templates must never contain interface shutdown | CC writes; no `allow_disruptive` |

## Implementer follow-up (applied)

[Hostile implementer](8c2ba9e7-222d-458d-bdb0-903852cf1e9b) required a structural
description tool, a real write `object` error contract, fail-closed unknown
states, preview→confirm `request_id` handoff, and “blocked” status until the
Ethernet6 fixture exists. Those are now in `docs/studios-phase2-spec.md`.

## Contract follow-up (applied)

[Hostile contract reviewer](c6ffefc3-1cff-426e-8118-47ecd8f87e1f):
`adapterDetails.description` and the `720xp-48` serial were listed as canonical
without fixtures; `AssignedTags/all` was never probed; `REQUEST_SUBMIT_FORCE` /
`REQUEST_ROLLBACK` spellings are unverified. Canonical table and Open table now
treat those as capture gates. Env off-list includes unset/`""`. `request`
allowlist is observed-values-only.

## Safety follow-up (applied)

[Adversarial security reviewer](3bbe2458-94c6-48a2-965f-1d64db5efbc2):
`confirm=True` on the first call bypassed every dry-run row — now bound by
`preview_token`. Generic Inputs in 2.1 refuse changed keys outside `description`.
Submit registration requires `SUBMIT_STALENESS_FIELD`. Workspace ids must be
`ws-mcp-*` (case-insensitive `builtin-` deny). Preflight reads fail closed.
DELETE is path + encoded params, not a prefix match. Staleness is not human
review; UI + CC remain the human controls.
