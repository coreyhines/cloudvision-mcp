# Bucket RR1 — Phase 1 contracts vs live fixtures

**Branch tip:** `feat/studios-support-spec` @ `bdf2715`  
**Owner / model:** cursor-auto · auto  
**Scope:** Phase 1 read contracts only (vs live fixtures + existing helpers). No product-code edits.  
**Inputs:** `docs/studios-support-spec.md`, `tests/fixtures/designed_config_sources_720xp24.json`, `tests/fixtures/workspace_build_enums.json`, `cvp_mcp/grpc/config_async_flow.py`, `cvp_mcp/grpc/uri_fetch.py`, optional `docs/research/studios-support-review-synthesis.md`.

## Verdict

Phase 1 **matches** the live fixtures on GetConfig array shape, string `source.key`, `CONFIG_TYPE_STUDIO` / `CONFIG_TYPE_STUDIO_STATIC`, mainline `workspaceId=""`, Studio keyed 200 vs StudioConfig keyed 404, and the build poll terminal enum set. Coding Phase 1 reads does **not** need more CVP probes for those contracts. Remaining work is **helper gaps** (`get_config` type hardcoding; full-stream NDJSON) plus a few **stale “confirm / Still required” phrases** that fixtures already answer.

---

## Q1 — Does Phase 1 match the live fixtures?

**Yes.** Spec and fixtures agree on every listed wire fact.

| Claim | Spec heading | Fixture evidence | Match |
| --- | --- | --- | --- |
| GetConfig HTTP 200 body is a **JSON array** of messages | Phase 1 → Compliance GetConfig; `get_cvp_designed_config` **Parse** | `designed_config_sources_720xp24.json` `notes[0]`, `sources` + `config_message_shape` | Yes |
| `source.key` is a **string** studio id | Same; example JSON block | Each `sources.source[].key` is a string (e.g. `"studio-authentication"`, `"avd-JPE19151499"`) | Yes |
| `CONFIG_TYPE_STUDIO` and `CONFIG_TYPE_STUDIO_STATIC` | Wire facts + Normalize | `source_types_observed`: 13 STUDIO / 5 STUDIO_STATIC; both types in `sources.source` | Yes |
| Mainline `workspaceId=""` | Verified facts Studio vs StudioConfig; `get_cvp_studio` **Mainline**; Endpoint matrix capture table | `workspace_build_enums.json` `mainline_workspace_id`, `keyed_get.mainline_workspace_id`, `mainline_example_key.workspaceId` | Yes |
| Keyed **Studio** 200 / **StudioConfig** 404 on mainline | Verified facts; `get_cvp_studio` Endpoint | `keyed_get.winner: "Studio"`; StudioConfig → 404 note (TOPOLOGY, studio-authentication) | Yes |

**Minor fixture nuance (not a contract miss):** `designed_config_sources_720xp24.json` lists the same `CONFIG_TYPE_STUDIO_STATIC` key `f239139b-96fd-4a7e-b692-fc43ddf3abc8` twice. Spec’s “dedupe, preserve order” for `studio_keys` correctly covers this; implementers should unit-test duplicate keys.

---

## Q2 — Leftover “confirm with live GET / TBD” that fixtures already answer?

### Important — stale “Still required” framing

**Where:** Endpoint access matrix, paragraph starting **“Still required before coding Phase 1 poll helpers”** (immediately above the capture table).

**Issue:** Heading still reads as a blocking probe list, then says **captured 2026-08-21** and points at the two fixtures. That contradiction will slow implementers who skim headings.

**Suggested text:**

```markdown
**Captured 2026-08-21 (no further live probes required for Phase 1):**
see `tests/fixtures/workspace_build_enums.json` and
`tests/fixtures/designed_config_sources_720xp24.json`:
```

### Important — tool inventory still says “Studio or StudioConfig (keyed; confirm)”

**Where:** Testing → Tool inventory row for `get_cvp_studio`.

**Issue:** Contradicts Verified facts / `get_cvp_studio` (Studio wins; StudioConfig 404). Fixtures answer this.

**Suggested text:** `GET Studio keyed (mainline workspaceId=""; do not use StudioConfig keyed for mainline)`.

### Minor — build poll “Confirm by reading WorkspaceBuild.key.buildId”

**Where:** Workspace build poll — “so **`request_id` is typically also `buildId`**. Confirm by reading…”

**Issue:** This is fine as a **runtime** check after a real build, not as “TBD before coding.” Fixture `notes` and `poll_contract` already encode `request_id ≈ buildId` for this tenant. Soften wording so it is not read as a pre-implementation probe.

**Suggested text:** Prefer `build_id=request_id` for the first keyed GET; if `WorkspaceBuild` 404s, re-resolve from `Workspace.responses` / message text — no extra pre-coding probe.

### Minor — `get_cvp_studio_inputs` still hedges on keyed GET

**Where:** `get_cvp_studio_inputs` Endpoint row (“or keyed GET if the live API documents query params”).

**Issue:** Does not block Phase 1 (Inputs/all is 200 in the matrix). Soft TBD remains; fixtures do not include an Inputs keyed sample. Acceptable to keep client-side filter of `/all` as the v1 path and drop the hedge, or leave as optional later — not a Critical.

### Out of scope / correctly still open

- Phase 2 “still to verify” rows that remain open (rebase, rate limits) — not Phase 1.
- Open questions 1–2 (service account display name; ConfigletConfig keyed) — not answered by these fixtures; not Phase 1 blockers.
- Open question 3 (mainline + enums) is already marked **Done** — consistent with fixtures.

---

## Q3 — Is the build poll contract implementable without more probes?

**Yes for Phase 1 read-tool polling.**

| Needed for poll | Present? | Source |
| --- | --- | --- |
| Terminal states | Yes | `poll_contract.terminal_build_states`: SUCCESS / FAIL / CANCELED |
| Success state | Yes | `success_build_state`: `BUILD_STATE_SUCCESS` |
| Non-terminal handling | Yes | Spec + fixture: treat `BUILD_STATE_IN_PROGRESS` (protobuf-only in dump) and unknowns as keep-polling |
| `request_id` ↔ `buildId` | Yes (tenant-typical) | Fixture notes + `poll_contract.after_REQUEST_START_BUILD` |
| Workspace / response companion enums | Yes | `Workspace.state_observed`, `Workspace.Response.status_observed` |
| Keyed WorkspaceBuild key shape | Yes | `{workspaceId, buildId}` in notes |

**Minor:** Fixtures describe the map `responses.values[<request_id>]` in prose but do not embed a sample Workspace keyed JSON blob. That does not block coding: poll algorithm and enums are specified; unit tests can use a minimal invented envelope matching that prose.

Phase 2 `build_cvp_workspace` still must not poll inside the write tool (spec already says so); Phase 1 poll helpers are sufficient for agent-loop / human review.

---

## Q4 — What must change in existing helpers before coding tools?

### Critical — `get_config` hardcodes `RUNNING_CONFIG`

**Where:** `cvp_mcp/grpc/config_async_flow.py` `get_config` payload (`"type": "RUNNING_CONFIG"`). Spec: Why; Compliance GetConfig (shared contract).

**Required before `get_cvp_designed_config`:**

1. Add a `type` parameter (`RUNNING_CONFIG` | `DESIGNED_CONFIG`); keep existing callers on running.
2. Parse designed responses as a **JSON array** of messages; collect `sources` and `config` from separate messages (do not assume a single object).
3. Do not stop at `_extract_config_from_response` alone for designed-config tools — that helper finds CLI text but does not surface `sources` / `source_type` / string `key`.

`_decode_json_maybe_multi` already returns a `list` when `json.loads` succeeds on an array body; the gap is **type** + **sources extraction**, not inventing a second POST client.

### Critical — first-object / first-line JSON decode must not be used for `/all`

**Where:** Spec Resource API `/all` NDJSON rules (items 7–8); `cvp_mcp/grpc/uri_fetch.py` `get_json_with_bearer` (lines ~102–114: on decode failure, first valid line wins). Spec name `fetch_uri_json_object` does not exist in code — real helper is `get_json_with_bearer`.

**Required:** New full-stream NDJSON helper per spec (full body, size cap, XSSI strip, per-line `json.loads`, yield `result.value`, last-wins dedupe). Reuse `fetch_uri_with_bearer` only for raw text / keyed single-object GETs.

Using `get_json_with_bearer` on Studio/all or StudioConfig/all would silently return one row and break `get_cvp_studios`, `search_cvp_studio_templates`, `get_cvp_workspaces`.

### Important — size caps for `/all`

`fetch_uri_with_bearer` defaults `max_bytes=2_000_000`; `get_json_with_bearer` uses `5_000_000`. Spec asks ~32 MiB for NDJSON streams. Raise (or parameterize) for list tools or StudioConfig/all search will truncate mid-stream.

### Important — name fix in spec (docs only)

Replace `fetch_uri_json_object` with `get_json_with_bearer` so implementers find the real first-object decoder to avoid.

### Minor — running-config path can keep current extractors

Existing running-config flow may continue using `_decode_json_maybe_multi` + `_extract_config_from_response` once `type` is parameterized; designed-config needs the richer array parse described in the fixture notes.

---

## Severity summary

| Severity | Finding |
| --- | --- |
| **Critical** | Parameterize `get_config` `type`; add designed array/`sources` parse before coding `get_cvp_designed_config`. |
| **Critical** | Add full-stream NDJSON helper; ban `get_json_with_bearer` first-line fallback for `/all` list/search tools. |
| **Important** | Retitle “Still required…” matrix paragraph; fix tool inventory “Studio or StudioConfig (keyed; confirm)”. |
| **Important** | Raise/parameterize URI fetch byte caps for `/all` (~32 MiB). |
| **Important** | Spec should name `get_json_with_bearer`, not `fetch_uri_json_object`. |
| **Minor** | Soften runtime “Confirm buildId” wording; optional Inputs keyed hedge; duplicate STATIC key in fixture; missing sample Workspace JSON for polls. |

## Do-not-block Phase 1

- No further live GET probes for mainline `""`, Studio vs StudioConfig, GetConfig array/`key` string, or terminal `BUILD_STATE_*`.
- Phase 2 write safety (except that build poll stays in Phase 1 read tools) — out of RR1 scope.
)
