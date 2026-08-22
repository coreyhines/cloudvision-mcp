# Spec: Phase 2.1 live-gap fixes (tags + generic Inputs)

Status: **ready to implement**. Does not add submit. Does not change 2.0 description CAS.

Parent: `docs/studios-phase2-spec.md`. Evidence: `docs/research/studios-phase2-followon-live-gap-analysis.md`.

## Goal

Make 2.1 tag assign and generic Inputs behave correctly on this CVaaS tenant:

- AssignedTags `/all` is live; many studios have **no row**.
- Access Interfaces stores **one** Inputs resource at `path.values []`. Nested JSON keys are not Resource paths.

## Non-goals

- Register `submit_cvp_workspace`.
- Change 2.0 `set_cvp_access_interface_description`.
- Tag API `tag/v1` (403 on this token).
- Invent AssignedTags rows for studios that have none.

## 1. AssignedTags read

`GET /api/resources/studio/v1/AssignedTags/all` is the URL (live 200). Keep NDJSON `result.value` parse.

After parse, client-filter `key.studioId` + `key.workspaceId` (mainline `""`).

| GET `/all` | Filter matches | Read result |
| --- | --- | --- |
| HTTP 4xx/5xx or transport error | — | `coverage="none"`, warning `assigned_tags_unavailable` (unchanged) |
| 200, 0 rows for this studio+workspace | **0** | `coverage="full"`, `items: [{studio_id, workspace_id, query: ""}]`, **no** `assigned_tags_unavailable` |
| 200, 1 row | 1 | `coverage="full"`, that `query` |
| 200, >1 row | >1 | `coverage="none"`, `assigned_tags_ambiguous` (assign must refuse) |

Do not copy UUID workspace rows onto mainline `""`.

## 2. AssignedTags assign

Keep: writes gate, `ws-mcp-*` pending, empty **new** `query` → `empty_query_forbidden`, preview token.

Change: “no row” is not unavailable.

| Current (after §1) | `expected_current_query` | Action |
| --- | --- | --- |
| `query=""` (no row) | `""` | Preview/POST first assignment |
| `query=""` | non-empty | `current_query_mismatch` |
| `query="foo"` | `"foo"` | Preview/POST replace |
| `query="foo"` | other | `current_query_mismatch` |
| unavailable / read_failed | any | refuse `assigned_tags_unavailable` / `assigned_tags_read_failed` |
| ambiguous | any | refuse `assigned_tags_ambiguous` |

POST body (same path already allowlisted):

```json
{
  "key": { "studioId": "<id>", "workspaceId": "<ws-mcp-…>" },
  "query": "<new query>"
}
```

Workspace id on POST is the **draft**, never `""`.

## 3. Generic Inputs paths

`path_values` is **Resource** `Inputs.key.path.values`, not a JSON pointer into `inputs`.

`get_cvp_studio_inputs` already returns `path_values` per row. Use that list.

On lookup miss, refuse `inputs_path_not_found` with:

```json
{ "studio_id": "…", "path_values": ["campus"], "available_path_values": [[]] }
```

`available_path_values` is the list of lists from Inputs/all for that studio (workspace overlay first, then mainline). Do not expand JSON keys.

Empty `path_values` / `[]` still `root_path_forbidden`. If `available_path_values` is only `[]`, `next_action` must say: use `set_cvp_access_interface_description` for description CAS; generic Inputs cannot edit this studio’s only row.

Do **not** add a generic root POST. That would bypass 2.0 CAS.

## 4. Overlay studio GET

Any 2.1/2.2 helper that needs studio flags must GET:

1. `Studio?key.studioId=&key.workspaceId=<draft>`
2. if coverage none, `key.workspaceId=`

`studio_crud.py` already does this. `studio_inputs_generic.py` currently calls `get_cvp_studio(..., "")` only — **must** use the overlay-then-mainline helper (extract a shared function in `studios.py` or import the crud helper; do not duplicate forever).

## 5. Tests (no live CVaaS required)

- AssignedTags `/all` 200 with studios A,B and **not** C → GET C returns `query=""` coverage full.
- Assign C with expected `""` and new `"device:X"` → preview then one POST.
- Assign C with expected `"device:X"` → mismatch, no POST.
- HTTP 404 on `/all` still `assigned_tags_unavailable`, assign refuses.
- Generic miss includes `available_path_values: [[]]`.
- Generic `[]` → `root_path_forbidden`, no HTTP.
- Generic Inputs studio GET: overlay 200 used; mainline-only 404 then overlay 200 succeeds.
- Existing 2.0 description tests unchanged.

## 6. Files

| File | Change |
| --- | --- |
| `cvp_mcp/grpc/studio_tags.py` | empty-filter ≠ unavailable; assign first-row |
| `tests/test_studio_tags.py` | cases in §5 |
| `cvp_mcp/grpc/studio_inputs_generic.py` | available paths; overlay studio GET |
| `tests/test_studio_inputs_generic.py` | cases in §5 |
| `cvp_mcp/grpc/studios.py` | optional: `get_cvp_studio_prefer_workspace()` |
| `docs/studios-phase2-spec.md` | one paragraph: no-row query `""`; Resource path ≠ JSON key |

Do not register submit. Do not edit description CAS except if extracting a shared studio GET (behavior unchanged).

## 7. Live verify (after code)

Same MCP host, writes on, submit off:

1. `get_cvp_studio_assigned_tags(studio-campus-access-interfaces)` → `query=""`, coverage full.
2. Create `ws-mcp-*`, assign with expected `""` **preview only** (do not confirm unless a human wants a real tag change).
3. `set_cvp_studio_inputs` with `["campus"]` → `inputs_path_not_found` + `available_path_values: [[]]`.
4. Create overlay studio, then generic Inputs must not fail Studio GET with mainline 404.
5. Delete the draft workspace.

## Farm later

Three implementation buckets (disjoint files) + one wiring/docs if spec paragraph is separate:

| ID | Own |
| --- | --- |
| T | `studio_tags.py` + tests |
| I | `studio_inputs_generic.py` + tests |
| S | shared overlay GET in `studios.py` if extracted; else folded into I |
| D | spec paragraph in `studios-phase2-spec.md` |

Do not farm until this spec is approved.
