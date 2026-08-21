# Studios spec re-review — Bucket RR3 (consistency / implementability)

**Reviewer:** cursor-named-sonnet (subagent) · **Model:** sonnet
**Reviewed:** `docs/studios-support-spec.md` @ `feat/studios-support-spec` `bdf2715`
**Scope:** findings only, no product-code or spec edits. See `docs/research/studios-support-rereview-buckets.md` for bucket assignment.

## Verdict

The synthesis-apply pass (`bdf2715`) tightened most of the Wave-2 Critical/Important items
(Phase 2 ship decision moved to top, CC tools dropped from the inventory table, NDJSON
dedupe rules, replace-semantics warnings). But the pass **introduced one new contradiction**
while adding verified facts (RR3-1), left **two real Phase-1 coding blockers** unresolved
(RR3-2, RR3-3), and the two new fixtures are **analyst summaries, not literal wire payloads**,
which undercuts the "fixture-backed" claim in the testing section (RR3-4, RR3-5). None of
these require reopening the whole spec; each has a narrow, concrete fix below.

---

## Q1 — Internal contradictions

### RR3-1 (Critical) — `search_cvp_studio_templates` worked example contradicts the same commit's Studio-vs-StudioConfig finding

**Headings:** `### Studio vs StudioConfig.` and `#### search_cvp_studio_templates` (worked example paragraph).

The "Studio vs StudioConfig" section states, verified 2026-08-21:

> keyed `GET .../Studio?key.studioId=&key.workspaceId=` with mainline `workspaceId=""`
> returns **200**... The same query on `StudioConfig` returns **404** `studio not found`.

`tests/fixtures/workspace_build_enums.json` confirms this with a concrete example
(`studioId: "TOPOLOGY"`, `workspaceId: ""` → `StudioConfig` 404).

Yet `search_cvp_studio_templates` is specified to walk `StudioConfig/all`, and its "Worked
example" paragraph claims a *mainline* studio ("EOS Event Handler" — almost certainly
`studio-eos-event-handler-pkg`, which appears as a live `CONFIG_TYPE_STUDIO` source for
720xp-24 in `tests/fixtures/designed_config_sources_720xp24.json`) was found via that same
`StudioConfig/all` stream. `git log` shows both the 404 finding and this worked example were
added in the **same commit** (`bdf2715`), so this isn't stale carry-over text — it's a fresh
internal contradiction: either `StudioConfig/all` surfaces mainline studio bodies despite
keyed `StudioConfig` GET 404ing on the exact same key (an unexplained and undocumented
asymmetry an implementer needs to know about), or the worked example predates the 404
finding and was never re-validated against it.

**Fix:** Re-run the `StudioConfig/all` probe and note, for at least one mainline studio, what
`workspaceId` value actually appears on its `/all` row (empty string, or something else). If
it's not `""`, document that `search_cvp_studio_templates` results carry a `workspace_id`
that cannot be fed back into a keyed `Studio`/`StudioConfig` GET the way `get_cvp_studio`
expects — that's a footgun for anyone chaining tool outputs.

### RR3-2 (Minor) — Open question 5 re-asks a question the spec body already answers

**Headings:** `## Open questions` (item 5) vs `#### get_cvp_studio` (`body=False` row).

`get_cvp_studio` already commits to a design: "return `template_bytes`, `template_sha256`
(hex SHA-256 of UTF-8 Mako source)." Open question 5 then re-litigates it: "SHA-256 vs
another hash for omitted template bodies — SHA-256 is the v1 choice unless CVP already
exposes a content hash." The hedge ("unless CVP already exposes a content hash") was never
probed anywhere in "Verified environment facts," so the open question is really "did we
check whether CVP has a native template hash?" (answer: no), not a live hash-algorithm
debate. As worded it reads like the SHA-256 decision is still unsettled, which contradicts
the definitive tool-body text.

**Fix:** Reword item 5 to "Confirm CVP does not already expose a template content hash
before Phase 1 ships client-side SHA-256" — or drop it if that's considered settled.

### RR3-3 (Minor) — Phase 2 is "not shipping" but its 250+ lines of tables read as ready-to-implement

**Headings:** `### Ship decision` vs `## Phase 2 — Write tools` (whole section).

The ship decision is unambiguous ("Phase 2 write tools do not ship in the first MCP
release") and every Phase-2 row in the "Tool inventory" table is tagged `2 (later)`. That's
adequate signal at the summary-table level. But each individual Phase-2 tool subsection
(`create_cvp_workspace`, `set_cvp_studio_inputs`, etc.) reads like a normal, current API
contract — same formatting and confidence level as the Phase 1 sections — with no per-section
marker tying it back to the ship-decision gate. A reader who jumps straight to, say,
`#### submit_cvp_workspace` (via search or a link) has no local cue that this is
speculative-until-opt-in design, not a spec to start coding against this sprint.

**Fix:** A one-line banner under the `## Phase 2 — Write tools` H2 (e.g. "*Design only —
gated by `CLOUDVISION_MCP_ALLOW_WRITES`; not part of this PR's scope*") would be cheap
insurance against a well-meaning contributor picking up a Phase 2 tool "since it's already
fully speced."

---

## Q2 — Gaps that would block a Phase 1 coding PR

### RR3-4 (Important) — `get_cvp_studio_inputs`'s endpoint contract is still unresolved, unlike every other keyed-GET tool

**Heading:** `#### get_cvp_studio_inputs`.

Every other Phase 1 keyed-GET tool (`get_cvp_studio`, `get_cvp_workspace`,
`get_cvp_workspace_build`) got a live 2026-08-21 verification pass and a definitive endpoint
row. `get_cvp_studio_inputs` did not:

> **Endpoint** | `GET /api/resources/studio/v1/Inputs/all` filtered client-side by
> `studio_id` + `workspace_id`, **or keyed GET if the live API documents query params**

That "or" is exactly the "TBD / confirm with live GET" pattern RR1 was asked to flag, and it
squarely blocks a Phase 1 PR: an implementer doesn't know whether to write a filtered
NDJSON-stream reader (with its own dedupe rule per the `/all` NDJSON section) or a keyed GET
(which, per the `Studio` vs `StudioConfig` precedent in this same doc, might simply 404 and
need a different resource name). No probe result, no fixture, no access-matrix row exists
for `Inputs` keyed GET the way one exists for `Studio`/`Workspace`/`WorkspaceBuild`.

**Fix:** Run the same keyed-GET probe used for Studio/Workspace/WorkspaceBuild against
`Inputs?key.studioId=&key.workspaceId=` on a studio with known inputs, record the result
(200 with what shape, or 404), and update the endpoint access matrix + this tool's row
accordingly.

### RR3-5 (Important) — `get_cvp_studio_inputs`'s singleton return shape may conflict with `InputsConfig`'s own key model

**Headings:** `#### get_cvp_studio_inputs` vs `#### set_cvp_studio_inputs` (Body row).

`set_cvp_studio_inputs`'s POST body is `{"key":{"studio_id","workspace_id","path":{"values":
...}},"inputs":"..."}` — `path` is part of the **resource key**, not just a request
parameter. That implies more than one `InputsConfig`/`Inputs` row can exist per
`(studio_id, workspace_id)`, one per distinct `path`.

`get_cvp_studio_inputs`, however, is specified to return a **singleton `object`**:
`{studio_id, workspace_id, path_values, inputs}` — one `path_values` field, not a list of
per-path entries. Per the "Envelope and registration" rule ("List tools use `items`;
singletons use `object`"), if `Inputs` really is keyed by path, this tool's shape is wrong
and it should return `items: [{studio_id, workspace_id, path_values, inputs}, ...]` (one
item per path actually populated in that workspace), the way `get_cvp_studios` does for its
resource family.

This isn't cosmetic — it decides whether the Phase 1 implementation needs to merge multiple
NDJSON rows per key or can treat the first/only match as authoritative, and it decides
whether "current instance values" (the tool's stated purpose, so Phase 2 writes aren't
guessed) is even complete if there are unlisted deeper-path rows.

**Fix:** Resolve alongside RR3-4's endpoint probe — capture a studio with inputs set at more
than one path (if that's a real scenario) and confirm whether `Inputs/all` yields one row or
several for it. Update the return shape (`object` vs `items[]`) to match.

### RR3-6 (Minor) — `get_cvp_studio_inputs` and the `get_cvp_workspace`/`get_cvp_workspace_build` pair omit `data_source`, unlike every other Phase 1 tool

**Heading:** `#### get_cvp_studio_inputs`, `#### get_cvp_workspace` / `get_cvp_workspace_build`.

`get_cvp_designed_config` and `get_cvp_studios` both spell out
`data_source="service_api:..."` / `data_source="resource_api:studio.v1"` in their envelope
description. The three tools above don't state a `data_source` string at all, which is a
small but real gap against the "Envelope and registration" section's requirement that every
tool return `tool_envelope(...)` with `data_source` populated — an implementer has to invent
the string ad hoc, and reviewers/tests can't check it against a documented value.

**Fix:** Add explicit `data_source` values (e.g. `resource_api:studio.v1` for inputs,
`resource_api:workspace.v1` for the workspace pair) to keep all Phase 1 tools at the same
documentation bar.

---

## Q3 — Did synthesis apply miss or partially apply Critical/Important items?

Checked each Wave-2 synthesis theme against the current spec text and prior commit
(`8ebc766`) for what changed:

| Synthesis theme | Applied? | Evidence |
| --- | --- | --- |
| 1. Line-level provenance vs "Why" | **Yes** | "Phase 1 does not answer" paragraph now explicit about configlets/template-search limits. |
| 2. GetConfig `type` param + `/all` NDJSON contract | **Yes** | `type` parameterization spelled out; NDJSON rules section added; live fixture added. |
| 3. Phase 2 field-semantics hazards (CC start, replace semantics, `builtin-`) | **Yes** | Replace-semantics warnings, `builtin-` denylist on *all* writes, CC tools dropped entirely (not just flagged) from the tool inventory table. |
| 4. Write gates vs live `tool_enabled` | **Yes** | Explicit statement that `tool_enabled` has no `writes=` today; `CLOUDVISION_MCP_ALLOW_WRITES`/`ALLOW_SUBMIT` spec'd as a *separate* gate, matching what `cvp_mcp/tool_access.py` actually implements (confirmed by reading the file: only `CVP_MCP_DISABLED_TOOLS`, no writes flag). |
| 5. "Should Phase 2 ship?" process contradiction | **Yes** | Moved to `### Ship decision` at the top of `## Phase 2`; removed the corresponding old open question 3. |
| 6. Missing Phase 1 reads Phase 2 assumes (`get_cvp_studio_inputs`, keyed probes, NDJSON helper) | **Partially** | `get_cvp_studio_inputs` exists as a section, but per RR3-4/RR3-5 its endpoint and shape are still unresolved — the section was added, not actually completed. Keyed Workspace/WorkspaceBuild probes *were* completed (fixture-backed). NDJSON helper requirement is stated but not new in this pass. |

**Net:** one clean miss carried forward (`get_cvp_studio_inputs` — added a section, but did
not close the gap synthesis theme 6 asked for), plus the new RR3-1 contradiction introduced
by the fixes for theme 1/2 (adding the live worked example without reconciling it against
the new Studio/StudioConfig 404 finding added in the same pass).

---

## Q4 — Is the test plan specified enough to TDD Phase 1?

### RR3-7 (Critical) — The two new fixtures are analyst-summary JSON, not literal API response bodies, so they cannot be fed directly into a parser the way this repo's existing fixtures are

**Headings:** `## Testing (phase 1)`, `#### get_cvp_designed_config` (fixture description),
and the fixture files themselves.

This repo's existing convention (`tests/test_lldp_parse.py`) loads a fixture straight off
disk and passes it into the function under test:

```12:14:tests/test_lldp_parse.py
    raw = json.loads(
        Path(__file__)
        .resolve()
```

`tests/fixtures/designed_config_sources_720xp24.json` cannot be used that way. The spec's
own documented wire shape for `get_cvp_designed_config` is:

```216:228:docs/studios-support-spec.md
[
  {
    "sources": {
      "source": [
        {"source_type": "CONFIG_TYPE_STUDIO", "key": "studio-authentication"},
```

— a **JSON array of message objects**. But the fixture file's top level is
`{"captured_at": ..., "cvp": ..., "device_id": ..., "notes": [...], "source_types_observed":
{...}, "sources": {"source": [...]}, "config_message_shape": {...}}` — a report *about* the
capture, with `sources` as a sibling key next to metadata, not wrapped in the two-element
message array the parser will actually receive, and no real second `{"config": "..."}`
message at all (`config_message_shape` is a placeholder comment, not data). A test author
following the repo's own `json.loads(fixture).read_text()` convention would get an object
shaped nothing like production input; they'd have to hand-reconstruct the array first,
which the spec doesn't say anywhere.

Same issue for `tests/fixtures/workspace_build_enums.json`: it's a notes/enum-tally bag
(`WorkspaceBuild.state_observed` counts, a `keyed_get` example key echo) rather than a
literal `Workspace?key.workspaceId=...` or `WorkspaceBuild?...` response body with real
`responses.values`, `state`, `errors` fields. There is currently **no** fixture usable as a
drop-in mock HTTP body for `get_cvp_workspace`, `get_cvp_workspace_build`, or the build-poll
state machine described in `### Workspace build poll` — despite that section being one of
the more intricate pieces of Phase 1 logic (terminal-state detection, `request_id`≈`buildId`
mapping) that most needs a fixture-driven test to get right.

**Fix:** Either (a) add literal, wire-shaped fixture files (e.g.
`designed_config_response_720xp24.json` holding exactly the two-element array;
`workspace_build_response_sample.json` holding one realistic `Workspace` and one
`WorkspaceBuild` body) alongside the current summary files, or (b) explicitly document, next
to each existing fixture, the transform a test must apply to turn it into a mock response
(and, ideally, do that transform once in a shared test helper so every future test doesn't
reinvent it).

### RR3-8 (Important) — Verify `source_type` casing before hardcoding the parser

**Heading:** `#### get_cvp_designed_config` ("Important wire facts").

The spec asserts the live wire field is snake_case: `{"source_type": "CONFIG_TYPE_STUDIO",
"key": "..."}`. Everywhere else in this document, live CVP wire JSON is camelCase
(`workspaceId`, `buildId`, `ccIds`, `displayName`, `inputSchema`) per the explicit rule
"POST bodies accept snake_case or camelCase keys; responses are **camelCase** (Arista)."
Public Arista API docs for the closest analogous model (`configstatus.v1.ConfigSource`)
name the enum `ConfigSourceType` with values like `CONFIG_SOURCE_TYPE_STUDIO` — not
`CONFIG_TYPE_STUDIO` as captured here. It's plausible `compliancecheck.Compliance/GetConfig`
is a different, older RPC with its own distinct (and genuinely snake_case) message shape —
the spec is already clear this is a different surface than `configstatus`'s Resource API —
but given RR3-7's finding that this fixture is a hand-typed summary rather than a byte-for-
byte capture, the field name and casing should be treated as **unverified** rather than
load-bearing until someone diffs it against a saved raw response body. A parser that keys
off `source_type` when the live field is actually `sourceType` (or `source.value.source_type`
under nested wrapping) would fail silently — `studio_keys` would come back empty and every
Phase 1 designed-config call would look "successful" (200, well-formed JSON) while silently
losing the one payload the tool exists to expose.

**Fix:** Before writing the parser, save one full raw response body (not just a hand-summary)
to `tests/fixtures/` and diff its exact key names/casing against what's currently written in
"Important wire facts." Update either the fixture-capture note or the spec text so they match
byte-for-byte.

### RR3-9 (Minor) — Described NDJSON edge-case tests have no backing fixture file yet

**Heading:** `## Testing (phase 1)`.

The section names three specific test cases ("trailing blank line," "missing `displayName`,"
"last-write-wins duplicate key") and a `search_cvp_studio_templates` case ("match inside
nested `inputSchema` description and another inside a template body") but, unlike the
designed-config and workspace-enum fixtures, no fixture file exists yet for any of these
(confirmed: only `designed_config_sources_720xp24.json` and `workspace_build_enums.json`
exist under `tests/fixtures/`). This is lower severity than RR3-7 because these are small,
easy to hand-write inline in a test (matching the existing `test_config_async_flow.py` style
of inline JSON strings), but the spec doesn't say which pattern to use, so note it to avoid
implementers debating fixture-file-vs-inline-string style during the PR itself.

**Fix:** Either add small fixture files for these NDJSON cases (consistent with the
lldp-sample style already in the repo) or explicitly say "inline literals are fine here,"
matching `test_config_async_flow.py`'s existing pattern, so Phase 1's PR doesn't need a style
discussion before merging tests.

---

## Do-NOT compliance

No edits made to `docs/studios-support-spec.md`, any fixture, or product code. Phase 2
field-level execute mechanics were not reviewed beyond the "out of v1" consistency checks
in RR3-3 (per bucket scope; Phase 2 depth is RR2's bucket).

## Summary table

| ID | Severity | Area | One-line fix |
| --- | --- | --- | --- |
| RR3-1 | Critical | Contradiction | Re-probe `StudioConfig/all` mainline `workspaceId` shape; reconcile with the 404 finding. |
| RR3-2 | Minor | Contradiction | Reword open question 5 to match the already-decided SHA-256 body text. |
| RR3-3 | Minor | Contradiction | Add a "design only, gated" banner to the Phase 2 section header. |
| RR3-4 | Important | Phase 1 blocker | Probe `Inputs` keyed GET; resolve the "or keyed GET if..." hedge. |
| RR3-5 | Important | Phase 1 blocker | Confirm whether `Inputs` is path-keyed; fix `object` vs `items[]` return shape. |
| RR3-6 | Minor | Phase 1 blocker | Add explicit `data_source` values to 3 under-specified tool rows. |
| RR3-7 | Critical | Test plan | Add literal wire-shaped fixtures; current two are hand-written summaries. |
| RR3-8 | Important | Test plan | Verify `source_type` field casing against a raw saved response before coding the parser. |
| RR3-9 | Minor | Test plan | Add or explicitly waive fixture files for the three described NDJSON edge cases. |
