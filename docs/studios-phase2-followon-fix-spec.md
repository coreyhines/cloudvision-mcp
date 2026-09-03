# Spec: Phase 2.1 live-gap fixes (tags + generic Inputs)

Status: **ready to implement** (revised 2026-08-22 after R1/R2/R3). Does not add submit. Does not change 2.0 description CAS.

Parent: `docs/studios-phase2-spec.md`. Evidence: `docs/research/studios-phase2-followon-live-gap-analysis.md`. Review: `docs/research/studios-phase2-followon-fix-spec-review.md`.

## Goal

Make 2.1 tag assign and generic Inputs behave correctly on this CVaaS tenant:

- AssignedTags `/all` is live; many studios have **no row**.
- Access Interfaces stores **one** Inputs resource at `path.values []`. Nested JSON keys are not Resource paths.

## Non-goals

- Register `submit_cvp_workspace`.
- Change 2.0 `studios_write.set_description` (including `studios_write._refused`).
- Tag API `tag/v1` (403 on this token).
- Invent AssignedTags rows for studios that have none.
- Switch AssignedTags reads to keyed GetOne (`AssignedTags?key.studioId=&key.workspaceId=`). Unprobed; a 404 there must not become `assigned_tags_unavailable`.
- `GET AssignedTagsConfig/all`. Config is write-only here. Current query comes from **state** `AssignedTags/all`.
- `/all` query params (`partial_eq_filter`, `filter`, `time.start`). Client-filter the full stream.

## 1. AssignedTags read

`GET /api/resources/studio/v1/AssignedTags/all` is the URL (live 200). Keep NDJSON `result.value` parse. No query-string filters.

After a **complete** parse, client-filter `key.studioId` + `key.workspaceId`.

A stream is **complete** only when all of these hold:

- HTTP 200 and helper `err is None`
- no warning containing `truncated_to_` or `ndjson_skip_invalid_line`
- at least one AssignedTags `result.value` parsed (other studios present)

Otherwise do **not** synthesize `query=""`.

| GET `/all` | Filter matches (this studio + this workspace) | Read result |
| --- | --- | --- |
| HTTP 404 (`http_error:404`) or helper `empty_response` | — | `coverage="none"`, warning `assigned_tags_unavailable` |
| HTTP other 4xx/5xx or transport error | — | `coverage="none"`; assign uses `assigned_tags_read_failed` |
| 200 but **incomplete** (truncation / skipped lines / `err`) | any | `coverage="none"`; assign uses `assigned_tags_read_failed` — **not** `query=""` |
| complete 200, 0 rows for this studio+workspace | **0** | `coverage="full"`, `items: [{studio_id, workspace_id, query: ""}]`, **no** `assigned_tags_unavailable` |
| complete 200, 1 row with a recognized query field (`query` / `tagQuery` / `tag_query`) | 1 | `coverage="full"`, that `query` |
| complete 200, 1 row with **no** recognized query field | 1 | `coverage="none"` (GET warning; assign `assigned_tags_read_failed`). A present row with a missing field is not “unassigned”. |
| complete 200, >1 row | >1 | `coverage="none"`, warning `assigned_tags_ambiguous` (assign must refuse) |

Split this in `_fetch_assigned_tags`, not only in the GET wrapper. Assign calls that helper.

**Resolver used by GET (when a draft id is passed) and by assign** — same function:

1. Overlay: filter `(studioId, draft workspaceId)` when the caller passed a draft id.
2. Else mainline: filter `(studioId, "")`.
3. Else `query=""` only if the stream was complete.
4. Never copy a **different UUID** workspace’s query onto mainline `""` or onto this draft.

GET with `workspace_id=None` uses mainline `""` only (step 2 then 3). Assign always runs overlay-then-mainline so a new `ws-mcp-*` draft inherits the mainline query. `expected_current_query=""` is valid only when that resolved value is `""`.

## 2. AssignedTags assign

Keep: writes gate, `ws-mcp-*` pending, empty **new** `query` → `empty_query_forbidden`, preview token.

Change:

- “no row after complete `/all`” is not unavailable.
- `expected_current_query=""` is a **valid CAS token** meaning “resolver returned unassigned.”
- `expected_current_query` omitted / `None` / non-str → still `expected_current_query_required` (before HTTP). Do **not** use `if not expected_current_query` — that treats `""` as missing.
- Bind the explicit `""` into `preview_token` the same as a non-empty expected.

| Resolver current | `expected_current_query` | Action |
| --- | --- | --- |
| `query=""` (no overlay, no mainline) | `""` | Preview/POST first assignment |
| `query=""` | non-empty | `current_query_mismatch` |
| `query="foo"` | `"foo"` | Preview/POST replace |
| `query="foo"` | other, including `""` | `current_query_mismatch` |
| unavailable | any | refuse `assigned_tags_unavailable` |
| read_failed (incl. incomplete `/all`) | any | refuse `assigned_tags_read_failed` |
| ambiguous | any | refuse `assigned_tags_ambiguous` |

Do not rename `current_query_mismatch` to parent’s unused `tag_query_mismatch`.

POST body (same path already allowlisted):

```json
{
  "key": { "studioId": "<id>", "workspaceId": "<ws-mcp-…>" },
  "query": "<new query>"
}
```

Workspace id on POST is the **draft**, never `""`. Mainline is not written. First assign is an overlay row; on later submit it would shadow the inherited query, which is why CAS must compare against the resolver (overlay else mainline), not draft-only.

## 3. Generic Inputs paths

`path_values` is **Resource** `Inputs.key.path.values`, not a JSON pointer into `inputs`.

`studios.inputs` already returns `path_values` per row. Use that list.

On lookup miss, refuse `inputs_path_not_found` with:

```json
{
  "studio_id": "…",
  "path_values": ["campus"],
  "available_path_values": [[]],
  "hint": "Use studios_write.set_description for this studio’s only Resource row (path_values []). Generic Inputs cannot POST the root."
}
```

`available_path_values` is the list of unique Resource `path.values` lists for **this studio**: overlay rows if that GET has any rows for the studio, else mainline. Do not expand JSON keys. Do not include `inputs` bodies. Cap reported lists at 10; if more exist, warn and do not dump the tree.

If Inputs/`all` is truncated or has `ndjson_skip_invalid_line`, fail closed `preflight_failed` — do not advertise a partial list as complete. `_read_path_document` already fail-closes on any warning; keep that.

Empty `path_values` / `[]` still `root_path_forbidden` **before any HTTP**. Do not GET just to fill `available_path_values` on that path. Put the description-CAS pointer in `error.message` for `root_path_forbidden`. Put `details.hint` on `inputs_path_not_found` when `available_path_values` is only `[]`.

`studios_write._refused` hardcodes `next_action: None`. **Do not edit that helper.** Use `details.hint` / `error.message`.

Do **not** add a generic root POST. That would bypass 2.0 CAS.

## 4. Overlay studio GET

Generic Inputs must not read only `studios.get` with the mainline workspace.

**Import** `_read_studio_anywhere` from `cvp_mcp.grpc.studio_crud`. Do not extract a twin into `studios.py`. Do not change the `studios.get` implementation (2.0 description CAS stays mainline).

Fallthrough is **404/`not_found` only**:

1. GET `Studio?key.studioId=&key.workspaceId=<draft>`
2. overlay `read_failed` (timeout, truncation, non-404) → `preflight_failed`, **no** mainline fallback
3. overlay `not_found` → GET `key.workspaceId=`
4. mainline `read_failed` → `preflight_failed`
5. both missing → `preflight_failed` as today (no studio flags)

That is the existing `_read_studio_anywhere` contract. Do not reinterpret “coverage none” as fallthrough.

## 5. Tests (no live CVaaS required)

AssignedTags (`tests/test_studio_tags.py`):

- `/all` 200 with studios A,B and **not** C → GET C returns `query=""` coverage full. Invert `test_read_no_matching_row_is_unavailable`.
- Truncated `/all` (prefix of rows, `truncated_to_*` warning) with C absent from the prefix → **not** `query=""`; assign refuses `assigned_tags_read_failed`; no POST.
- Skipped invalid NDJSON line (`ndjson_skip_invalid_line`) → same fail-closed.
- Empty body / `empty_response` still `assigned_tags_unavailable`. Keep `test_read_empty_stream_is_unavailable`.
- HTTP 404 on `/all` still `assigned_tags_unavailable`; assign refuses. Keep `test_read_404_is_unavailable_and_invents_nothing`.
- Matching row with no query field → not synthesized `""`.
- GET `>1` row → coverage none + `assigned_tags_ambiguous`; assign refuses (`test_assign_refuses_ambiguous_rows` already covers assign).
- Assign C with expected `""` and new `"device:X"` → preview then one POST. Split `""` **out** of `test_assign_requires_expected_current_query`.
- `expected_current_query=None` / omitted still `expected_current_query_required`, no HTTP.
- Assign C with expected `"device:X"` when current is `""` → `current_query_mismatch`, no POST.
- Draft has no overlay row, mainline has `"device:Y"`, expected `""` → `current_query_mismatch`, no POST.
- Draft has no overlay row, mainline has `"device:Y"`, expected `"device:Y"` → preview/POST overlay keyed to the draft.

Generic Inputs (`tests/test_studio_inputs_generic.py`):

- Miss includes `available_path_values: [[]]` and `details.hint` naming `studios_write.set_description`.
- Generic `[]` → `root_path_forbidden`, no HTTP.
- Studio GET: overlay 200 used; overlay 404 then mainline 200 succeeds; overlay non-404 `read_failed` does **not** fall through.

Existing 2.0 description tests unchanged.

## 6. Files

| File | Change |
| --- | --- |
| `cvp_mcp/grpc/studio_tags.py` | complete-stream split; overlay-then-mainline resolver; `""` CAS token |
| `tests/test_studio_tags.py` | cases in §5 |
| `cvp_mcp/grpc/studio_inputs_generic.py` | `available_path_values`; import `_read_studio_anywhere` |
| `tests/test_studio_inputs_generic.py` | cases in §5 |
| `docs/studios-phase2-spec.md` | **replace** named sentences in §7 (not an additive paragraph) |

Do not register submit. Do not edit `studios_write.py` / description CAS. Do not edit the `studios.get` implementation in `studios.py`. `studio_crud.py` is import-only (no behavior change).

## 7. Parent spec replacements (bucket D)

Replace, do not append:

1. `docs/studios-phase2-spec.md` `studios.tags` paragraph that says `GET AssignedTags/all` is **unprobed** and 404-or-empty → `assigned_tags_unavailable` / do not invent a query. New text: `/all` is live (200). 404 or empty **body** stays unavailable. Complete 200 with 0 rows for this studio+workspace → `query=""`, coverage full. Incomplete `/all` → `assigned_tags_read_failed`, not `""`.
2. Same file `studios_write.assign_tags`: `expected_current_query` remains required as a parameter; **`""` is a valid value** (unassigned). Omitted/non-str still `expected_current_query_required`. Preflight uses overlay-then-mainline resolver.
3. Inventory row `studios.tags` | 2.0 optional read (URL unprobed) → URL probed; no-row is `query=""`.
4. Open table row ``GET AssignedTags/all` URL` → closed: `/all` 200 on this tenant.
5. One sentence under `studios_write.set_inputs`: Resource `path.values` ≠ JSON keys; Access Interfaces only row is `[]` and stays 2.0 description CAS.

Do not revive `tag_query_mismatch`. Keep `current_query_mismatch`.

## 8. Live verify (after code)

Same MCP host, writes on, submit off:

1. `studios.tags` for `studio-campus-access-interfaces` → `query=""`, coverage full (mainline).
2. Create `ws-mcp-*`. Assign CAS GET is overlay-then-mainline (same resolver). Preview only with expected `""` unless a human wants a real tag change. Do not confirm by default.
3. If a human confirms: re-GET — new row keyed to the **draft**; mainline still has no row. Then delete the draft.
4. `studios_write.set_inputs` with `["campus"]` → `inputs_path_not_found` + `available_path_values: [[]]` + hint.
5. Create overlay studio, then generic Inputs must not fail Studio GET with mainline 404.
6. Delete the draft workspace.

## Farm later

Three **disjoint** buckets. Do not farm an S extract.

| ID | Own |
| --- | --- |
| T | `studio_tags.py` + `tests/test_studio_tags.py` |
| I | `studio_inputs_generic.py` + tests; import `_read_studio_anywhere` from `studio_crud` (read-only import) |
| D | replacements in `docs/studios-phase2-spec.md` listed in §7 |

Do not farm until this spec is approved.
