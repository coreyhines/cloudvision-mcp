# Bucket R5 — Testing, contradictions, open questions

Reviewer: cursor-named-sonnet (subagent, sonnet)
Scope: `docs/studios-support-spec.md` (full), cross-checked against
`tests/test_uri_fetch.py`, `tests/test_endpoint_seed.py`, `tests/test_envelope_and_parse.py`,
`tests/test_config_compliance_rest.py`, and the `cvp_mcp/grpc/*.py` modules the spec claims to
reuse. No product files edited.

Severity legend: **Critical** = blocks correct implementation or will actively mislead;
**Important** = should be fixed before Phase 1/2 coding starts; **Minor** = cosmetic /
clarity / drift risk.

---

## 1. Internal contradictions

### 1.1 [Important] "Role-grant" narrative left over from debugging, but the conclusion is "it's not a role/grant issue"

The **Service account vs user role** section opens by framing the 403 as a role-assignment
question:

> "CloudVision service accounts have their **own** role assignments (Settings → Access
> Management → Service Accounts)... Permissions are evaluated server-side from roles bound
> to that service account." (lines 37–40)

That setup primes a reader to go fix a role binding for the service account. But the same
section's own re-verification conclusion reverses that:

> "**Re-checked 2026-08-19:** Config read does **not** require fixing a configstatus
> permission in the role editor... configstatus Resource API remains 403 — likely a
> separate Resource API auth boundary on this staging instance, not an absent
> network-admin checkbox." (lines 52–55)

So the section title is "(important)" and spends four paragraphs on role-assignment
mechanics, then the payoff is "role assignment is not the fix; use a different API family
entirely (compliance) and stop probing configstatus." An implementer skimming for the
actionable fact could walk away chasing an IAM role-editor change that the spec explicitly
says won't help. Recommend collapsing this into: state up front "service-account roles are
separate from your user role (that's why nothing you can do in the UI as `network-admin`
fixes this)", then go straight to "configstatus is 403 by API-layer design here; compliance
GetConfig is the correct path" — without implying role-editor investigation is a next step.

### 1.2 [Critical] Phase 2 is fully designed, then separately marked "should we even ship it"

The spec spends ~150 lines (lines 195–363) writing exact endpoints, parameters, request
bodies, and a canonical workflow for ten Phase 2 write tools, phrased as settled design
("Tool reference (parameters, API, response)"). Yet the final Open Questions section asks:

> "3. Whether phase 2 write tools should ship in homelab MCP at all, or stay read-only with
> human CVP UI for submits." (line 402)

If #3 is a genuinely open question, the entire Phase 2 tool-reference section (gates,
canonical workflow, per-tool endpoint tables) is premature — a "should we build this" call
should gate the "here is exactly how we'll build this" section, not follow it as a footnote.
Either promote OQ#3 to a decision that must be made **before** Phase 2 is read as a
committed design, or drop it from Open Questions and state explicitly in the Phase 2 intro
that shipping is already decided and only the *safeguards* are still up for debate.

### 1.3 [Minor] "Blocked on 403" is stated three times with slightly different framings, never fully reconciled with "why still call configstatus at all"

The "Why" motivation section says the existing tool "tries configstatus first (403 on
homelab Resource API), then falls back to compliance GetConfig" (line 14) — describing
current *implemented* behavior as if it's a reasonable two-step probe. The endpoint matrix
(lines 105–107) and access-matrix conclusion (line 126) both restate that configstatus is
403 and won't be usable here. The spec never asks whether `get_cvp_device_config`'s existing
"try configstatus first" step is worth keeping for the homelab CVP instance, given the spec's
own evidence that it deterministically fails there. Not a hard contradiction, but the spec
documents a known-dead code path without recommending removing the wasted round-trip, while
in the same breath telling new tools (`get_cvp_designed_config`) to skip configstatus
entirely (line 58). Worth a one-line recommendation either way.

### 1.4 [Minor] Two separate "still open" lists that don't cross-reference each other

"Phase 2 still to verify before coding" (lines 344–353) and the final "Open questions"
section (lines 398–404) are both unresolved-item lists, but they live in different parts of
the doc and overlap without acknowledging each other:

- Phase 2 table: "Configlet write APIs (`ConfigletConfig` POST) | Read works; write not
  probed — homelab uses AVD configlets" (line 349)
- Open questions #4: "Configlet write path for homelab AVD-generated configlets (if ever
  needed)." (line 403)

Same open item, tracked twice, phrased differently, no link between them — a maintainer
resolving one is likely to miss the other. Recommend merging into a single open-items table.

---

## 2. Phase 1 + Phase 2 test plans

### 2.1 [Important] The spec's own "reuse fetch_uri_with_bearer" instruction doesn't produce a list for the `/all` endpoints it needs to test

Phase 1 testing guidance says:

> "Add fixtures for the NDJSON stream shape, including a blank trailing line and a row whose
> `result.value` lacks `displayName`..." (lines 368–370)

This implies a parser that walks every NDJSON line and returns **all** rows (needed for
`get_cvp_studios` / `get_cvp_workspaces`, which must list every studio/workspace, not just
one). But the only NDJSON-handling code that currently exists —
`cvp_mcp/grpc/uri_fetch.py` (lines 103–114) and `cvp_mcp/grpc/config_async_flow.py`'s
`_decode_multi_json` (lines 86–128) — is written to return the **first** valid JSON object
found, then stop. Neither is a multi-row collector. The spec's "reuse the existing module
conventions" instruction (line 137: "Reuse `fetch_uri_with_bearer`") glosses over the fact
that a **new** all-rows NDJSON parser has to be written for the list tools, and the testing
section should call that out as a new unit under test — right now the fixture guidance reads
as if it's testing an existing capability rather than a yet-to-be-written one.

### 2.2 [Important] `get_cvp_workspace_build` endpoint is never present in the Endpoint access matrix — testing guidance implicitly assumes it's verified

The Endpoint access matrix (lines 94–107) probes 12 endpoints. `GET .../Workspace?key.workspaceId=`
and `GET .../WorkspaceBuild?key.workspaceId=&key.buildId=` (introduced only in the "Phase 1
add-ons" subsection, lines 187–191) appear nowhere in that matrix. Every other Phase 1
endpoint in the spec carries an explicit "confirmed 200" / "Was uncertain in July probe, now
200" annotation; these two do not. The testing section should require a fixture proving these
singular-keyed GETs actually return the shape assumed (e.g., a `state` enum field the poll
loop in `build_cvp_workspace`'s follow-up depends on — see "Phase 2 still to verify... Exact
build state enum values," line 353) rather than assuming success by analogy to the `/all`
variants that *were* probed.

### 2.3 [Minor] Test reference name doesn't match any identifier in the repo

> "Extend the existing host-allowlist tests, which already cover
> `_is_uri_host_allowed_cvp_host`." (lines 367–368)

No symbol named `_is_uri_host_allowed_cvp_host` exists. The public function is
`is_uri_host_allowed` (`cvp_mcp/grpc/uri_allowlist.py:24`, no underscore prefix); the test
covering the CVP-host case is `test_is_uri_host_allowed_cvp_host`
(`tests/test_uri_fetch.py:8`). The spec's reference conflates the test function's name with
a nonexistent private helper. Low stakes, but will cost a `grep` round-trip for whoever
implements this.

### 2.4 [Important] `search_cvp_studio_templates` test guidance doesn't specify which JSON paths should NOT match, only which should

The spec asks for two positive fixtures — "a match inside a nested `inputSchema`
description and another inside a template body" (lines 372–374) — and asserts the returned
JSON paths differ. It never specifies a fixture asserting the *false-positive-avoidance*
claim made earlier in the body: "The worked example: searching `logging` matched only the
*EOS Event Handler* studio... No studio template emits `logging host`." (lines 164–167).
That worked example is presented as the whole reason the tool is trustworthy, but the test
plan doesn't ask for a regression fixture that pins "template body does NOT contain X" as a
negative assertion — only that paths differ when there IS a match. Recommend adding an
explicit negative-match fixture (a studio input schema containing "logging" purely as a UI
label, with an assertion that no `CONFIG_TYPE_STUDIO` source flags it as a config-body hit).

### 2.5 [Minor] `body: bool = False` "length plus a hash" convention has no precedent in the codebase

`get_cvp_studio`: "support a `body: bool = False` argument and return a length plus a hash
when omitted" (line 149). No existing tool in `cvp_mcp/grpc/*.py` does length+hash for
large payloads — grepping the codebase for `hashlib`/`sha256` returns zero hits. The
existing precedent for oversized text (`cvp_mcp/grpc/config.py:40,384-388`,
`_MAX_RUNNING_CONFIG_CHARS`) is truncate-and-warn, not hash-and-omit. Not wrong, just a new
pattern the testing section should explicitly cover (fixture: body omitted → hash present,
no raw template text in payload) since there's nothing to copy from elsewhere in the repo.

### 2.6 [Confirmed not a problem] Phase 2 live-test safety instructions are actually consistent

Checked because the task flagged this as a risk area: the integration-test bullet — "create
→ inputs → assign → build → **delete workspace** without submit, using `ws-mcp-test-*`
prefix" (lines 362–363) — correctly avoids `submit_cvp_workspace` and matches the
`delete_cvp_workspace` guardrail against `^builtin-` (line 257) and the "No compound tools"
gate (line 213). One residual gap: the bullet doesn't say the integration test must also set
`CLOUDVISION_MCP_ALLOW_WRITES=1`, and per the Global write gates table, write tools are
described as "not registered unless env set" (line 211) — meaning the "Refuse writes when
`CLOUDVISION_MCP_ALLOW_WRITES` unset" unit test (line 359) can't be exercised by calling a
registered tool and checking a refusal return value; if tools are truly unregistered in that
state, the test has to call the underlying function directly, bypassing MCP registration.
Worth a one-line clarification on which layer that refusal test targets.

---

## 3. Open questions — blocker vs nice-to-have

| # | Question | Classification | Why |
| --- | --- | --- | --- |
| 1 | Service account display name in CVP UI for `sid=019d...` | **Nice-to-have** | Cosmetic/operational curiosity; no tool behavior depends on it. |
| 2 | Correct keyed GET for `ConfigletConfig` | **Nice-to-have (currently orphaned)** | No tool in the Phase 1 or Phase 2 inventory table touches `ConfigletConfig`. This question has no consumer in the current spec scope — it only matters if configlet write is added later (Open Q #4, "if ever needed"). Recommend merging with #4 and marking both "deferred, no current tool depends on this." |
| 3 | Whether Phase 2 write tools should ship at all | **Blocker** | See §1.2 — this should gate the ~150-line Phase 2 design, not trail it as an afterthought. Must be resolved before any Phase 2 coding starts. |
| 4 | Configlet write path for AVD-generated configlets | **Nice-to-have**, explicitly "if ever needed" and out of current scope | Duplicate of the Phase 2 verify-table row on the same topic (§1.4). |
| 5 | Whether `get_cvp_designed_config` should diff running vs designed | **Should be reclassified: Important, not nice-to-have** | The spec's own motivating incident (line 9: "tracing a duplicated `logging host` statement on 720xp-24") is a running-vs-designed discrepancy. `get_cvp_designed_config` alone (provenance only, no running-config comparison) does not fully answer the motivating question — you'd still need to manually diff against `get_cvp_device_config`'s running-config output. Shipping Phase 1 without at least a documented manual-diff workflow undersells the "Why" section's own justification for building this. |

---

## 4. Tool inventory table vs body of the spec

Checked every row of the "Tool inventory (quick reference)" table (lines 378–396) against
its corresponding body section. Counts match (7 Phase 1 + 10 Phase 2 = 17, all present in
both places, no orphaned rows). Specific gaps found:

### 4.1 [Important] Table claims endpoints for two tools that were never in the access matrix

- `get_cvp_workspace` → table says `GET Workspace` (line 384). The access matrix only
  confirmed `Workspace/all` (200, line 99); a singular keyed `GET .../Workspace?key.workspaceId=`
  was never probed.
- `get_cvp_workspace_build` → table says `GET WorkspaceBuild` (line 385). This endpoint
  string does not appear anywhere else in the document outside the Phase 1 add-ons prose
  (line 190) and this table row. Zero probe evidence.

Both tools are needed to "poll build progress before any phase 2 submit" (line 191) — i.e.,
they gate the riskiest phase of the whole feature (submit/build), and neither has been
empirically verified the way every other row in the access matrix has. This is the single
biggest gap between "this spec is re-verified against live CVP" (line 3) and what's actually
been checked.

### 4.2 [Minor] `get_cvp_studio`'s endpoint is inferable but never stated in the body

Table says `GET StudioConfig` (line 381) for `get_cvp_studio`. The body section for
`get_cvp_studio` (lines 145–149) never names an endpoint at all — it only describes
behavior ("One studio by `studio_id`, including the Mako template body"). The endpoint
access matrix confirms `StudioConfig/all` (collection, 200) but not a singular keyed GET.
Same unverified-singular-GET gap as §4.1, one severity lower only because the body doesn't
actively claim it works — it's silent rather than contradictory.

### 4.3 [Minor] "defaulting to mainline" has no literal value anywhere in the spec

`get_cvp_studio`: "Accepts an optional `workspace_id`, defaulting to mainline." (line 148).
"Mainline" as a concept recurs 5 times in the doc (lines 148, 172, 235, 338, 351) but the
spec never states what literal `workspace_id` value represents "mainline" for the Resource
API (empty string? a reserved constant? omit the key entirely?). None of the endpoint-matrix
probes test this. Since the same ambiguity would affect `get_cvp_designed_config`'s implicit
default view and `assign_cvp_studio_tags`'s target-workspace semantics, this is worth
resolving once, explicitly, rather than leaving each tool section to guess independently.

### 4.4 [Confirmed not a problem] `data_source` naming for `get_cvp_designed_config`

Flagged for verification: the spec's `get_cvp_designed_config` response field
`data_source: service_api:compliancecheck.getconfig` (line 185) looks like it could be an
unprecedented naming convention next to the rest of the codebase's `resource_api:*` /
`connector:*` prefixes (`events.py`, `interfaces.py`, `routing.py`, `overlay.py`). Checked
against source: `cvp_mcp/grpc/config.py:414,421` already uses the exact string
`"service_api:compliancecheck.getconfig"` for the existing compliance-fallback path. The
spec is consistent with actual code here — not a finding, noted so the synthesis bucket
doesn't re-flag it.

---

## Findings index (severity)

| Severity | Section | One-line summary |
| --- | --- | --- |
| Critical | 1.2 | Phase 2 fully designed (150 lines) while "should we ship Phase 2 at all" is still an open question. |
| Important | 1.1 | Role-assignment framing contradicts the section's own "not a role/grant issue" conclusion — risks misdirected debugging. |
| Important | 2.1 | List tools (`get_cvp_studios`, `get_cvp_workspaces`) need a new all-rows NDJSON parser; existing helpers only return the first object. Testing guidance doesn't flag this as new code. |
| Important | 2.2 / 4.1 | `get_cvp_workspace` and `get_cvp_workspace_build` (needed to gate build/submit) were never probed in the access matrix, unlike every other endpoint in the spec. |
| Important | 2.4 | Test plan for `search_cvp_studio_templates` covers positive matches only, not the false-positive-avoidance behavior the tool is supposed to be trusted for. |
| Important | 3 (OQ#5) | "Diff running vs designed" is filed as nice-to-have but is actually central to the spec's own motivating incident. |
| Minor | 1.3, 1.4, 2.3, 2.5, 4.2, 4.3 | Dead-path cleanup, duplicate open-question tracking, a misnamed test reference, an unprecedented hash convention, and an unstated "mainline" workspace value. |

Confirmed non-issues (checked, no action needed): §2.6 write-test safety (`ws-mcp-test-*`,
no-submit) is sound; §4.4 `data_source` naming matches existing `config.py` convention.
