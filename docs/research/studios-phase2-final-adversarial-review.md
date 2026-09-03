# Phase 2 final spec — adversarial review (2026-09-02)

Target: `docs/studios-phase2-final-spec.md` (draft of the same day). Reviewer:
coordinator, inline, four lanes (safety, wire contract, consistency with shipped
code, implementability). Every claim below was checked against the code on
`main` @ `79f95f7` (532 tests passing), not against the specs.

**Verdict: revise, then approve.** The shape is right — bounded op vocabulary,
digest CAS, diff-scope guard, submit physically removed from the helper. Four
holes would still let an implementer ship a tool that (1) misses a newly
introduced `shutdown`, (2) drops the fabric through a `/0` group, (3) corrupts
the studio's hidden policy-id mapping, or (4) creates ambiguous group names.
All four are cheap to close and are **applied in the spec** below.

## Critical (applied)

| ID | Finding | Spec fix |
| --- | --- | --- |
| F-C1 | §D.5 step 4 lints the after-document and subtracts hits "not present in the before document". `_disruptive_hits` (`studios_write.py:380`) returns pattern **names**, not positions. If any string in the 32 AGNI group names, monitor objects or existing descriptions already matches `shutdown`, a new rule description containing `shutdown` is masked. | Lint the **caller-supplied `operations`** (canonical JSON of the validated ops) — the only source of new text — and rely on the scope check for everything else. Pre-existing text in untouched leaves is irrelevant by construction. |
| F-C2 | `mss_rule_too_broad` keys on the literal `<any>`. A static group with `members: ["0.0.0.0/0"]` (or `::/0`) is `<any>` with a different name; `ip_network(strict=False)` accepts it. `drop group→group/<any>` then drops the fabric. | Refuse any member with `prefixlen == 0` → `mss_operation_invalid` ("use `<any>`"). Broadness checks then hold. |
| F-C3 | `policies` is in the four writable collections, but `hiddenPolicyIdMapper` (`POL1 → 256`, observed live) is out of scope. `upsert` of a **new** policy has no id; `remove` of a policy leaves a dangling mapper entry; `set_policy_rules` with `[]` empties enforcement. The studio's behaviour on any of those is unknown. | `policies`: `remove` refused (`mss_operation_invalid`); `upsert` only on an **existing** name (new → `mss_entry_not_found`); `policy_rules` must be non-empty. Policy creation is a later spec if ever needed. |
| F-C4 | Rule references resolve group names against `staticGroups` **or** `acceptedGroups[].name`. A static group upserted with a name that already exists in `acceptedGroups` (or literally `<any>`) makes every reference ambiguous; the studio picks one. Same for a service or rule named `<any>`. | `upsert` refuses a `staticGroups` name present in `acceptedGroups[].name`; any entry name equal to `<any>` (case-insensitive, trimmed) → `mss_operation_invalid`. |

## Important (applied)

| ID | Finding | Spec fix |
| --- | --- | --- |
| F-I1 | Read side parses with `studios._parse_inputs_field` (raw string on decode failure); write side with `studios_write._parse_inputs` (`None` on failure). A digest handed out for an unparsable row can never match, and the two parsers can drift. | `inputs_sha256(document)` is defined only for `dict`/`list`; read emits `inputs_sha256: null` otherwise; write refuses `inputs_path_unresolved` (already does). Both call the one function in `inputs_digest.py`. |
| F-I2 | §A says `REQUEST_SUBMIT` is "rejected as unknown request" without naming the code. `_check_request_field` (`resource_write.py:90`) returns **`request_not_allowed`**. | Name it in §A and the test. |
| F-I3 | §D.6 lists `studio_not_found`. `_read_studio_anywhere` both-missing → generic Inputs refuses `preflight_failed` (2.1 fix spec §4.5). Two codes for one condition. | Drop `studio_not_found`; both-missing is `preflight_failed`. |
| F-I4 | Preflight order in §D.5 is prose. `studio_inputs_generic.set_cvp_studio_inputs` already implements the exact order (writes → id → structural → workspace GET → pending/unknown → studio overlay → flags). | Spec names that function as the normative order to copy. |
| F-I5 | The fixture named in §D.0 was described (2 rules, POL1 `["monitor"]`) from a read taken **before** the hand-entered change control; the CC landed at 13:53 CDT. A fresh capture will contain the pdu4 group, three services, three rules and the new POL1 order. The worked-example test ("4 changed paths") would then see `inputs_unchanged`. | Capture **post-change** mainline as the fixture. Derive the pre-change document in the test by removing the pdu4 entries. The worked example test asserts `after_sha256 == inputs_sha256(post_fixture)` — the ops must reproduce byte-for-byte (after canonicalisation) what the UI wrote. |
| F-I6 | `hiddenIntersectedGroupsMapper` / `hiddenVrfMapper` may be input-side state that the UI rewrites when a group is added. If so, an MCP-added group has no mapper entry and the build either fails or silently does not enforce. | F-I5's test surfaces this: if the post-change fixture's hidden mappers reference `pdu4-trendnet`, the ops cannot reproduce the post doc and the test fails. Capture gate now says: inspect the fixture for `pdu4` under any `hidden*Mapper`; if found, **stop and re-spec** before M. |
| F-I7 | `direction: true` and `packet: "any"` in the worked example are asserted, not observed. §D.0 already makes the fixture win, but §D.7 JSON would still be copied verbatim by an agent. | §D.7 is labelled "regenerate from the fixture schema"; the post-change fixture *is* the ground truth for these three rules. |
| F-I8 | `changed_leaf_paths` on an append is `$.rules` (whole list), so `request_body` is the only place a reviewer can see what was added. Preview must be reviewable without diffing a 30 KB body. | Add `entries_added` / `entries_replaced` / `entries_removed` (`collection:name` strings) to the envelope. |

## Closed (do not re-litigate)

- Generic root POST / `replace_all_inputs`: still out. 2.3 is a fixed-studio, fixed-vocabulary tool.
- Keyed `Inputs` GetOne: stay on `/all` + client filter (`_load_root_inputs`).
- Submit: retired. Not "unregistered". `REQUEST_SUBMIT` leaves the allowlist.
- Editing `studios_write._refused`, `studio_inputs_generic.py`, `studio_tags.py`: no.
- `confirm=True` on first call: bound by `preview_token` + `after_sha256` + digest CAS. Three independent checks; adequate.

## Not taken

- Shadowing by a *named* forward rule whose group is a superset of a later drop's group (`mss_rule_shadowed` only catches the all-`<any>` case). Real but needs CIDR containment across AGNI groups whose membership is not in the document. Warning stays literal-`<any>` only.
- Refusing edits to the pre-existing `monitor` rule. Operator choice; `mss_rule_too_broad` already blocks the dangerous mutation (`monitor` → `drop`).
- Envelope size: `request_body` carries the full after-document (~30 KB with 32 AGNI groups). Accepted; it is the reviewable artifact, and the audit-log rule ("never log full inputs") is about logs, not the envelope.
- §C step 5 (`create_cvp_studio` throwaway to exercise the overlay studio GET) is heavy. Kept: it is the only live path that proves the 404-only fallthrough.

## Farm shape (checked)

Buckets S / R / D are file-disjoint. S and M both touch `cloudvision_mcp.py` (docstring vs registration) in different waves — fine. M imports `inputs_digest` and `_load_root_inputs(studio_id=)` from R, so M cannot start until R merges; the spec already sequences it. §B/§C live verifies need only the current image and can run today.

## Post-implementation code review (2026-09-02, branch `feat/studios-phase2-final`)

Three reviewers on the diff: hostile code review, silent-failure hunt, test
coverage. Findings applied in the fix commit; the spec §D.5 / §D.8 carry the
new behaviour.

| ID | Finding | Fix |
| --- | --- | --- |
| R-1 | `_entries` turned a non-list wire collection into `[]` and the confirm POSTed it; inside a writable key, so the scope check could not see it. | `_validate_document_shape` before apply: writable collections present and lists, entries objects with plain unique string names, rule/policy list fields lists of strings → `mss_document_malformed` with path. `_entries` no longer creates anything. |
| R-2 | Unhashable caller values (`op: ["upsert"]`, `action: ["drop"]`) escaped `except _Invalid` as `TypeError` → generic `client_error` with no path. | `isinstance(str)` guards before every set-membership test; `except TypeError` backstop → `mss_operation_invalid`. |
| R-3/4/6/7 | `_check_result` crashed or iterated strings character-wise on malformed pre-existing entries; `_names` unwrapped `{"value": …}` while apply compared raw `name`. | One `_entry_name()` (plain non-empty str or `None`) everywhere; shape check refuses before any of it runs. |
| R-5 | Duplicate rule names on the wire: dict-by-name kept the last, hiding a fabric-wide drop; upsert replaced first, remove deleted all. | Shape check refuses duplicates. |
| R-8 | Workspace / studio GET warnings dropped on the success path. | `warnings = [*ws, *studio, *load]`. |
| R-9/10 | `inputs_path_unresolved` and studio `preflight_failed` lost the source workspace / status. | `inputs_source_workspace_id`, `reason`, `studio_status` in details. |
| R-11 | `" <ANY> "` validated as the wildcard but stored literally; ports stored with whitespace. | Exact `<any>` token; lookalikes are reserved names; port tokens exact. |
| T-12 | A pre-existing empty policy blocked every unrelated edit. | Empty-`policyRules` refusal scoped to policies this call touched (same rule as `mss_rule_broad`). |
| C-1 | `_rule_is_all_any` required a **single-element** `["<any>"]`; `["<any>", "trogdor"]` is any on the wire and reached confirm with no warning — one upsert turned `monitor` into a fabric drop. | Membership, not equality, in both broadness helpers; `<any>` may not share a list with a name; duplicate references refused. |
| C-2 | Two `/1` members (or `::/1` + `8000::/1`) cover the whole space past the `/0` check; `10.0.3.4/8` validated non-strict and was stored verbatim as a `/8`. | `strict=True` (host bits refused), per-family `collapse_addresses` union refused at `/0`, `mss_group_broad` warning wider than `/24` / `/64`. |
| C-I3 | `_is_any` was case/whitespace-loose, so `" <ANY> "` skipped referential integrity and was posted as a literal group name. | Exact `<any>` for semantics; lookalikes are `mss_operation_invalid`. (Same fix as R-11.) |
| C-I4 | `upsert` replaced the whole entry against a closed key set: omitting `description` deleted it; any field CVP adds later would be deleted by the first upsert. | Merge over the existing entry; `test_fixture_entries_use_only_allowlisted_keys` flags a schema drift on re-capture. |
| C-I5 | Content refusals (`monitorName`, empty policy, fabric drop) were global, so UI-left state would brick every edit; the `monitorName` refusal used a `rules:<name>` path while every other `mss_operation_invalid` uses `operations[N]…`. | `monitorName` checked per op with an `operations[N].entry.monitorName` path; fabric-drop and empty-policy refusals scoped to touched entries, pre-existing cases surfaced as `mss_rule_too_broad_existing:<rule>` warnings. Referential integrity stays global. |
| C-L | Spec drift: `mss_rule_broad` scoping, lint step, ` <ANY> ` test, registration test claim; terse tool docstring. | §D.4/§D.5/§D.8 corrected; docstring names the ops, collections and where the digest comes from. |
| T-* | Missing pins: overlay-vs-mainline digest mistake, F-C1 both halves, token composition, token bound to workspace, root row not an object, accepted envelope shape, last-wins upsert, missing credentials, reorder+remove, nested replace paths, transport failure through the real helper, registration guard (lost with the submit suite), `REQUEST_SUBMIT` absent from `resource_write`. | Added. |

Not taken: refusing `immutable: "true"` string flags (consistent with 2.0/2.1;
separate change if wanted); `inputs_root_ambiguous` when several root rows share
a workspace (touches the 2.0 loader; fail-closed today via digest mismatch);
dropping `default=str` from `canonical_json` (spec-canonical, never fires on
parsed JSON); `client_error` logging with `exc_info` (shared helper, separate
change).
