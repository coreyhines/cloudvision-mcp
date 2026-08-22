# Bucket RR2 — Phase 2 residual write-safety after the synthesis apply

Reviewer: claude-opus (Claude Code CLI farm). Bucket RR2 only.
Scope: `docs/studios-support-spec.md` Phase 2 (ship decision, global write gates, tool
reference) as of `bdf2715`, re-checked against the original Wave 1 findings in
`docs/research/studios-support-review-R3.md` and the Wave 2
`docs/research/studios-support-review-synthesis.md`.
Date: 2026-08-20. Findings only. No spec edits, no product Python edits, no tools implemented.

## Sources used

| Source | What it settled |
| --- | --- |
| `docs/studios-support-spec.md` L332–L546 (Phase 2) | Current ship decision, gates, tool reference |
| `docs/research/studios-support-review-R3.md` | The 5 Critical / 11 Important the apply was supposed to close |
| `docs/research/studios-support-review-synthesis.md` §"Recommended spec edit order" item 3 | What the apply committed to |
| `cvp_mcp/tool_access.py` | Real `tool_enabled(tool_name)`: `CVP_MCP_DISABLED_TOOLS` at call time, no `writes=` |
| `tests/fixtures/workspace_build_enums.json` | Which build/workspace fields were actually captured live |

## Verdict

The apply is **real progress, not cosmetic**. Change-control write tools are gone, the submit
gate is split across two env vars, replace semantics are written down, `request_id` is a UUID,
and the "submit is harmless" framing is corrected. Sections
**"Phase 2 — Ship decision"**, **"Global write gates (all phase 2 tools)"** items 1–5, and
**"Change control tools — out of v1"** are the strongest parts of the document.

What did **not** survive the apply is R3's central argument: *the risk lives in the body of an
endpoint you already decided to expose, so the guard has to be structural rather than prose.*
The revision converted most of R3's **mechanisms** into **descriptions**. The spec now tells the
implementer that root-path input writes replace the whole tree and that a tag query is a full
replacement, then still specifies both tools with the destructive behaviour as the **default
path** and no flag, no expected-current value, and no fail-closed preflight standing in front of it.

| R3 finding | Status after apply |
| --- | --- |
| C1 CC create can `start` a CC | **Closed** — both CC tools cut from v1 (spec §"Change control tools — out of v1") |
| C2 root-path input replace | **Partly** — documented, not gated (RR2-C2) |
| C3 tag query replace | **Partly** — documented, not gated (RR2-C3) |
| C4 model-settable submit | **Mostly closed** — env split + proof-of-review, but the proof cannot prove what it claims (RR2-C6) |
| C5 upsert + unrestricted Mako | **Partly** — `overwrite_existing` + lint added; lint has the wrong scope (RR2-C5, RR2-I9) |
| I5 `workspace_id=""` is mainline | **Not applied** (RR2-C1) |
| I10 helper-level `request` allowlist | **Not applied** — prose only (RR2-C4) |
| I8 secret redaction | **Not applied** (RR2-I1) |
| I9 abandon / cancel-build | **Not applied** (RR2-I5) |
| I11 delete-studio impact preview | **Not applied** (RR2-I6) |
| I1 `get_cvp_studio_assigned_tags` | **Half applied** — inputs tool added, tags tool not (RR2-I7) |
| I3 `request_id` UUID | **Closed** (spec §`build_cvp_workspace`, "Do not default `b1`") |
| I7 submit ≠ harmless / auto-execute | **Closed** — verified 2026-08-20, stated in §"Canonical provisioning workflow" |
| I2 dead `path_values` | **Closed** — body now interpolates `<path_values or []>` |

| Severity | Count |
| --- | --- |
| Critical | 6 |
| Important | 10 |
| Minor | 6 |

---

## Q1 — Are CC create/delete truly out? Any remaining path to `start` / execute?

**Out at the tool layer: yes.** §"Change control tools — out of v1" is unambiguous, the tool
inventory table carries no CC row, and §"Global write gates" item 7 forbids compound tools.
§"Canonical provisioning workflow" step 7 keeps approve/execute in the CVP UI. The
2026-08-20 verification that submit-created CCs sit in pending approval is recorded in
§"Ship decision" and closes R3-I7's blocking item.

**Out at the mechanism layer: no.** See RR2-C4. The one shared write helper the spec defines
takes an arbitrary `path`, and nothing in the document constrains it.

Correctly closed and worth recording so a later wave does not re-litigate: no EOS CLI path, no
`configure terminal`, no direct device write, no configlet write, `ApproveConfig` absent,
compliance `GetConfig` read-only.

### RR2-C4 (Critical) — `post_resource_config(path, body)` is still an unvalidated passthrough

§"Global write gates" ends with:

> Shared helper: `post_resource_config(path, body) -> dict` using existing bearer + host
> allowlist; parse a single JSON object (writes are not NDJSON `/all`).

The host allowlist constrains the **hostname**. Nothing constrains the **path** or the **body**.
The three prohibitions that replaced R3-C1 and R3-I10 are all written as per-tool discipline:

> **Hard-coded `request` enum per tool.** Never pass `Workspace.Request` through from the model.
> Do **not** expose `REQUEST_SUBMIT_FORCE` or `REQUEST_ROLLBACK`. Do **not** send
> `start` / `schedule` on ChangeControlConfig (CC write tools are out of v1 entirely).

"Do not expose" is a statement about which tools exist. It is not a check that runs. R3's
recommended edit #1 was explicitly *"the write helper must validate the body, not just the
path"*, and that subsection was not added. Consequences:

- Any future tool, any refactor that factors build and submit into a shared
  `_workspace_request(workspace_id, request)`, or any bug that lets a caller-derived string reach
  the `request` field re-opens `REQUEST_SUBMIT_FORCE` / `REQUEST_ROLLBACK` with one changed
  string, at the same URL `build_cvp_workspace` already posts to.
- `changecontrol/v1/ChangeControlConfig` is a reachable path for the helper. The exclusion of CC
  tools is enforced only by nobody writing one.

**Concrete spec edit.** Add a subsection **"Field-level dangers (write helper contract)"**
immediately after the "Hard-coded `request` enum per tool" paragraph:

> `post_resource_config(path, body)` MUST enforce, independently of any caller:
>
> 1. **Path allowlist** — exactly `{workspace/v1/WorkspaceConfig, studio/v1/InputsConfig,
>    studio/v1/AssignedTagsConfig, studio/v1/StudioConfig}`. Any other path raises before the
>    request is built. `changecontrol/*` and `configlet/*` are not on the list in v1.
> 2. **`request` allowlist** — if the body contains a `request` key, its value must be in
>    `{REQUEST_START_BUILD, REQUEST_SUBMIT}`. Reject every other value, including
>    `REQUEST_SUBMIT_FORCE` and `REQUEST_ROLLBACK`.
> 3. **Body key denylist** — reject any body containing `start`, `schedule`, or `change`
>    at any depth.
> 4. **Non-empty `key.workspace_id`** — see RR2-C1.
>
> These are backstops. Each tool still constructs its own literal enum; the helper exists so the
> refusal is structural rather than remembered.

Add to §"Phase 2 testing": helper-level tests that a disallowed path, a `REQUEST_ROLLBACK` body,
and a body carrying `start` are each rejected **without** an HTTP call, asserted against the
helper directly rather than through a tool.

### RR2-C1 (Critical) — no write tool refuses `workspace_id=""`, which is mainline

R3-I5 raised this; nothing in the revision applies it. The spec establishes the meaning itself,
twice, in Phase 1:

> **Mainline** | When `workspace_id` is `None`, use `""` (empty string). Verified 2026-08-21

The Phase 2 global gates cover exact-`"1"` env vars, `confirm`, no compound tools, audit logging,
and `^builtin-`. There is no non-empty check. Per tool, only `create_cvp_workspace` rejects an
empty id, and it does so in its own preflight row, not as a shared rule.

Failure scenario, single call, no other gate violated:

```
set_cvp_studio_inputs(studio_id="studio-ntp", workspace_id="", inputs={...}, confirm=True)
```

`workspace_id=""` is not `^builtin-`, so the denylist passes. `confirm=True` skips the dry-run.
The target is **mainline**, bypassing workspace → build → review → change control entirely. The
same hole applies to `assign_cvp_studio_tags`, `create_cvp_studio`, `delete_cvp_studio`,
`build_cvp_workspace`, and `submit_cvp_workspace`. Whether the server rejects a mainline
`InputsConfig` write is **unverified** — the write-probe table in §"Write access" probed
`InputsConfig` and `AssignedTagsConfig` but does not record which `workspaceId` it used, so the
200s there cannot be read as "mainline is refused."

**Concrete spec edit.** Add as global write gate item 9, next to the `builtin-` rule:

> **Non-empty `workspace_id` on every write.** After `strip()`, refuse empty
> (`error="workspace_id_required"`). `""` is **mainline**; MCP never writes to mainline directly.
> This is a client-side control and does not depend on server behaviour, which is unverified for
> mainline `InputsConfig` / `StudioConfig` writes.

Add to §"Phase 2 still to verify": *"Does the server reject `key.workspace_id=""` on
`InputsConfig` / `AssignedTagsConfig` / `StudioConfig`? Probe read-only before enabling writes;
either way the client-side refusal ships."*
Add to §"Phase 2 testing": *"Refuse empty / whitespace `workspace_id` on all writes."*

---

## Q2 — Are input path + tag query replace semantics and dry-run previews specified tightly enough?

**The description is now excellent. The enforcement is absent.** Both "Replace semantics
(critical)" rows are accurate, quotable, and better than R3 asked for on the documentation axis.
Neither converts into a refusal.

### RR2-C2 (Critical) — full-tree input replacement is still the default and needs no extra flag

§`set_cvp_studio_inputs`:

> **Parameters** | `studio_id: str`, `workspace_id: str`, `inputs: dict`,
> `path_values: list[str] \| None = None`, `confirm: bool = False`
> **Serialize** | ... Normalize `path_values is None` to `[]`.
> **Replace semantics (critical)** | ... The root path (`values: []`) **replaces the entire input
> tree** ... Dry-run **must** state: path, whether this is a full-tree replace, and a preview of
> current inputs

So the parameter default (`None`) normalizes to the most destructive value (`[]` = root = wipe),
and the entire protection is that a `full_tree_replace: bool` appears in a dry-run the caller is
free to skip by passing `confirm=True` on the first call. R3-C2's required fix — *"refuse the
root path unless an explicit `replace_all_inputs=True` is passed"* — was not adopted.

R3's NTP/timezone scenario is unchanged by the revision: an agent asked to add one NTP server
posts an `inputs` blob containing only that server at the default path, the SFO timezone inputs
disappear, the build succeeds, and the resulting change control removes config from every SFO
device. Nothing in the call looks like an error.

There is a second, quieter reason everyone will end up on the root path: **the spec never
documents the non-root path syntax.** `path_values: list[str]` is typed but never given an
example, and studio.v1's key-based bracket form
(`["ntpServers", "[ip=10.10.10.10]", "vrf"]`) appears nowhere in the spec. A caller who wants a
scoped write has no documented way to construct one, so the safe path is unreachable and the
unsafe one is the default. This is the single most likely way an implementer ships something
that destroys config while following the spec exactly.

**Concrete spec edits** to the `set_cvp_studio_inputs` table:

1. Parameters gain `replace_all_inputs: bool = False`.
2. New **Refuse** row: *"`path_values` empty or `None` while `replace_all_inputs` is False →
   `error="root_path_requires_replace_all_inputs"`, no POST. The root path is a whole-tree
   replacement, not a default."*
3. New **Path syntax** row with a worked non-root example and the studio.v1 bracket notation:
   `path_values=["ntpServers", "[ip=10.10.10.10]", "vrf"]`, plus a statement that path elements
   are matched against the schema from `get_cvp_studio`.
4. Extend the **Replace semantics** row with the read-modify-write guidance R3 asked for: fetch
   current inputs via `get_cvp_studio_inputs`, deep-merge, and put a **before/after diff** in the
   dry-run, not just a preview of the current document.

### RR2-C3 (Critical) — the tag-query dry-run preview is not implementable as specified

§`assign_cvp_studio_tags`:

> **Dry-run** | Resolve the query to a **target preview**: device ids + count (inventory/tag
> read). State that this **replaces** the previous assignment.
> **Returns** | `studio_id`, `workspace_id`, `query`, `target_device_ids`, `time`

Three things are missing that the preview depends on:

- **No endpoint.** §"Endpoint access matrix" contains inventory, studio, workspace, configlet,
  changecontrol, and configstatus rows. There is no `tag/v1` row and no probe result. "How do I
  turn `datacenter:NY` into a device list with this token" is unanswered, unprobed, and load-
  bearing for the only preview this tool has.
- **No read of the current query.** R3-I1 asked for `get_cvp_studio_assigned_tags`; the apply
  added `get_cvp_studio_inputs` and not this one (RR2-I7). Without it the preview can show the
  **new** device set but not the **old** one, so it cannot show what is being unassigned, which
  is the destructive half.
- **No concurrency guard and no empty-string gate.** R3-C3 required `expected_current_query`
  and `unassign_all=True` for `query=""`. Neither is present; the spec only says empty string is
  "destructive, not 'just another query'." The tool is also still named `assign_*` while doing a
  replace, which R3 asked to rename.

R3's failure mode stands verbatim: a studio assigned `datacenter:NY`, an agent asked to *also*
cover one lab switch, `assign_cvp_studio_tags(query="device:720xp-24")`, every NY device loses
that studio's designed config on submit.

**Concrete spec edits:**

1. Rename to `set_cvp_studio_tag_query` in the tool reference, tool inventory, and canonical
   workflow step 3. Keep one line noting the rename so the R3 name is traceable.
2. Parameters gain `expected_current_query: str` (**required**, echoed from
   `get_cvp_studio_assigned_tags`) and `unassign_all: bool = False`.
3. New **Refuse** rows: stale `expected_current_query` → `error="tag_query_changed"`, no POST;
   `query.strip() == ""` without `unassign_all=True` → `error="empty_query_requires_unassign_all"`.
4. Extend **Dry-run** to require **both** device sets: `current_target_device_ids`,
   `new_target_device_ids`, and the computed `devices_losing_studio` list. A count alone hides
   the removal.
5. Add the tag-resolution endpoint to §"Endpoint access matrix" with a live probe result, and
   add *"tag query → device list resolution endpoint + token access"* to
   §"Phase 2 still to verify" as **blocking**. If it is not readable with the container token,
   say so in the tool table and mark the tool not shippable, rather than shipping a tool whose
   only safety feature silently degrades to "query string echoed back."

### RR2-I4 (Important) — "fail closed" is specified in exactly one place

§`create_cvp_workspace` preflight says *"If the read fails, **fail closed** (no POST)."* That
phrase appears nowhere else. Every other preflight and dry-run read in Phase 2 — current inputs,
current tag query, device resolution, `StudioSummary.immutable` / `from_package`, workspace
existence, build re-fetch — has no stated behaviour when the read errors or 403s. An implementer
who writes `except Exception: warnings.append(...)` and proceeds is not contradicting the spec.
Given that `configstatus` already returns 403 on this token, partial read failure is the expected
case, not the exotic one.

**Concrete spec edit.** Global write gate item 10: *"Every preflight and dry-run read fails
closed. If a read required to build a preview or evaluate a refusal does not return 200, the tool
returns `error="preflight_failed"` with the failing read named, and performs no POST/DELETE. A
`warnings` entry is not sufficient."*

---

## Q3 — Are the env gates, proof-of-review, no-FORCE/ROLLBACK, and the builtin denylist solid?

**Env gates: solid, and the best-executed part of the apply.** Items 1–5 of §"Global write gates"
correctly match the real `tool_enabled` in `cvp_mcp/tool_access.py` — no fictional `writes=True`,
`CVP_MCP_DISABLED_TOOLS` kept as an independent call-time deny list, exact-`"1"` stated twice with
the rejected values enumerated (`""`, `"0"`, `"true"`, `"yes"`), registry-time non-registration so
disabled writes are undiscoverable, **and** a runtime backstop before the mutating request. The
`ALLOW_WRITES` / `ALLOW_SUBMIT` split with submit defaulting off even when writes are on is
exactly R3-C4's fix. The dry-run precedence ladder is unambiguous. I have no findings against
items 1–5 or against the precedence ladder.

**Proof-of-review: the intent is right, the specified mechanism cannot deliver it.**

**No FORCE / ROLLBACK: stated, not enforced.** Covered by RR2-C4.

**Builtin denylist: scope fixed, matching still weak.** See RR2-I3.

### RR2-C6 (Critical) — proof-of-review binds to an immutable record and cannot detect the thing it exists to detect

§`submit_cvp_workspace`:

> **Proof-of-review** | Caller passes `build_id` and `build_proof` (build hash or `last_modified`
> from `get_cvp_workspace_build`). Tool **re-fetches** that build and refuses if missing, not
> success, `errors` non-empty, or the proof does not match ... **Refuse if workspace contents
> changed after that build.**

Two defects:

1. **The re-fetch proves nothing about the workspace.** A `WorkspaceBuild` record is keyed
   `(workspaceId, buildId)` and is terminal once the build ends. Its `last_modified` will still
   match on re-fetch after any number of subsequent `InputsConfig` / `AssignedTagsConfig` writes
   into the same workspace, because those writes do not touch the build record. The comparison
   the spec specifies therefore always passes and detects nothing. The bolded requirement
   ("refuse if workspace contents changed after that build") names no field, no comparison, and
   no endpoint, so an implementer who wires up the re-fetch has satisfied every concrete
   instruction in the row while implementing a no-op. This is the R3-C4 hole reappearing one
   layer down: the gate is prose, and the mechanism named beside it does not implement the prose.
2. **No proof field is known to exist.** `tests/fixtures/workspace_build_enums.json` captures
   build/workspace/response **enums** and the poll contract. It records no `version`, no content
   hash, and no `lastModifiedAt` on either `WorkspaceBuild` or `Workspace`. §"Phase 2 still to
   verify" marks the enum question **Done** but never asks whether a staleness token exists.
   "build hash or `last_modified`" is a guess presented as a contract.

The Arista precedent the spec cites, `ApproveConfig.version`, is a **monotonic version on the
object being mutated**, not a timestamp on a sibling record. The equivalent here is a workspace
version or last-modified compared against the build's completion time.

**Concrete spec edits:**

1. Add to §"Phase 2 still to verify" as **blocking**: *"Which field on `Workspace` (or
   `WorkspaceBuild`) monotonically advances on any `InputsConfig` / `AssignedTagsConfig` /
   `StudioConfig` write into that workspace? Capture it live into
   `tests/fixtures/workspace_build_enums.json` alongside the enums. Until captured,
   `submit_cvp_workspace` is not shippable."*
2. Rewrite the **Proof-of-review** row to name the comparison once the field is known:
   > Caller passes `build_id` and `build_proof`, where `build_proof` is the workspace staleness
   > token **as of the reviewed build** (field TBD, see "still to verify"). The tool re-fetches
   > **both** the build and the **workspace**, and refuses when: the build is missing, its state
   > is not `BUILD_STATE_SUCCESS`, its `errors` are non-empty, or the workspace's current
   > staleness token differs from `build_proof`. Comparing the build record against itself is
   > **not** a valid implementation of this row.
3. State the limit plainly, because it is currently implied and it matters for the homelab
   policy: *"Proof-of-review proves the workspace is unchanged since a successful build. It does
   **not** prove a human read the diff. The human control is CVP UI review at workflow step 5 and
   change-control approval at step 7; `ALLOW_SUBMIT` is the operator's standing consent, not
   per-change consent."*

### RR2-I3 (Important) — `^builtin-` is normalized for whitespace but not case, and no positive allowlist was added

§"Global write gates":

> **`builtin-` denylist on every write**, not only delete: refuse `workspace_id` matching
> `^builtin-` (after strip).

The scope fix is the important half of R3-I4 and it landed. Two residuals:

- **Matching.** R3 asked for `strip().lower()`. The spec says "(after strip)" only, so
  `Builtin-studios-V0-l3ls` passes the denylist. A one-word omission reintroduces the bypass.
- **Allowlist.** R3's stronger invariant — *"MCP may only write to workspaces whose id matches
  `^ws-mcp-`"* — was not adopted. `ws-mcp-<purpose>-<YYYYMMDD>-<uuid8>` survives only as a
  **Recommended id** row on `create_cvp_workspace`, which is a naming convention, not a control.
  A closed allowlist would subsume RR2-C1 (mainline `""` fails it), the builtin case, and every
  future prefix nobody has thought of yet.

**Concrete spec edit.** Replace the builtin bullet with:

> **Workspace id guard on every write.** After `strip()`: refuse empty; refuse
> `^builtin-` case-insensitively (`strip().lower()`); and require `^ws-mcp-` — MCP writes only to
> workspaces it created. Refusing builtin workspaces is deliberate homelab policy, not an API
> constraint; Arista's own docs demonstrate deleting `builtin-studios-V0-l3ls`. Studio-level
> refusals use `StudioSummary.immutable` / `from_package`, never a name regex, since
> Arista-provided studios are `studio-*` and the AVD/L3LS studios in this homelab match no
> reserved prefix at all.

Add to §"Phase 2 testing": *"Refuse `Builtin-` and leading-whitespace variants; refuse a
`workspace_id` not matching `^ws-mcp-`."*

---

## Q4 — What Critical / Important holes remain that an implementer could still ship unsafely?

The six Criticals above, plus the Important findings below. The unifying pattern is worth stating
because it predicts where the next hole will be: **the apply moved R3's findings from the
"required mechanism" column into the "documented hazard" column.** Every remaining Critical is a
place where the spec accurately describes a destructive behaviour and then specifies the tool with
that behaviour reachable by default.

An implementer can read the revised Phase 2 end to end, implement every row faithfully, pass every
test in §"Phase 2 testing", and still ship tools that: write to mainline (RR2-C1), wipe a studio's
input tree in one call (RR2-C2), unassign a studio from production devices via an `assign_*` call
(RR2-C3), reach `REQUEST_ROLLBACK` after one refactor (RR2-C4), push `shutdown` to an interface
through unlinted inputs (RR2-C5), and submit on a proof that always matches (RR2-C6).

Note also that `confirm` is model-settable on **every** write, and only submit gained a proof
requirement. The drafting writes are not harmless just because they need a later submit: they
change what a subsequently approved submit carries. A human who approves "add an NTP server" can
be approving "replace the entire input tree" if the draft-time call went to the root path.

### RR2-I1 (Important) — secret-typed studio inputs are redacted nowhere, and the dry-run mandates echoing them

R3-I8 flagged studio.v1's `SecretInput` service returning `plain_text`. The words "secret" and
"redact" do not appear in the spec. What the revision *did* add makes the exposure worse in one
specific place — §`set_cvp_studio_inputs` dry-run:

> a preview of current inputs from `get_cvp_studio_inputs` (read-only)

So the write preflight is now **required** to pull the current input document into the MCP
response and therefore into agent context. Upstream of it, Phase 1 `get_cvp_studio_inputs`
returns `inputs` as a parsed JSON object with no redaction contract. The only protection anywhere
is audit-log-side, in global gate 8: *"Never log token, Authorization, full inputs, template body,
or input schema."* That protects the container log and not the response.

This is in RR2 scope rather than RR1 because it is the write preflight that compels the echo.

**Concrete spec edits:**

1. Global write gate 11: *"Redact secret-typed fields. Before any dry-run preview, tool response,
   or audit line includes input values, consult the studio `input_schema` from `get_cvp_studio`
   and replace secret-typed field values with `"<redacted>"`. Never call the `SecretInput`
   service. If the schema cannot be read, redact **all** input values rather than emitting them
   (fail closed, per gate 10)."*
2. Mirror one sentence into Phase 1 `get_cvp_studio_inputs`: the same redaction applies to the
   read tool, since the write preview is built from its output.
3. Add to §"Phase 2 testing": *"Secret-typed input fields are redacted from dry-run output, tool
   responses, and audit lines; schema-read failure redacts everything."*

### RR2-I5 (Important) — no recovery path, and the in-progress guard is an unresolved TODO

R3-I9 asked for `abandon_cvp_workspace` and `cancel_cvp_workspace_build`, the rare case where more
write surface is strictly safety-increasing. Not adopted. `REQUEST_ABANDON` is the documented way
to make a drafted workspace un-submittable; `delete_cvp_workspace` discards the evidence instead.
Related, §`build_cvp_workspace` ends with:

> Refuse if workspace missing, builtin, or a build is already in progress (**once live states are
> known**).

That parenthetical is a TODO inside a shipping guard, and the states *are* now known:
`tests/fixtures/workspace_build_enums.json` records `BUILD_STATE_IN_PROGRESS` (protobuf) and the
`Workspace.responses.values[<request_id>]` poll contract. §"Phase 2 still to verify" separately
lists *"Rate limits / concurrent builds — avoid parallel builds on one workspace"* as open, so the
guard has no definition anywhere.

**Concrete spec edits:** add `abandon_cvp_workspace` (`REQUEST_ABANDON`) and
`cancel_cvp_workspace_build` (`REQUEST_CANCEL_BUILD`) to the tool reference and tool inventory,
extend the RR2-C4 `request` allowlist to `{REQUEST_START_BUILD, REQUEST_SUBMIT, REQUEST_ABANDON,
REQUEST_CANCEL_BUILD}`, and resolve the in-progress guard against the fixture rather than deferring
it: *"refuse when the newest `responses.values` entry for this workspace has no terminal
`WorkspaceBuild` state."*

### RR2-I6 (Important) — `delete_cvp_studio` still reads like a cleanup operation

R3-I11 asked for an impact preview. The tool table is unchanged in substance: Endpoint,
Parameters, Body, Prerequisite. It refuses `immutable` / `from_package`, which is the R3-M5 half.
It does not check `in_use`, does not resolve the assigned device list, and specifies no dry-run
content — even though the spec adds `in_use` to `get_cvp_studios` in Phase 1 and says
*"prefer these over name regex for later write refusals."* The signal was added and left unused.

Deleting a studio removes every line it generated from the designed config of every assigned
device; on submit that is a change control full of negation config.

**Concrete spec edits** to `delete_cvp_studio`: add a **Dry-run** row requiring the current tag
query, `StudioSummary.in_use`, the resolved device list, and the sentence *"this removes designed
config from N devices: ..."*; add a **Refuse** row for `in_use` true without an explicit
`accept_device_impact=True`; and state that unassign and remove must occur **in the same
workspace** (Arista's example uses one `del-studio` workspace for both; split across workspaces the
delete builds against stale assignments).

### RR2-I7 (Important) — `get_cvp_studio_assigned_tags` is still missing from Phase 1

R3-I1 asked for two read tools. `get_cvp_studio_inputs` was added, with a table, parameters, and a
place in §"Phase 2 information callers must provide". The tags equivalent was not. The consequence
chain: no current-query read → no `expected_current_query` (RR2-C3) → no old-vs-new device set in
the tag dry-run → no impact preview for `delete_cvp_studio` (RR2-I6). One missing read tool blocks
three write-side controls.

The §"Phase 2 information callers must provide" table shows the gap directly: the "Target devices"
row says *"Inventory/tags; dry-run must show count/ids"* with no Phase 1 tool named, while every
other row names one.

**Concrete spec edit.** Add `get_cvp_studio_assigned_tags` to Phase 1 (`GET
/api/resources/studio/v1/AssignedTags/all` filtered by `studio_id` + `workspace_id`, or the keyed
GET if it exists), add it to the tool inventory, and name it in the "Target devices" row. Probe the
endpoint and add the result to §"Endpoint access matrix" — it is not in the matrix today.

### RR2-I8 (Important) — the auto-approve verification has no expiry and no runtime check

§"Ship decision" records the 2026-08-20 finding that submit-created CCs stay pending approval,
then adds: *"Re-check this if tenant General / Change Control settings change."* Nobody will
notice a tenant setting change, and no MCP tool can. The entire homelab human-in-the-loop
guarantee rests on a point-in-time observation with no monitor.

This is the correct verification and the wrong lifecycle. **Concrete spec edit:** make it a dated
release-checklist item rather than a note — *"Re-verify before enabling `ALLOW_WRITES` and at
least every 90 days; record the date here"* — and add to §"Phase 2 still to verify" whether the
tenant CC-settings resource is readable with the container token. If it is, require
`submit_cvp_workspace`'s dry-run to report the tenant's auto-approve / auto-execute setting so the
guarantee is checked at call time rather than assumed from a note.

### RR2-I9 (Important) — `create_cvp_studio`'s existence check is mainline-only

§`create_cvp_studio` preflight: *"If studio exists in mainline and `overwrite_existing` is false →
refuse."* R3-C5's upsert hazard also applies within the workspace: a studio already copied into
the target workspace (by an earlier call, or by another operator working the same workspace) is
silently replaced, because it does not exist in **mainline** under that id or because the mainline
check passes while the workspace copy is the thing being overwritten.

**Concrete spec edit:** *"Keyed GET the studio in **both** mainline (`workspaceId=""`) and the
target workspace. Refuse unless `overwrite_existing=True` when either exists. The dry-run must
show the existing `template_sha256` (from `get_cvp_studio`, `body=False`) alongside the SHA-256 of
the incoming `template_body`, so the reviewer sees that a replacement is happening and what it
replaces."*

### RR2-I10 (Important) — the audit log cannot answer "what was written"

Global gate 8 logs tool name, `workspace_id`, `studio_id`, `request_id`, outcome, and correctly
forbids logging the payload. R3-I6 asked for the middle ground and it was not applied: after an
incident you can prove *that* inputs were written but not *what*. **Concrete spec edit:** add to
gate 8 — *"plus `payload_sha256` (SHA-256 of the exact serialized body), `path_values`,
`full_tree_replace`, and the tag `query` string. These are identifiers and structure, not values,
and are safe to log under the redaction rule in gate 11."*

---

## Q5 — Template lint and the `create_cvp_studio` upsert vs the 720xp-24 shutdown class

**Verdict: the upsert half is adequately addressed. The lint half is not, because it guards the
path the spec tells callers not to use, and leaves the path it tells them to prefer unguarded.**

The lint row itself is well-drafted and names the right primitives:

> `template_body` is unrestricted Mako → EOS. Lint for disruptive primitives (`shutdown` under an
> interface, `no interface`, `reload`, `write erase`, `no ip routing`, management/uplink edits).
> Refuse unless `allow_disruptive` names the **specific interfaces** (same bar as homelab EOS
> safety rules). Surface matching lines in dry-run.

The "names the specific interfaces" bar correctly mirrors the homelab rule that produced the
720xp-24 `Ethernet18` / `Ethernet21` incident. Three defects around it.

### RR2-C5 (Critical) — the lint covers `template_body` only, and the spec steers callers to the unlinted path

R3-C5 required linting *"`template_body` **and `inputs`**"*. The apply kept the first and dropped
the second. `set_cvp_studio_inputs` has no lint row, no disruptive-primitive check, and no
`allow_disruptive` parameter.

That is backwards relative to the spec's own guidance. The same `create_cvp_studio` table says:

> **Caller must supply** | Complete `input_schema` graph. Prefer inputs/tags on existing studios
> over creating templates from MCP.

So the spec routes agents away from the linted tool and toward the unlinted one. A Mako template
is a **generator**: `interface ${intf}` / `${state}` renders whatever the inputs say. Setting
`state` to `shutdown` on an existing, already-approved, non-`immutable` studio produces exactly the
720xp-24 outcome, through `set_cvp_studio_inputs`, with zero lint anywhere in the path. The 5×
`CONFIG_TYPE_STUDIO_STATIC` AVD entries on 720xp-24 in
`tests/fixtures/designed_config_sources_720xp24.json` are a reminder that this homelab's real
config comes from existing studios driven by inputs, not from MCP-authored templates.

A second gap: static lint of Mako source has an unbounded false-negative rate. `shutdown` can be
`${action}`, a Mako `<% %>` block, a schema default, or a concatenation. The spec does not say what
the lint does when it **cannot** statically decide.

**Concrete spec edits:**

1. Add a **Template/input lint** row to `set_cvp_studio_inputs` with the same primitive list and
   the same `allow_disruptive` bar, applied to the string values in the `inputs` document. State
   that this is the **primary** lint surface because the spec prefers inputs over new templates.
2. Add to the `create_cvp_studio` lint row: *"Fail closed on undecidable templates. If a
   `${...}` interpolation, a `<% %>` block, or a `% for` body appears inside an `interface`
   stanza, the lint cannot decide and the tool refuses unless `allow_disruptive` names the
   interfaces. Do not treat 'no literal match' as 'clean'."*
3. Add `allow_disruptive: list[str] | None = None` to the **Parameters** row of both tools. It is
   referenced in the lint prose but is absent from every parameter list in the spec, so a literal
   implementer produces a tool that can only ever refuse.
4. Add to §"Phase 2 testing": *"`set_cvp_studio_inputs` with an input value containing `shutdown`
   → refused without `allow_disruptive`"* and *"a `template_body` with `${action}` inside an
   `interface` stanza → refused without `allow_disruptive`."*

### RR2-I2 (Important) — the only lint that generalizes is on the built config, and it is not specified

Source-level lint on templates and inputs is worth having and is structurally incomplete: the only
artifact where a disruptive line is unambiguously present is the **built** designed config. The
spec has a build step and a human review step and never connects them to the lint.

Honest caveat, and the reason this is Important rather than a required edit: it is **not
established that a workspace-scoped designed-config diff is reachable with this token**.
`configstatus` is 403, and compliance `GetConfig` takes `device_id` + `timestamp` + `type` with no
workspace parameter, so it reads mainline designed config rather than a workspace's pending build.
R3 did not resolve this either.

**Concrete spec edits:**

1. Add to §"Phase 2 still to verify": *"Is there a workspace-scoped designed-config or config-diff
   resource readable with the container token (e.g. under `workspace.v1` build artifacts)? Probe
   read-only."*
2. If reachable: require `submit_cvp_workspace`'s dry-run to run the disruptive-primitive lint over
   the built diff and refuse without `allow_disruptive`. That single check subsumes both source
   lints and catches interpolation-driven and input-driven cases.
3. If not reachable: say so explicitly in the lint row — *"source lint is best-effort and cannot
   see interpolated or input-driven values; the CVP UI diff at workflow step 5 is the only
   complete control"* — so nobody mistakes a passing lint for a safety guarantee.

### Upsert handling: adequate

`overwrite_existing: bool = False` plus the `immutable` / `from_package` refusal is R3-C5's
required fix and it landed. RR2-I9 narrows the existence check; the design is sound.

---

## Minor findings

| # | Finding |
| --- | --- |
| RR2-M1 | `create_cvp_studio` has **no Body row**, unlike every other write tool, and no `template_type` parameter. R3-M3: the wire body needs `template.type: "TEMPLATE_TYPE_MAKO"`. Its parameter list is also the only one in the section with no type annotations. Add the body skeleton and `template_type: str = "TEMPLATE_TYPE_MAKO"`. |
| RR2-M2 | §"Canonical provisioning workflow" still omits where `create_cvp_studio` goes (R3-M4). Arista's order for a new studio is create workspace → **create studio** → inputs/tags → build → submit; inputs cannot be set for a studio not yet in the workspace. Add it as step 1b. |
| RR2-M3 | Only `post_resource_config` is specified, but `delete_cvp_workspace` uses DELETE, and DELETE responses are `{"key":..., "time":...}` while POST returns `{"value":..., "time":...}` (R3-M2). Either add `delete_resource_config(path)` or state that the helper parses both shapes. |
| RR2-M4 | `submit_cvp_workspace`'s parameter list is `..., confirm: bool = False, allow_submit: bool = False, build_id: str, build_proof: str` — required parameters after defaulted ones, which is a Python syntax error as written. Reorder so `build_id` / `build_proof` precede the defaulted flags, or give them no defaults and move the flags last. |
| RR2-M5 | `tests/fixtures/workspace_build_enums.json` lists `BUILD_STATE_SKIPPED` in `state_protobuf_also`, but the §"Workspace build poll" state machine classifies only SUCCESS / FAIL / CANCELED (terminal) and IN_PROGRESS (non-terminal), sending everything else to "warn and keep polling until timeout." A skipped build therefore polls for the full 120s and then blocks submit. That is fail-closed and so only Minor, but classify `BUILD_STATE_SKIPPED` as terminal-not-success explicitly. |
| RR2-M6 | `allow_submit: bool = False` remains as a model-settable parameter alongside the `ALLOW_SUBMIT` env var. Harmless belt-and-braces, but state the precedence once: env off → `submit_disabled` regardless of the parameter; env on and parameter false → refuse. Otherwise an implementer may read the parameter as the gate. |

---

## Residual-risk summary for the ship decision

The §"Ship decision" text — *"Phase 2 write tools do not ship in the first MCP release"* — remains
correct and is the control that makes everything above non-urgent. Nothing in RR2 changes the
Phase 1 recommendation.

If and when the homelab opts into writes, the ordering I would enforce:

1. **Blocking before any write tool ships:** RR2-C1 (non-empty `workspace_id`), RR2-C4 (helper
   path/body/enum allowlist), RR2-C2 (`replace_all_inputs` + documented path syntax), RR2-C5
   (input-side lint), RR2-I1 (secret redaction), RR2-I4 (fail closed).
2. **Blocking before `set_cvp_studio_tag_query` / `delete_cvp_studio` ship:** RR2-C3, RR2-I6,
   RR2-I7, plus the tag-resolution endpoint probe.
3. **Blocking before `submit_cvp_workspace` ships:** RR2-C6 and the staleness-token capture. Until
   a field is identified live, submit has no working proof-of-review and should stay unregistered
   even with both env vars set.

---

```
Bucket RR2: success
Files: docs/research/studios-support-rereview-RR2.md
Commit: HEAD of feat/studios-support-rereview-bucket-RR2-claude (this file's own commit;
        a self-referential sha cannot be embedded in the object it names)
Notes:
  1. CC create/delete are genuinely out and the env-gate stack (exact "1", registry-time
     non-registration, runtime backstop, separate ALLOW_SUBMIT) is solid — but the single shared
     write helper `post_resource_config(path, body)` has no path allowlist, no `request` enum
     allowlist, and no body denylist, so REQUEST_SUBMIT_FORCE / REQUEST_ROLLBACK / ChangeControl
     `start` are prose-excluded rather than structurally excluded (RR2-C4).
  2. The apply turned R3's required mechanisms into documented hazards. Root-path input replace is
     still the default with no `replace_all_inputs` flag and no documented non-root path syntax
     (RR2-C2); the tag query has no `expected_current_query`, no `unassign_all` gate, no current-
     query read tool, and no probed tag-resolution endpoint (RR2-C3); and no write tool refuses
     `workspace_id=""`, which is mainline (RR2-C1, R3-I5 never applied).
  3. The 720xp-24 shutdown class is NOT closed: the template lint covers `template_body` only,
     while the spec tells callers to "prefer inputs/tags on existing studios" — the one path with
     no lint at all (RR2-C5). Separately, proof-of-review re-fetches an immutable build record, so
     the specified comparison always matches and cannot detect post-build workspace mutation, and
     no staleness field has been captured live (RR2-C6).
```
