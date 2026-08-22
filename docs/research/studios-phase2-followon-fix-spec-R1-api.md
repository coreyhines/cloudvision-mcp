# Spec review R1 — API / tenant facts

Reviewer: Cursor Task (read-only). Spec: `docs/studios-phase2-followon-fix-spec.md` (pre-revise). Code + `docs/research/studios-phase2-followon-live-gap-analysis.md`.

## Verdict (revise)

Tenant facts match the live analysis and the current Resource-API helpers: `GET /api/resources/studio/v1/AssignedTags/all` is the live URL; NDJSON is `result.value`; Access Interfaces has no AssignedTags row and a single Inputs row at `path.values []`; nested JSON keys are not Resource paths; generic Inputs studio preflight is mainline-only. The spec is not implementable as written: first assign with `expected_current_query=""` still hits an existing refusal, overlay fallthrough is specified more loosely than `studio_crud`, and several Resource API read variants are unnamed.

## Blocking

- **First assign with `expected_current_query=""` is specified but still refused.** Spec §2 and §5 require `query=""` (no row) + expected `""` → preview/POST. `assign_cvp_studio_tags` still treats empty expected as missing, **before** any AssignedTags GET (`studio_tags.py` `expected_current_query_required`). Tests lock that in. Changing only `_fetch_assigned_tags` empty-filter cannot pass §5.

- **Empty-filter vs HTTP miss must be split in `_fetch_assigned_tags`, not only in the GET wrapper.** Assign calls `_fetch_assigned_tags(..., workspace)` (draft id) and refuses `assigned_tags_unavailable` on that status. Today `if not items: return [], "unavailable"` and `_UNAVAILABLE_ERRORS` includes `empty_response`. Live `/all` is **200**, 22 `result.value` rows, Access Interfaces **not** in the set. Spec table is right; name the status split on this helper.

## Important

- **Keyed AssignedTags GET is unprobed and unspecified.** Code and spec only use `/all` then client-filter. AssignedTags key is `{studioId, workspaceId}` only, so keyed GetOne is a real API. Spec should **forbid** switching to GetOne unless probed: a 404 there must not become `assigned_tags_unavailable`.

- **`AssignedTagsConfig` is write-only in this repo; Config reads are unnamed.** POST path is allowlisted. There is no `GET AssignedTagsConfig/all`. Spec should say: current query comes from **state** `AssignedTags/all`, not Config.

- **`/all` query params are unused and unprobed.** Live call and code are bare `…/AssignedTags/all`. Say explicitly: no filter/time query params; client-filter of the full stream is the tenant-proven path.

- **Assign CAS is workspace-scoped `/all` filter, not overlay-then-mainline.** GET default is mainline `""`. Assign filters the **draft** id. Spec “Do not copy UUID workspace rows onto mainline `""`” matches GET default. State it: CAS is “no row in **this** workspace,” not merged mainline — **or** (safety R2) inherit mainline. Pick one.

- **Overlay Studio GET is specified as “coverage none → mainline”; `studio_crud` only falls through on 404.** `_read_studio_anywhere` short-circuits `read_failed` (non-404). Literal spec fallthrough would use mainline flags after a failed overlay GET.

- **`[]` next_action vs “no HTTP”.** Spec §3 wants `next_action` to name `set_cvp_access_interface_description` when `available_path_values` is only `[]`. §5 says generic `[]` → `root_path_forbidden`, **no HTTP**. `studios_write._refused` hardcodes `next_action: None`. Pick one: canned hint with no GET, or fetch paths (then §5 is wrong).

## Minor

- Classify 200/parsed/0 matches as `query=""`; leave empty body/404 as unavailable.
- GET `>1` row → coverage none is a behavior change vs today (GET returns all matches).
- Parent spec still says AssignedTags `/all` is unprobed.
- §5 overlay test wording describes current failure order, not helper order.
- Do not expand JSON keys for `available_path_values`.
