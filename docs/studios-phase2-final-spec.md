# Spec: Studios Phase 2 final — MSS root Inputs CAS, submit retirement, standing live verify

Status: **implemented on `feat/studios-phase2-final`** (2026-09-02; capture gate
passed, §D.4 corrected from the fixture;
findings F-C1–F-C4, F-I1–F-I8 applied — see
`docs/research/studios-phase2-final-adversarial-review.md`). Supersedes
`docs/studios-phase2-3-mss-root-inputs-spec.md` (its content is carried here with
corrections). Parent: `docs/studios-phase2-spec.md`. Sibling (shipped):
`docs/studios-phase2-followon-fix-spec.md`.

This is the last Phase 2 spec. It contains everything from Phase 2 that is not
finished and still stands, plus one decision that closes the rest:

| Item | Where from | State today |
| --- | --- | --- |
| **A.** Retire workspace submit | User decision 2026-09-02 | Library exists (`workspace_submit.py`), never registered |
| **B.** 2.0 live loop never run | `studios-phase2-session-2026-08-22.md` "Not done (by design): live CVaaS POST" | Only `create_cvp_workspace` has been exercised live (2026-09-02) |
| **C.** 2.1 §8 live verify never run | `studios-phase2-followon-fix-session-2026-08-22.md` "Still open" | Code merged (#16), unverified on tenant |
| **D.** 2.3 MSS Service root Inputs CAS | `studios-phase2-3-mss-root-inputs-spec.md` | Nothing exists; fixture not in repo |
| **E.** Client-side permission allowlist note | 2.3 draft §9 | Undocumented |

Not standing (closed, do not carry): tag-query → device-serial preview stays a
2.1 dry-run warning (`target_preview_unresolved`), not a blocker. Server-side
reject of mainline `workspaceId=""` on InputsConfig: client refuses; no probe.

## Decision: the MCP never submits

The human review point is the **workspace**, not the change control. The MCP
stops at `build_cvp_workspace`. The operator opens the workspace in the CVP UI,
reads the diff, and submits there. Change Control approve/execute stays where it
was: UI, human.

Consequences (this replaces the parent's "2.1 submit, unregistered until
staleness known"):

- `submit_cvp_workspace` is **retired**, not deferred. The helper must be unable
  to send `REQUEST_SUBMIT` at all, not merely gated.
- `CLOUDVISION_MCP_ALLOW_SUBMIT` and `SUBMIT_STALENESS_FIELD` are removed. A
  second env gate that can never be turned on is a trap for the next reader.
- The parent's open items "Full `Workspace.Request` enum" and "Workspace
  `last_modified_at`" close: the helper's request allowlist is
  `{REQUEST_START_BUILD}` and nothing else is needed.

## Canonical names (additions and removals vs parent)

| Concept | Canonical |
| --- | --- |
| Request allowlist | `ALLOWED_REQUESTS = {REQUEST_START_BUILD}` — **`REQUEST_SUBMIT` removed** |
| Submit env / staleness | **removed** (`SUBMIT_ENV`, `SUBMIT_STALENESS_FIELD`, `submit_enabled`, `_submit_allowed`) |
| 2.3 tool | `set_cvp_mss_policy_inputs` |
| MSS studio (fixed) | `studio-mss-service` |
| 2.3 module | `cvp_mcp/grpc/studio_mss_inputs.py` |
| Digest module | `cvp_mcp/grpc/inputs_digest.py` — `inputs_sha256(document) -> str` (no `cvp_mcp.grpc` imports; both `studios.py` and `studio_mss_inputs.py` import it, which avoids a `studios.py` → `studios_write.py` cycle) |
| Digest | SHA-256 hex of `json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)` over the **parsed** document |
| CAS parameter | `expected_inputs_sha256: str` (required; 64 lowercase hex) |
| Root loader | `studios_write._load_root_inputs(datadict, workspace_id, studio_id=ACCESS_INTERFACE_STUDIO_ID)` — optional parameter, existing callers unchanged |
| Preview token | existing `write_access.preview_token` / `check_preview_token`; mismatch code is the existing **`preview_required`** (the 2.3 draft's `preview_token_required` / `preview_token_mismatch` do not exist and are not added) |
| Fixture | `tests/fixtures/inputs_mss_service_root_2026-09-02.json` |
| Tests | `tests/test_studio_mss_inputs.py`, `tests/test_inputs_digest.py` |

Everything else in the parent's canonical table stands.

## A. Submit retirement

### Code

| File | Change |
| --- | --- |
| `cvp_mcp/grpc/workspace_submit.py` | **delete** |
| `tests/test_workspace_submit.py` | **delete** (33 tests) |
| `cvp_mcp/write_access.py` | remove `SUBMIT_ENV`, `SUBMIT_STALENESS_FIELD`, `submit_enabled()`. Keep `writes_enabled`, `preview_token`, `check_preview_token`, `validate_workspace_id`. |
| `cvp_mcp/grpc/resource_write.py` | `ALLOWED_REQUESTS = frozenset({REQUEST_START_BUILD})`; delete `REQUEST_SUBMIT` constant and `_submit_allowed()`. `REQUEST_SUBMIT` on a body is then rejected by the existing "any other string" rule in `_check_request_field` → **`request_not_allowed`**, before any HTTP. |
| `tests/test_resource_write.py` | replace the "submit gate" block with: `REQUEST_SUBMIT` → `request_not_allowed`, no request built. Add `REQUEST_SUBMIT` to the parametrised reject list at line 137. |
| `tests/test_write_access.py` | drop the three `submit_enabled` tests. |
| `cvp_mcp/grpc/studio_crud.py` | docstrings at lines 25 and 494: "review and submit" → "review in the CVP UI and submit there". No behaviour change. |
| `cloudvision_mcp.py` | line 1504 docstring: "build/submit polling" → "build polling". Tool docstrings that say "Does not submit" stay (still true). |

No other module imports from `workspace_submit`. `grep -rn "REQUEST_SUBMIT\|submit_enabled\|SUBMIT_STALENESS_FIELD\|ALLOW_SUBMIT" cvp_mcp tests cloudvision_mcp.py` must return nothing after this bucket.

### Docs (parent `docs/studios-phase2-spec.md`, replace named text, do not append)

1. Status line: drop "2.1 submit remains gated"; add "Submit retired 2026-09-02 (`docs/studios-phase2-final-spec.md`)."
2. Canonical table: delete rows `Submit env`, `Submit request enum`.
3. Slices table: 2.1 row → `assign_cvp_studio_tags`; generic `set_cvp_studio_inputs` (shipped). Add row **2.3** | `set_cvp_mss_policy_inputs` | this spec. Add row **Submit** | retired | never.
4. Goals #3: "Submit is a second opt-in and never approves/executes a CC." → "The MCP never submits. The human reviews the workspace diff in the CVP UI and submits there."
5. HTTP helper `request` allowlist bullet 2: `{REQUEST_START_BUILD}` only; `REQUEST_SUBMIT` is rejected like any other string.
6. Process/env gates table: delete the `CLOUDVISION_MCP_ALLOW_SUBMIT` and `SUBMIT_STALENESS_FIELD` rows. Delete the "Helper: `REQUEST_SUBMIT` is allowed only when…" paragraph.
7. Canonical workflow block: delete the `submit_cvp_workspace` line; the 2.1 block ends at "human reviews the workspace in the CVP UI and submits there — never MCP".
8. "Submit updates **mainline designed config** even with no device CC executed…" paragraph: keep the tenant fact, prefix "Human submit:".
9. Tool reference `submit_cvp_workspace` section → one line: "Retired 2026-09-02. See `docs/studios-phase2-final-spec.md` §A."
10. Caller inputs table: delete the `Submit` row.
11. Testing bullets that mention submit (two) → one bullet: "`REQUEST_SUBMIT` on a WorkspaceConfig body → `request_not_allowed`, no request built."
12. Open table: rows `Full Workspace.Request protobuf enum` and `Workspace last_modified_at` → **Closed 2026-09-02: submit retired.** Row `InputsConfig POST of a patched root tree` → still open, see final spec §B.
13. Inventory: `submit_cvp_workspace` → retired; add `set_cvp_mss_policy_inputs | 2.3`.
14. `docs/studios-support-spec.md` Phase 2 paragraph: "Submit needs a second env gate." → "The MCP never submits; the human submits the reviewed workspace in the CVP UI."

## B. 2.0 live loop (standing since 2026-08-22)

Never run on the tenant: `set_cvp_access_interface_description` POST, `build_cvp_workspace` POST, `delete_cvp_workspace` DELETE. Only `create_cvp_workspace` has succeeded live. This is the parent's first Open row and it blocks trusting the root-POST shape that 2.3 reuses.

Run (writes on, coordinator, not farmed), in this order, before 2.3 is enabled:

1. The operator names one port whose description may change for the test
   (2.0 CAS has no no-op path, and the intended labels already match mainline).
   Read its current description with `get_cvp_studio_inputs`. Record the port.
2. `create_cvp_workspace ws-mcp-test-desc-<date>-<uuid8>` → `accepted`.
3. Description CAS preview with `expected_current_description=<current>` and
   `new_description="<current> (mcp-test)"` → confirm → `accepted`; re-read
   Inputs with the draft id → overlay row present with exactly that one changed
   leaf; mainline unchanged. The workspace is deleted in step 6, so no restore
   write is needed.
4. `build_cvp_workspace` preview → confirm; poll `get_cvp_workspace` / `get_cvp_workspace_build` → `BUILD_STATE_SUCCESS`.
5. Open the workspace in the CVP UI and confirm the diff is one description line. **Do not submit.**
6. `delete_cvp_workspace` → `accepted`.

Record the outcome in `docs/research/studios-phase2-live-verify-<date>.md` and flip the parent's Open row to closed.

## C. 2.1 §8 live verify (standing since 2026-08-22)

Same session as §B, writes on:

1. `get_cvp_studio_assigned_tags(studio-campus-access-interfaces)` → `query=""`, `coverage="full"` (mainline, no row).
2. `get_cvp_studio_assigned_tags(studio-mss-service)` → the live query `T3:X3 AND Campus:campus-1709 OR monitor-device:true`, `coverage="full"`.
3. In the §B draft: `assign_cvp_studio_tags` **preview only** with `expected_current_query=""` on Access Interfaces and a throwaway `query="device:JPE19151499"` → `outcome: preview`, no POST. Do not confirm.
4. `set_cvp_studio_inputs(studio-mss-service, <draft>, ["rules"], {...})` → `inputs_path_not_found`, `available_path_values: [[]]`, `details.hint` naming the description CAS. (Already observed 2026-09-02, MCP image 1.60 — re-run to record it under the final spec.)
5. Overlay studio GET: `create_cvp_studio` a throwaway `studio-mcp-test-*` in the draft, then `set_cvp_studio_inputs` against it with a nested path → must fail on path lookup, **not** on a mainline 404 of the studio. Delete the studio in the same draft.
6. Delete the draft.

## D. `set_cvp_mss_policy_inputs` (2.3)

### Why

MSS Service (`studio-mss-service`) stores its whole input tree as **one** Inputs
Resource row at `path.values []`. 2.1 generic Inputs refuses `[]`
(`root_path_forbidden`), cannot resolve nested keys as Resource paths, and is
description-only. Adding a group or a `drop` rule changes leaves named `name`,
`members`, `action`, `services` → `input_key_not_allowed`. That refusal is
correct for a generic tool. The 2026-09-02 rogue-DHCP incident (TRENDnet TPI-06
at `10.0.3.4` ACKing leases; policy entered by hand through a CC) is the operator
job this tool exists for.

The sanctioned pattern for a root row is 2.0 description CAS: read root, change a
bounded set of leaves, verify the diff, CAS, POST root keyed to the draft. 2.3 is
that shape with a bounded **operation** vocabulary instead of a free document and
a **digest** CAS instead of a single-leaf CAS.

### Non-goals

- A generic root POST or `replace_all_inputs`. Still forbidden.
- Loosening 2.1 `set_cvp_studio_inputs` (allowlist, forbidden tokens, root refusal).
- Any MSS key outside the four collections in §D.3: `securityDomains`,
  `monitorObjects`, `redirectObjects`, `acceptedGroups`, `ignoredGroups`,
  `acceptedSensors`, `sslProfileName`, any `hidden*Mapper`,
  `staticExceptionList`, `enableStaticExceptionList`.
- The studio's tag query (2.1 `assign_cvp_studio_tags`).
- Segment Security studio (`studio-segmentation`, MSS-G). Different schema.
- ChangeControlConfig, approve, execute, submit.

### D.0 Capture gate (blocks M)

`tests/fixtures/inputs_mss_service_root_2026-09-02.json` does not exist in the
repo. Capture it first: `get_cvp_studio_inputs("studio-mss-service")` on the
MCP host, save `items[0]` verbatim (key + `path_values` + parsed `inputs`).

Capture the **post-change** mainline — the document as the CVP UI left it after
the two 2026-09-02 change controls. The shape quoted in the superseded 2.3 draft
(2 rules, `POL1 = ["monitor"]`) was read **before** the 13:53 CDT CC; a fresh
capture will contain `pdu4-trendnet`, the three DHCP/DNS services, the three
`drop-*` rules and the new `POL1` order. That is deliberate: it is the ground
truth for what a correct edit looks like on the wire (F-I5). Contains
private-range IPs and group names only; no secrets; redact nothing.

The entry schemas in §D.4 are **derived from that fixture at implementation
time**. Where the fixture contradicts §D.4 (field name, type of `direction`,
spelling of `<any>`, `packet` values, port-string grammar), the fixture wins and
§D.4 is corrected in the same PR. Do not code a constraint the fixture does not
show at least one instance of.

**Stop condition (F-I6):** search the fixture for `pdu4` (or any of the three
new rule/service names) under any `hidden*Mapper` key. If found, the UI
maintains input-side derived state outside the four collections when a group,
service or rule is added; 2.3 as written cannot reproduce that and **must not be
implemented** until the mapper contract is understood. Record the finding and
re-spec. If not found, proceed.

**Outcome (2026-09-02 capture):** passed. `hiddenIntersectedGroupsMapper` is `[]`,
`hiddenPolicyIdMapper` still only `POL1 → 256`, device/VRF mappers unchanged. The UI
wrote nothing outside the four collections. Fixture committed; pinned by
`test_fixture_hidden_mappers_do_not_reference_the_change`.

Corrections the fixture forced on §D.4 (fixture wins): `monitorName` is stored on
**drop** rules too (`ztx-7230` on all three); rules carry **no `description`**
key; new service configurations **omit `icmpTypes`** (old ones have `"all"`);
the DNS service is `dns-server-port` with three single-port configurations, not a
comma list; `direction` is a JSON boolean; `packet` is `"any"`.

### D.1 Read side (`get_cvp_studio_inputs`)

Add `inputs_sha256` to every item, computed with `inputs_digest.inputs_sha256`
over the parsed `inputs` value. `inputs_sha256(document)` is defined only for
`dict` / `list`; for anything else (raw string after a failed JSON parse, `None`)
the item carries `inputs_sha256: null` — never a digest that the write side
could not reproduce (F-I1). The write side refuses that row with
`inputs_path_unresolved`. Both sides call the one function. Non-breaking. Key
order and wire whitespace do not affect it.

### D.2 Parameters

| Parameter | Type | Rule |
| --- | --- | --- |
| `workspace_id` | `str` | `validate_workspace_id` (`ws-mcp-*`, not `builtin-`) |
| `expected_inputs_sha256` | `str` | required; 64 lowercase hex; else `expected_inputs_sha256_required`. `""`, `None`, non-str → same code. |
| `operations` | `list[dict]` | §D.3; 1–20 entries |
| `confirm` | `bool = False` | |
| `preview_token` | `str \| None = None` | required on `confirm=True` |

Studio is fixed to `studio-mss-service`; there is no `studio_id` parameter.

### D.3 Operation vocabulary

Applied in order to a deep copy of the current root document.

| `op` | Fields | Semantics |
| --- | --- | --- |
| `upsert` | `collection`, `entry` (object with `name`) | Replace the entry whose `name` matches, else append. Validated per §D.4. On `policies`: **existing name only** (new → `mss_entry_not_found`). |
| `remove` | `collection`, `name` | Remove the entry whose `name` matches. Missing → `mss_entry_not_found`. **Not allowed on `policies`** → `mss_operation_invalid`. |
| `set_policy_rules` | `policy`, `policy_rules` (non-empty list of rule names) | Replace `policies[name==policy].policyRules`. Missing policy → `mss_entry_not_found`. Empty list → `mss_operation_invalid`. |

`collection` ∈ `staticGroups`, `services`, `rules`, `policies`; anything else →
`mss_collection_not_allowed`. Unknown `op` or missing fields →
`mss_operation_invalid` with the offending path (`operations[3].entry.foo`).

Policies are constrained (F-C3) because `hiddenPolicyIdMapper` (`POL1 → 256`
live) is outside the writable scope: a new policy would have no id, a removed one
would leave a dangling mapper entry, and an empty rule list empties enforcement.
Policy creation is a separate spec if ever wanted.

Empty `operations` → `mss_operations_required`. More than 20 →
`mss_operations_too_many`.

Entries match by `name` only, case-sensitive. Empty/whitespace-only names, or a
name equal to `<any>` after trim (case-insensitive) → `mss_operation_invalid`
(F-C4). Two ops in one call that target the same `name` in the same collection
are allowed (last wins); the result is validated once.

### D.4 Entry validation (before diff, before any HTTP)

Only these keys are accepted per collection. Extra key → `mss_operation_invalid`
with path. Types are checked. **Subject to the D.0 fixture.**

| Collection | Accepted keys | Constraints |
| --- | --- | --- |
| `staticGroups` | `name`, `membership.members` | `members`: non-empty list of IPv4/IPv6 CIDR strings (`ipaddress.ip_network(strict=False)`); **`prefixlen == 0` refused** (`0.0.0.0/0`, `::/0` are `<any>` under another name — F-C2). `name` must not collide with any `acceptedGroups[].name` (F-C4). No `staticExceptionList` / `enableStaticExceptionList`. |
| `services` | `name`, `protocols`, `configurations[]` with `protocol`, `sourceports`, `destinationports`, optional `icmpTypes` | `protocols` ∈ `TCP/UDP`, `ICMP`. `protocol` ∈ `tcp`, `udp`, `icmp`. Ports: `all`, single `1–65535`, `a-b` range, or comma list of those. `icmpTypes` (optional): `all` or comma list of ints 0–255. `configurations` non-empty. |
| `rules` | `name`, `action`, `sources`, `destinations`, `services`, `packet`, `direction`, optional `monitorName` | No `description` (the wire has none). `action` ∈ `forward`, `drop`. `sources`/`destinations`: non-empty lists of group names or `<any>`. `services`: non-empty list of service names or `<any>`. `packet` = `any`. `direction`: bool. `monitorName` allowed on any action, must name an existing `monitorObjects[].name`. |
| `policies` | `name`, `description`, `policyRules` | `policyRules`: list of rule names, unique. |

**Referential integrity** on the **result** document after all ops: every group
name in a rule exists in `staticGroups` or `acceptedGroups[].name` (AGNI groups
are read-only here but referenceable); every service name exists in `services`;
every rule name in any `policyRules` exists in `rules`; a `remove` that leaves a
dangling reference → `mss_reference_unresolved` naming the referrer.

**Blast-radius refusal (new vs 2.3 draft):** a rule with `action: drop` and
`sources == ["<any>"]` and `destinations == ["<any>"]` and `services == ["<any>"]`
→ `mss_rule_too_broad`, no preview. That rule drops the fabric at physical
ingress on every MSS-enforcing switch. There is no `allow_broad` flag, matching
the parent's "no `allow_disruptive`" rule.

**Warnings (not refusals):**

- `mss_rule_broad:<rule>` — `action: drop` with both `sources` and `destinations` `<any>` (services narrowed).
- `mss_rule_shadowed:<policy>:<rule>` — a policy places a `forward` rule with all three `<any>` before any `drop` rule (the `monitor` rule on this tenant; operator wants drops ahead of it).
- `inputs_unchanged` — zero changed leaves after all ops; preview still returned.

### D.5 Write shape (five steps, same as 2.0)

1. Gates, no HTTP: `writes_enabled()`, `validate_workspace_id`,
   `expected_inputs_sha256` well-formed, operations validated (§D.3 structure only
   — §D.4 needs the document).
2. Workspace GET (`_read_workspace`): exists and `WORKSPACE_STATE_PENDING`. Studio GET
   via `_read_studio_anywhere` (overlay then mainline, 404-only fallthrough):
   not `immutable`, not `from_package`; `read_failed` or both-missing →
   `preflight_failed`. **Normative order and codes: copy the preflight block of
   `studio_inputs_generic.set_cvp_studio_inputs`** (writes → id → structural →
   workspace GET → `workspace_not_pending` / `workspace_state_unknown` → studio
   overlay → flags). Do not re-derive it (F-I4).
3. Root read: `_load_root_inputs(datadict, workspace_id, studio_id="studio-mss-service")`.
   Draft overlay preferred, else mainline. Truncation / skipped NDJSON →
   `preflight_failed` (already the loader's behaviour). Document not a dict →
   `inputs_path_unresolved`. `before_sha256 = inputs_sha256(document)`. Mismatch
   with `expected_inputs_sha256` → `inputs_digest_mismatch` with
   `details.current_inputs_sha256` and `details.inputs_source_workspace_id`; no POST.
4. Apply ops to a deep copy. Validate result (§D.4, referential, blast radius).
   Diff with `_changed_leaf_paths(before, after)`. **Scope check:** every path
   must satisfy `path == "$.<c>"` or `path.startswith("$.<c>[")` for
   `c ∈ {staticGroups, services, rules, policies}`; anything else →
   `tree_diff_outside_mss_scope` (defence in depth against a buggy applier).
   Note `_changed_leaf_paths` reports a **list length change as one path at the
   list** (`$.rules`), and an in-place replacement as nested paths
   (`$.rules[1].action`); both forms are in scope. **EOS lint runs on the
   caller-supplied `operations`** — `_disruptive_hits(json.dumps(operations,
   sort_keys=True))` — not on the after-document: the ops are the only source of
   new text, and `_disruptive_hits` returns pattern names, so a before/after
   subtraction would mask a new `shutdown` whenever any pre-existing string
   already matched (F-C1). Any hit → `disruptive_content_forbidden`. Zero
   changed leaves → warning `inputs_unchanged`, proceed.
5. `confirm=False` → `outcome: preview`, `preview_token = preview_token(TOOL_NAME, {studio_id, workspace_id, expected_inputs_sha256, operations, after_sha256})`.
   `confirm=True` → `check_preview_token` over the **same** dict recomputed from
   this call (so `after_sha256` is recomputed from this call's read + ops);
   mismatch/missing → `preview_required`, no HTTP. Match → **one** POST:

```json
{
  "key": {"studioId": "studio-mss-service", "workspaceId": "<ws-mcp-…>", "path": {"values": []}},
  "inputs": "<json.dumps(after_document)>"
}
```

`inputs` is dumped **without** `sort_keys` (preserve the document's key order on
the wire; the digest is the only place keys are sorted). Mainline `""` is never
written. The first write on a fresh draft is what creates the overlay row; later
writes read it.

Binding `after_sha256` into the token means a confirm cannot post a different
document than the one previewed, even if the caller replays the token with
edited operations or the mainline changed between preview and confirm (the
digest CAS also catches that).

### D.6 Envelope

`tool_envelope(..., obj=...)`; `object` fields on preview and accepted:

| Field | Value |
| --- | --- |
| `outcome` | `preview` / `accepted` / `refused` |
| `dry_run` | `true` on preview |
| `operation` | `set_mss_policy_inputs` |
| `studio_id` | `studio-mss-service` |
| `workspace_id` | draft id |
| `inputs_source_workspace_id` | `""` or the draft |
| `before_sha256`, `after_sha256` | digests |
| `operations_applied` | count; `operations` echo of validated ops |
| `entries_added`, `entries_replaced`, `entries_removed` | lists of `<collection>:<name>` — the human-readable summary, since an append shows up in `changed_leaf_paths` only as `$.rules` (F-I8) |
| `changed_leaves` | true count; `changed_leaf_paths` capped at 10 |
| `posted_at_root` | `true` |
| `disruptive` | `false` (always; a `true` case is a refusal) |
| `request_body` | the POST body (preview shows it; accepted echoes it) |
| `resource_time` | from the POST response; `None` on preview |
| `preview_token` | preview only |
| `next_action` | preview: `Re-call with confirm=True and this preview_token.` accepted: `build_cvp_workspace, then review the workspace diff in the CVP UI.` |

Refusals reuse `studios_write._refused` unchanged (`next_action: None`; hints in
`details.hint` / `error.message`). Codes (new in bold): `writes_disabled`,
`invalid_workspace_id`, `workspace_id_required`, `builtin_workspace_forbidden`,
`workspace_not_found`, `workspace_read_failed`, `workspace_not_pending`,
`workspace_state_unknown`, `studio_immutable`,
`studio_from_package`, `preflight_failed`, `inputs_path_unresolved`,
**`expected_inputs_sha256_required`**, **`inputs_digest_mismatch`**,
**`mss_operations_required`**, **`mss_operations_too_many`**,
**`mss_operation_invalid`**, **`mss_collection_not_allowed`**,
**`mss_entry_not_found`**, **`mss_reference_unresolved`**,
**`mss_rule_too_broad`**, **`tree_diff_outside_mss_scope`**,
`disruptive_content_forbidden`, `preview_required`, `resource_write_failed`.

Audit INFO per parent: tool, `workspace_id`, `studio_id`, outcome,
`before_sha256`, `after_sha256`, `changed_leaves`. Never the document.

### D.7 Worked example (the 2026-09-02 change)

Operator intent: drop UDP/67 both ways and UDP/TCP 53 + UDP 5353 **to** the PDU,
ahead of the forward-all `monitor` rule in `POL1`.

The JSON below is illustrative (transcribed before the capture). The canonical
form is `_worked_example_ops()` in `tests/test_studio_mss_inputs.py`, built from
the fixture entries verbatim: no rule `description`, `monitorName: "ztx-7230"` on
every rule, no `icmpTypes`, and the DNS service named `dns-server-port` with three
single-port configurations. `test_worked_example_reproduces_post_change_fixture`
proves those ops turn the pre-change document into the fixture, digest-equal.

```
get_cvp_studio_inputs(studio_id="studio-mss-service")  -> items[0].inputs_sha256 = "<digest>"
```

```json
{
  "workspace_id": "ws-mcp-mss-block-pdu4-dhcp",
  "expected_inputs_sha256": "<digest>",
  "operations": [
    {"op": "upsert", "collection": "staticGroups", "entry": {"name": "pdu4-trendnet", "membership": {"members": ["10.0.3.4/32"]}}},
    {"op": "upsert", "collection": "services", "entry": {"name": "dhcp-server-replies", "protocols": "TCP/UDP", "configurations": [{"protocol": "udp", "sourceports": "67", "destinationports": "all", "icmpTypes": "all"}]}},
    {"op": "upsert", "collection": "services", "entry": {"name": "dhcp-server-port", "protocols": "TCP/UDP", "configurations": [{"protocol": "udp", "sourceports": "all", "destinationports": "67", "icmpTypes": "all"}]}},
    {"op": "upsert", "collection": "services", "entry": {"name": "dns-mdns-server-port", "protocols": "TCP/UDP", "configurations": [{"protocol": "udp", "sourceports": "all", "destinationports": "53,5353", "icmpTypes": "all"}, {"protocol": "tcp", "sourceports": "all", "destinationports": "53", "icmpTypes": "all"}]}},
    {"op": "upsert", "collection": "rules", "entry": {"name": "drop-dhcp-from-pdu4", "description": "TPI-06 leftover dnsmasq: drop OFFER/ACK/NAK", "action": "drop", "sources": ["pdu4-trendnet"], "destinations": ["<any>"], "services": ["dhcp-server-replies"], "packet": "any", "direction": true}},
    {"op": "upsert", "collection": "rules", "entry": {"name": "drop-dhcp-to-pdu4", "description": "drop unicast renewals to the PDU", "action": "drop", "sources": ["<any>"], "destinations": ["pdu4-trendnet"], "services": ["dhcp-server-port"], "packet": "any", "direction": true}},
    {"op": "upsert", "collection": "rules", "entry": {"name": "drop-dns-to-pdu4", "description": "nobody resolves through the PDU", "action": "drop", "sources": ["<any>"], "destinations": ["pdu4-trendnet"], "services": ["dns-mdns-server-port"], "packet": "any", "direction": true}},
    {"op": "set_policy_rules", "policy": "POL1", "policy_rules": ["drop-dhcp-from-pdu4", "drop-dhcp-to-pdu4", "drop-dns-to-pdu4", "monitor"]}
  ]
}
```

Expected preview: `changed_leaves: 4`, `changed_leaf_paths` exactly
`["$.policies[0].policyRules", "$.rules", "$.services", "$.staticGroups"]`
(appends are list-length changes), 8 ops applied, no warnings, full
`request_body`. Confirm posts once. Then `build_cvp_workspace`; the human reviews
the generated `traffic-policy` in the CVP workspace and submits there.

If `monitor` were listed first, preview carries `mss_rule_shadowed:POL1:monitor`
and still allows confirm; shadowing is a policy choice.

### D.8 Tests (no live CVaaS)

`tests/test_inputs_digest.py`:

- Same document, shuffled key order, different whitespace on the wire → same digest.
- `inputs_sha256` of a `str` / `None` / `int` → `None` (read side emits `inputs_sha256: null`).

`tests/test_studio_mss_inputs.py` against the D.0 fixture. The fixture is
**post-change**; tests that need a "before" document derive `PRE` in a helper by
removing `pdu4-trendnet`, the three services and three rules, and restoring
`POL1.policyRules = ["monitor"]`.

- **Round-trip (the important one, F-I5/F-I6):** apply the D.7 ops to `PRE` →
  `after_sha256 == inputs_sha256(fixture)`. The vocabulary must reproduce
  exactly what the UI wrote, hidden mappers included. If this fails on the real
  fixture, the D.0 stop condition applies.
- `expected_inputs_sha256` omitted / `""` / `None` / 63 chars / uppercase hex → `expected_inputs_sha256_required`, no HTTP.
- Digest mismatch → `inputs_digest_mismatch`, `details.current_inputs_sha256`, no POST.
- Overlay row present → digest and diff use the overlay; `inputs_source_workspace_id` is the draft. Absent → mainline, `""`.
- Truncated `/all` or skipped NDJSON line → `preflight_failed`, no POST.
- Studio GET: overlay 200 used; overlay 404 then mainline 200; overlay non-404 `read_failed` → `preflight_failed`, no fallthrough. `immutable` / `from_package` refuse.
- Workspace not pending / unknown state / missing → refuse, no POST.
- Each of `staticGroups`, `services`, `rules`: `upsert` new (list-length path), `upsert` replace by `name` (nested paths), `remove`; unknown key → `mss_operation_invalid` with path; bad CIDR, bad port string, bad `action`, `monitorName` on `drop`, unknown `monitorName` → `mss_operation_invalid`.
- `staticGroups` member `0.0.0.0/0` or `::/0` → `mss_operation_invalid`. `staticGroups` name equal to an `acceptedGroups[].name` → `mss_operation_invalid`. Any entry named `<any>` / ` <ANY> ` → `mss_operation_invalid`.
- `policies`: `upsert` existing (description / policyRules) allowed; `upsert` new name → `mss_entry_not_found`; `remove` → `mss_operation_invalid`; `set_policy_rules` with `[]` → `mss_operation_invalid`.
- `remove` of a group referenced by a rule → `mss_reference_unresolved` naming the rule. `remove` of a rule still in `POL1.policyRules` → `mss_reference_unresolved` naming the policy.
- `set_policy_rules` unknown rule → `mss_reference_unresolved`; duplicate → `mss_operation_invalid`; unknown policy → `mss_entry_not_found`.
- Reference to an `acceptedGroups[].name` allowed.
- `drop` with `<any>`/`<any>`/`<any>` → `mss_rule_too_broad`, no preview. `upsert` of `monitor` with `action: drop` → same. `drop` with `<any>`/`<any>`/named service → warning `mss_rule_broad`, preview.
- Forward-all before a drop → `mss_rule_shadowed:<policy>:<rule>`, outcome preview.
- Ops that change nothing (re-upsert of an existing entry byte-identical) → `inputs_unchanged`, preview.
- Injected op that mutates `securityDomains` (monkeypatched applier) → `tree_diff_outside_mss_scope`.
- `shutdown` in a new rule description → `disruptive_content_forbidden`, no HTTP. A fixture doctored so an **untouched** AGNI group name contains `shutdown` → still refused when the ops contain it, still allowed when they do not (lint is on ops, not on the document).
- Envelope carries `entries_added` / `entries_replaced` / `entries_removed` as `<collection>:<name>`.
- Preview token replay with altered operations → `preview_required`. Same ops after mainline changed between preview and confirm → `inputs_digest_mismatch` (digest check runs first).
- Confirm path: exactly one POST; `key.path.values == []`; `inputs` is a JSON string; `workspaceId` is the draft, never `""`; body key order preserved.
- Writes env off → `writes_disabled` before HTTP; tool absent from registration.
- Worked example end to end from `PRE`: 4 changed paths as listed, 8 ops, no warnings, `entries_added` has 7 entries and `entries_replaced == ["policies:POL1"]`.
- `operations` of 21 → `mss_operations_too_many`; `[]` → `mss_operations_required`.

`tests/test_studios_write.py`: `_load_root_inputs` default studio unchanged;
`studio_id="studio-mss-service"` filters that studio. `tests/test_studios.py`:
`inputs_sha256` present on items.

Existing 2.0 / 2.1 / 2.2 tests unchanged except the submit deletions in §A.

### D.9 Live verify (after code; writes on)

1. `get_cvp_studio_inputs(studio-mss-service)` → item carries `inputs_sha256`.
2. `create_cvp_workspace ws-mcp-test-mss-<date>-<uuid8>`.
3. D.7 preview with a stale digest → `inputs_digest_mismatch`. Right digest → preview, `changed_leaves: 4`, `request_body.key.path.values == []`.
4. Confirm → `accepted`; re-read with the draft id → overlay row with the new entries; mainline unchanged (same digest as step 1).
5. `build_cvp_workspace` → `BUILD_STATE_SUCCESS`; open the workspace in CVP and read the generated `traffic-policy`. Since the PDU policy already exists on mainline (entered by hand on 2026-09-02), the live D.7 run is expected to preview `inputs_unchanged` (ops re-upsert identical entries). To exercise a real diff, use a throwaway group `mcp-test-group` with one `/32` and a single `drop` rule not added to any policy; expect exactly `$.staticGroups` and `$.rules` changed and a build that succeeds. Do not submit.
6. `delete_cvp_workspace`. Never submit from the MCP.

## E. Client-side permission note (README)

Claude Code in auto mode has a permission classifier that can deny MCP write
calls without a prompt. Add to README under the MCP client section:

> Allowlist the non-submit write tools in `permissions.allow`:
> `mcp__cloudvision-mcp__create_cvp_workspace`, `delete_cvp_workspace`,
> `build_cvp_workspace`, `set_cvp_access_interface_description`,
> `set_cvp_studio_inputs`, `assign_cvp_studio_tags`, `create_cvp_studio`,
> `delete_cvp_studio`, `set_cvp_mss_policy_inputs`. Every one of these is
> dry-run unless `confirm=True` with a matching `preview_token`; none can submit.

README currently has no tool list; add one section for the write tools with the
env gate and the dry-run rule.

## Files

| File | Bucket | Change |
| --- | --- | --- |
| `cvp_mcp/grpc/workspace_submit.py`, `tests/test_workspace_submit.py` | S | delete |
| `cvp_mcp/write_access.py`, `tests/test_write_access.py` | S | drop submit gate |
| `cvp_mcp/grpc/resource_write.py`, `tests/test_resource_write.py` | S | `ALLOWED_REQUESTS = {REQUEST_START_BUILD}` |
| `cvp_mcp/grpc/studio_crud.py`, `cloudvision_mcp.py` (docstrings only) | S | wording |
| `cvp_mcp/grpc/inputs_digest.py`, `tests/test_inputs_digest.py` | R | new |
| `cvp_mcp/grpc/studios.py`, `tests/test_studios.py` | R | `inputs_sha256` on items |
| `cvp_mcp/grpc/studios_write.py`, `tests/test_studios_write.py` | R | `_load_root_inputs(studio_id=)` |
| `tests/fixtures/inputs_mss_service_root_2026-09-02.json` | 0 | live capture |
| `cvp_mcp/grpc/studio_mss_inputs.py`, `tests/test_studio_mss_inputs.py` | M | new |
| `cloudvision_mcp.py` (registration), `README.md` | M | register behind `writes_enabled()` + `tool_enabled`; README §E |
| `docs/studios-phase2-spec.md`, `docs/studios-support-spec.md` | D | §A replacements + 2.3 rows |

Do not edit `studio_inputs_generic.py`, `studio_tags.py`, or 2.0 description
CAS behaviour.

## Farm later

Do not farm until this spec passes adversarial review and is approved.

| Wave | ID | Own | Depends |
| --- | --- | --- | --- |
| 0 | 0 | fixture capture (coordinator, live read) | — |
| 1 | S | submit retirement (§A code) | — |
| 1 | R | digest module, `inputs_sha256` on read, `_load_root_inputs(studio_id=)` | — |
| 1 | D | parent + support spec replacements | — |
| 2 | M | `studio_mss_inputs.py` + tests + registration + README | 0, R |

§B, §C, §D.9 live verifies are coordinator work on the deployed image, in that
order, after wave 2 merges. §B and §C can run before wave 2 on the current image.
