# Spec review synthesis — 2.1 live-gap fix spec

Date: 2026-08-22. Three read-only reviews of `docs/studios-phase2-followon-fix-spec.md`. Findings: R1 API, R2 safety, R3 contract.

## Verdict

**Revise, then implement.** Product model is accepted: AssignedTags `/all` 200 with no row for this studio is “never assigned,” not “API missing.” Generic Inputs must report Resource `path.values` and never treat JSON keys as paths. Overlay Studio GET for generic Inputs is a real bug.

The pre-revise spec was **not** farm-ready. Three independent holes would ship a wrong T bucket or a write on a partial `/all`. **Those holes are now in the spec** (same file, revised 2026-08-22). Do not farm until that revision is approved.

## Decisions (blocking, now in the spec)

| Hole | R1 / R3 | R2 | Spec now |
| --- | --- | --- | --- |
| `expected_current_query=""` | Current code refuses `expected_current_query_required` before GET. T must narrow that to omitted/non-str only. | Bind `""` into `preview_token`. | Explicit `""` is a valid CAS token. `None`/non-str still required. |
| Incomplete `/all` | Empty body stays unavailable. | Truncation / skipped NDJSON must **not** become `query=""`. | Synthesize `""` only on complete 200 stream with ≥1 parsed row and 0 filter matches. Truncation → `assigned_tags_read_failed`. |
| Assign vs mainline | R3: draft-scoped filter; don’t copy UUID onto `""`. | Overlay-then-mainline or first assign shadows inherited query on submit. | Overlay row if present, else mainline `""`, else `""`. Never another UUID workspace. |
| Overlay Studio GET | “coverage none” is too broad vs `_read_studio_anywhere`. | Overlay `read_failed` must not fall back. | Import `_read_studio_anywhere` (404-only). No extract in `studios.py`. |
| Parent 2.0 text | D “one paragraph” leaves “unprobed / do not invent a query” in force. | Same. | D **replaces** named sentences. |
| `next_action` on `[]` | `_refused` hardcodes `None`; 2.0 file not in §6. | Hint yes, no generic root POST. | `root_path_forbidden` before HTTP. Hint on `inputs_path_not_found` via `details.hint` when available is only `[]`. |

## Not taken

- Keyed AssignedTags GetOne as a substitute for `/all` (unprobed). Stay on `/all` + client-filter.
- Generic root POST.
- `submit_cvp_workspace` registration.
- Editing `studios_write._refused` or description CAS.

## Farm after this spec

| ID | Own | Depends |
| --- | --- | --- |
| T | `studio_tags.py` + `tests/test_studio_tags.py` | — |
| I | `studio_inputs_generic.py` + tests; **import** `_read_studio_anywhere` from `studio_crud` | — |
| D | **Replace** named parent sentences in `docs/studios-phase2-spec.md` | — |
| S | **cancelled** — do not extract into `studios.py`; I imports crud helper | — |

Do not farm S and I in parallel on overlay GET.
