# Bucket R1 — defect-first code review (Phase 2.0)

Status: **done** (findings applied on `feat/studios-phase2-impl`)
Owner: cursor-named-sonnet (Task, read-only)
Scope: `cvp_mcp/write_access.py`, `cvp_mcp/grpc/resource_write.py`,
`cvp_mcp/grpc/studios_write.py`, `cloudvision_mcp.py` write registration,
`tests/test_write_access.py`, `tests/test_resource_write.py`,
`tests/test_studios_write.py`.
Spec: `docs/studios-phase2-spec.md`.

No live CVP POSTs. Unit tests mock HTTP.

## Verdict

2.0 is implementable and, after coordinator edits, fail-closed on the
defects below. Remaining items are test/docs tightness, not new write
surfaces.

## Findings

### Important (applied)

| ID | Defect | Fix |
| --- | --- | --- |
| R1-I1 | Truncated or skipped NDJSON Inputs could still reach tree-diff + POST (full-document replace of a partial tree). | `_load_root_inputs` returns `preflight_failed` when any warning contains `truncated_to_` or `ndjson_skip_invalid_line`. |
| R1-I2 | Description CAS did not GET the workspace; a submitted/abandoned id could still POST Inputs. | CAS now requires `WORKSPACE_STATE_PENDING` via `_read_workspace`. |
| R1-I3 | `build_cvp_workspace` preview token omitted `request_id`, so confirm could start a different build id than the dry-run showed. | Token args include `request_id`; `confirm=True` without that id is `invalid_request_id`. |
| R1-I4 | DELETE accepted extra query keys (`{"other": "x"}`) and could confuse the workspace-id extractor. | Only `key.workspaceId` / `key.workspace_id`; anything else is `invalid_params`. |
| R1-I5 | Description CAS did not GET the Access studio; `immutable` / `from_package` were caller-trust. | `get_cvp_studio` on `studio-campus-access-interfaces`; refuse `studio_immutable` / `studio_from_package`. |

### Minor (open)

| ID | Note |
| --- | --- |
| R1-M1 | `build_cvp_workspace` docstring originally said `request_id` was *not* in the token. Corrected to match the bound-token behavior. |
| R1-M2 | Spec still says confirm *may* mint a UUIDv4 if `request_id` is omitted. Code is stricter (must echo preview). Prefer spec follow-up over relaxing code. |
| R1-M3 | Write tools register at import when env is `"1"`. Spec-required; changing env at runtime still needs process restart. |

## Tests added/aligned

- Pending workspace + studio GET in description fixtures (`_mocked`).
- Build confirm uses the preview `request_id`.
- Extra DELETE query key → `invalid_params`.
- Truncated Inputs stream refuses.
- Confirm without `request_id`; non-pending workspace on CAS; immutable / packaged studio.
