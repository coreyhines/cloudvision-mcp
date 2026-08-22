# Spec review R3 — contract / implementability

Reviewer: Cursor Task (read-only). Spec: `docs/studios-phase2-followon-fix-spec.md` (pre-revise) vs `docs/studios-phase2-spec.md` 2.1 and §6 files.

## Verdict (revise)

Implementable as a code change, but not as a farm-ready contract. §1/§2 fight parent 2.0 AssignedTags text; §4’s overlay fallback is broader than fail-closed; T will hit an unnamed `expected_current_query_required` gate; S vs I is still a fork.

## Blocking

1. **Parent 2.0 GET still forbids the §1 model.** Parent `get_cvp_studio_assigned_tags` says AssignedTags `/all` is unprobed, and **404 or empty** → `coverage="none"` + `assigned_tags_unavailable`, **do not invent a query**. The fix spec’s no-row case is `coverage="full"`, `query=""`, no that warning. Bucket D’s “one paragraph” does not name those sentences (or the inventory “URL unprobed” Open table row) as **replace**, so parent and follow-on stay contradictory after D.

2. **First assign with `expected_current_query=""` is unspecified vs current `_refused`.** Current assign does `not expected_current_query` → `expected_current_query_required` (same in `test_assign_requires_expected_current_query` for `""` and `None`). The Keep list never says to narrow that code to omitted/non-str only. A T worker can keep the gate and still fail §2/§5.

3. **§4 overlay fallback is `coverage="none"`; existing helper is 404-only.** `studio_crud._read_studio_anywhere` tries draft then `""`, but **`read_failed` (non-404) does not fall through**. Parent writes: any non-200 preflight → `preflight_failed`. Spec must say: overlay **404/`not_found`** → mainline; other overlay errors → `preflight_failed`.

## Important

- **Error codes vs code (keep these; do not revive parent aliases):**
  Tags: `assigned_tags_unavailable`, `assigned_tags_read_failed`, `assigned_tags_ambiguous`, `current_query_mismatch`, `empty_query_forbidden`, `expected_current_query_required`.
  Inputs: `inputs_path_not_found`, `root_path_forbidden`.
  Parent still has unused `tag_query_mismatch`. D must add the codes above and **not** rename `current_query_mismatch`.

- **`empty_response` vs 0 filter hits.** After a successful parse, 0 client-filter rows → full + `query=""` (live 200 with other studios). Helper `empty_response` / 404 stay unavailable. Keep `test_read_empty_stream_is_unavailable` unless you explicitly reclassify empty body.

- **Farm T / I / S / D:**
  **T** `studio_tags.py` + `tests/test_studio_tags.py` — disjoint.
  **I** `studio_inputs_generic.py` + `tests/test_studio_inputs_generic.py` — disjoint if overlay is **imported**.
  **S** `studios.py` extract is optional and **not** disjoint until locked: either I imports `_read_studio_anywhere` (no `studios.py` edit; `studio_crud.py` is not in §6), or S extracts a public helper and I only imports it. Do not farm S and I both writing overlay GET.
  **D** parent paragraph only — must be a **replace** of the 2.0 GET empty/unprobed text, plus Resource path ≠ JSON key.

- **`next_action` when `available_path_values` is only `[]`.** `studios_write._refused` always sets `next_action: None`. That file is not in §6 (and must not change 2.0 CAS). Put the pointer in `error.message` / `details.hint` on `inputs_path_not_found`, not by editing `_refused`.

- **AssignedTags CAS is draft-scoped, not overlay-then-mainline.** Assign already GETs the **draft** workspace. R2 disagrees (must inherit mainline). Spec must pick one.

- **Existing T tests that must change (not named in §5):** `test_read_no_matching_row_is_unavailable` (invert); split `""` out of `test_assign_requires_expected_current_query`.

## Minor

- GET `>1` row: spec wants `coverage="none"` + `assigned_tags_ambiguous`. Today GET returns `coverage="full"` and assign refuses. Say GET uses a **warning** with that name; assign keeps it as `error.code`.
- `available_path_values` merge: overlay rows then mainline, unique lists; do not walk JSON keys.
- Root `[]` stays `root_path_forbidden` **before** HTTP.
- Do not change `get_cvp_studio()` itself; 2.0 description CAS still GETs mainline.
- Parent 2.1 assign still says `expected_current_query` **required**; D should say `""` is a valid current query, not “missing”.

**Missing tests (add to §5):** `expected_current_query=""` is not `expected_current_query_required`; omitted/`None` still is; GET `>1` row; miss hint; overlay 404 + mainline 200; overlay 500 does not fall through. Keep 2.0 description tests unchanged.
