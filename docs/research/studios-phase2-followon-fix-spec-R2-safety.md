# Spec review R2 — safety (adversarial)

Reviewer: Cursor Task (read-only). Spec: `docs/studios-phase2-followon-fix-spec.md` (pre-revise).

## Verdict (revise)

The product model is right: on this tenant a 200 from `AssignedTags/all` with **no row for this studio+workspace** is “never assigned,” not “API missing.” Shipping that as `coverage="full"` + `query=""` **without** the same fail-closed rules description CAS already uses will let a partial or mis-filtered `/all` look like an empty query and authorize a first-assign POST.

POST still targets a `ws-mcp-*` draft (`resource_write` refuses empty `workspaceId`). That is not a direct mainline write. The residual is overlay-on-submit: CAS never saw the inherited mainline query.

## Blocking

**1. Incomplete `/all` must not become `query=""`.**

`get_ndjson_all_values_with_bearer` turns byte truncation into a warning and still returns `err=None` plus a prefix of rows. Invalid NDJSON lines are skipped the same way.

Today `_fetch_assigned_tags` treats **zero client-filter matches** like 404/`empty_response` (`unavailable`) and assign refuses. That is the safety property.

The follow-on spec table does not. `studios_write._load_root_inputs` already refuses `truncated_to_` / `ndjson_skip_invalid_line` because a partial stream would POST a partial tree. AssignedTags is the same class of bug: if the matching row sits past the cut (or on a skipped line), filter count is 0, GET reports `query=""`, assign with `expected_current_query=""` previews and POSTs.

Live `/all` is 22 rows, so truncation is unlikely **today**. Studio/`all` on this tenant already proved `/all` truncation is real. Spec tests cover “A,B present, not C” and HTTP 404; they do **not** cover truncated prefix, skipped lines, or empty body.

Synthesize `query=""` only when **all** of these hold:

- HTTP 200, `err is None`
- no `truncated_to_*` / `ndjson_skip_invalid_line` warnings
- stream parsed at least one AssignedTags value (other studios present), **or** a keyed GET 404 is confirmed
- client-filter matches for this studio+workspace is exactly 0

Otherwise keep `assigned_tags_unavailable` / `assigned_tags_read_failed`. Add those cases to §5.

**2. Assign CAS must inherit mainline, not only the draft overlay.**

Assign already fetches `_fetch_assigned_tags(datadict, sid, workspace)` with the **draft** id. Live AssignedTags rows are UUID drafts, not `""`. A new `ws-mcp-*` workspace will usually have **no overlay row** even when mainline has a real query.

Today: 0 draft matches → unavailable → no POST.
Spec: 0 draft matches → current `""` → expected `""` → POST `AssignedTagsConfig` keyed to the draft.

That is not a mainline POST. It **is** an overlay that shadows/replaces the inherited assignment on later submit, and CAS never compared against mainline. Inputs already does overlay-then-mainline in `_read_path_document`. Tags assign does not.

“Do not copy UUID workspace rows onto mainline” is correct for GET default. It must not be read as “ignore mainline when assigning into a new draft.”

Current query for assign: overlay row if present, else mainline `workspaceId=""`, else `""` **only if `/all` was complete**. `expected_current_query=""` must match that inherited value.

## Important

- Overlay studio GET: do not use “coverage none” as the fallback predicate. Reuse `_read_studio_anywhere` (404-only fallthrough). Overlay `read_failed` must not fall back.
- `expected_current_query=""` vs omitted: current gate `if not expected_current_query` treats `""` as missing. Distinguish omitted/null (`expected_current_query_required`) from explicit `""`. Bind the explicit empty value into `preview_token`.
- `available_path_values` is paths, not a new Inputs dump. Overlay-if-present-else-mainline (do not union in a way that invites a POST of a mainline-only path as a new overlay row unless that is intended). Fail closed on truncated Inputs/`all`. Cap list length. Never dump `inputs` bodies.
- If the only path is `[]`, keep `root_path_forbidden` and point at `set_cvp_access_interface_description`. Do not add a generic root POST.

## Minor

- Keep 404 → `assigned_tags_unavailable` vs other HTTP → `assigned_tags_read_failed`.
- A matching row with no recognized query field currently becomes `query=""`. After no-row synthesis, that looks like “unassigned”. Prefer `assigned_tags_read_failed` if a matching row has no query field.
- Live-verify: the GET used for CAS is overlay-then-mainline, same as assign.
- Parent spec still says AssignedTags/`all` is unprobed.
- First human-confirmed POST should re-GET and assert the new row is keyed to the draft and mainline is unchanged. Submit stays unregistered.
