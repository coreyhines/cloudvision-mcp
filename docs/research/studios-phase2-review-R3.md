# Bucket R3 — contract vs spec (Phase 2.0)

Status: **done** (findings applied on `feat/studios-phase2-impl`)
Owner: claude-opus (planned claude-cli; executed as Cursor Task after CLI farm
flag parsing failed on earlier farms)
Spec: `docs/studios-phase2-spec.md`.

## Canonical strings

| Spec | Code | Match |
| --- | --- | --- |
| `CLOUDVISION_MCP_ALLOW_WRITES` exact `"1"` | `write_access.WRITES_ENV` | yes |
| `CLOUDVISION_MCP_ALLOW_SUBMIT` exact `"1"` | `SUBMIT_ENV`; submit off while `SUBMIT_STALENESS_FIELD is None` | yes |
| `tool_envelope(..., obj=)` → `object` | write helpers use `_outcome` / `_refused` → `object` | yes |
| `ws-mcp-` prefix | `validate_workspace_id` | yes |
| Mainline `""` never written | empty id → `workspace_id_required` | yes |
| `REQUEST_START_BUILD` | `resource_write.REQUEST_START_BUILD` | yes |
| `WORKSPACE_STATE_PENDING` | `WORKSPACE_STATE_PENDING` | yes |
| `studio-campus-access-interfaces` | `ACCESS_INTERFACE_STUDIO_ID` | yes |
| Locator `interface:<IfName>@<serial>` | `interface:{port}@{device}` | yes |
| Root Inputs `path: {}` / `values: []` | `_is_root_path`; POST `path.values: []` | yes |
| `adapterDetails.description` | tree-diff leaf | yes |
| `immutable` / `from_package` (wire `fromPackage`) | Phase 1 mapper + CAS GET | yes |
| Write tools only if env `"1"` at import | `if writes_enabled():` in `cloudvision_mcp.py` | yes |
| Submit unregistered | no `submit_cvp_workspace` tool | yes |

## Gaps vs spec prose

| ID | Spec text | Code | Resolution |
| --- | --- | --- | --- |
| R3-I1 | Confirm *may* generate UUIDv4 if `request_id` omitted | Confirm **requires** preview `request_id` (bound in token) | Keep code; spec should say "must echo preview id" |
| R3-I2 | Description CAS "refuse immutable / from_package" | Was caller-side; now GET studio | applied |
| R3-I3 | Fail closed on unknown Inputs GET | Truncation warning now refuses | applied |
| R3-M1 | `get_cvp_studio_assigned_tags` "can ship with 2.0 as best-effort" | Not implemented | Acceptable; not on description CAS path |
| R3-M2 | Audit INFO: tool, workspace, studio, request_id | logging.error on helper refusals; not a structured audit line | follow-up |

## Out of scope (correctly absent)

Studio create/delete, tag assign, generic `set_cvp_studio_inputs`, Change Control writes, live CVaaS POST.
