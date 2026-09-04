# Spec: Phase 2.3 MSS Service root Inputs CAS (`studios_write.set_mss_inputs`)

> **Superseded 2026-09-02 by `docs/studios-phase2-final-spec.md`** (carried over with corrections; review that file, not this one).

Status: **draft for review** (2026-09-02). Does not add submit. Does not change 2.0 description CAS or 2.1 generic Inputs.

Requested as "2.2"; the parent's 2.2 slot (`studios_write.create_studio` / `studios_write.delete_studio`) is shipped, so this is 2.3.

Parent: `docs/studios-phase2-spec.md`. Sibling: `docs/studios-phase2-followon-fix-spec.md` (2.1 fixes). Evidence: §9 below and `~/code/obsidian/Personal/Incidents/2026-09-02 Rogue DHCP from TRENDnet PDU stopped with MSS.md`.

## Goal

Let an operator draft an **MSS Service** policy change (static group, service, rule, policy rule order) into a `ws-mcp-*` workspace through the MCP, dry-run first, CAS-guarded, never submitted.

Today this is impossible by design and the failure is exact:

- MSS Service (`studio-mss-service`) stores its whole input tree as **one** Inputs Resource row at `path.values []`.
- 2.1 `studios_write.set_inputs` refuses `[]` (`root_path_forbidden`) and cannot resolve any nested key as a Resource path (`inputs_path_not_found`, `available_path_values: [[]]`). Spec §3 of 2.1: "Do not add a generic root POST. That would bypass 2.0 CAS."
- Even at a valid path, 2.1 is description-only (`allowed_input_keys` default `["description"]`, not exposed by the tool wrapper) and refuses admin tokens. Adding a group or a `drop` rule changes leaves named `name`, `members`, `action`, `services`: `input_key_not_allowed`.

The sanctioned pattern for a root row is 2.0 `studios_write.set_description`: read root, change a bounded set of leaves, verify the diff, CAS, POST root keyed to the draft. 2.3 is the same shape for MSS Service with a bounded **operation** vocabulary instead of a free document.

## Non-goals

- Register `submit_cvp_workspace`. Still unregistered.
- A generic root POST or `replace_all_inputs`. Still forbidden.
- Loosen 2.1 `studios_write.set_inputs` (its allowlist, forbidden tokens, root refusal).
- Touch MSS keys outside the four collections in §3: `securityDomains`, `monitorObjects`, `redirectObjects`, `acceptedGroups`, `ignoredGroups`, `acceptedSensors`, `sslProfileName`, any `hidden*Mapper`, `staticExceptionList`, `enableStaticExceptionList`.
- Change the studio's tag query (that is 2.1 `studios_write.assign_tags`).
- Segment Security studio (`studio-segmentation`, MSS-G). Different schema; separate spec if wanted.
- ChangeControlConfig, approve, execute.

## 1. Canonical names

| Concept | Canonical |
| --- | --- |
| Action | `studios_write.set_mss_inputs` |
| Studio (fixed) | `studio-mss-service` |
| Module | `cvp_mcp/grpc/studio_mss_inputs.py` |
| Tests | `tests/test_studio_mss_inputs.py` |
| Endpoint | `POST /api/resources/studio/v1/InputsConfig` with `key.path.values: []` |
| Read | `GET /api/resources/studio/v1/Inputs/all`, client-filter `studioId`, root key, overlay-then-mainline (same as `studios_write._load_root_inputs`, parameterised by studio) |
| Digest | `inputs_sha256`: SHA-256 hex of `json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)` |
| CAS parameter | `expected_inputs_sha256: str` (**required**; `""` refused) |
| Envelope flag | `posted_at_root: true` |
| Gate | writes env `"1"` + group/action disable checks; `studios_write` is registered only when writes are enabled |

## 2. Read side addition (`studios.inputs`)

Add `inputs_sha256` to every item. Non-breaking. The operator (or agent) copies it into `expected_inputs_sha256`. Compute it over the parsed document, not the wire string, so key order on the wire does not matter.

`studios.inputs` with `studio_id` and `workspace_id=""` already returns the root row for MSS Service; nothing else changes there.

## 3. Operation vocabulary

`operations: list[dict]`, applied in order to a deep copy of the current root document. Each op is one of:

| `op` | Fields | Semantics |
| --- | --- | --- |
| `upsert` | `collection`, `entry` (object with `name`) | Replace the entry whose `name` matches, else append. Entry is validated per collection (§4). |
| `remove` | `collection`, `name` | Remove the entry whose `name` matches. Missing → `mss_entry_not_found`. |
| `set_policy_rules` | `policy`, `policy_rules` (list of rule names) | Replace `policies[name==policy].policyRules`. Policy must exist. |

`collection` ∈ `staticGroups`, `services`, `rules`, `policies`. Anything else → `mss_collection_not_allowed`. `upsert` on `policies` may only create a policy with `name`, `description`, `policyRules`; it may not add unknown keys.

Empty `operations` → `mss_operations_required`. More than 20 ops → `mss_operations_too_many` (this is a hand-edit tool, not bulk import).

Entries are matched by `name` only. Names are case-sensitive strings; whitespace-only or empty names → `mss_operation_invalid`.

## 4. Entry validation (before diff, before any HTTP)

Only these keys are accepted per collection. Any extra key → `mss_operation_invalid` with the offending path. Values are type-checked.

| Collection | Accepted keys | Constraints |
| --- | --- | --- |
| `staticGroups` | `name`, `membership.members` | `members`: non-empty list of IPv4/IPv6 CIDR strings (`ipaddress.ip_network(strict=False)`). No `staticExceptionList`, no `enableStaticExceptionList` (those hit the 2.1 forbidden token `enabled` and are out of scope). |
| `services` | `name`, `protocols`, `configurations[]` with `protocol`, `sourceports`, `destinationports`, `icmpTypes` | `protocols` ∈ `TCP/UDP`, `ICMP`. `protocol` ∈ `tcp`, `udp`, `icmp`. Ports: `all` or `1-65535`, single, comma list, or `a-b` range. `icmpTypes`: `all` or comma list of integers 0-255. |
| `rules` | `name`, `description`, `action`, `sources`, `destinations`, `services`, `packet`, `direction`, `monitorName` | `action` ∈ `forward`, `drop`. `sources`/`destinations`: non-empty lists of group names or `<any>`. `services`: non-empty list of service names or `<any>`. `packet` ∈ `any`. `direction`: bool. `monitorName` only with `action: forward`, and must name an existing `monitorObjects[].name`. |
| `policies` | `name`, `description`, `policyRules` | `policyRules`: list of rule names, unique. |

Referential integrity is checked on the **result** document after all ops: every group name in a rule exists in `staticGroups` or in `acceptedGroups[].name` (AGNI groups are read-only here but may be referenced); every service name exists in `services`; every rule name in any `policyRules` exists in `rules`; a `remove` that leaves a dangling reference is refused `mss_reference_unresolved` naming the referrer.

Shadowing check (warning, not refusal): if a policy's `policyRules` places a rule with `action: forward` and `sources`, `destinations`, `services` all `<any>` **before** any `drop` rule, add warning `mss_rule_shadowed:<policy>:<rule>`. This is the `monitor` rule on this tenant; the operator wants drops ahead of it.

## 5. Write shape (five steps, same as 2.0)

1. Gates: `writes_enabled()`, `validate_workspace_id` (`ws-mcp-*`, not `builtin-`), `expected_inputs_sha256` present and 64 hex chars (`expected_inputs_sha256_required`), operations validated (§3, §4). No HTTP yet.
2. Workspace GET: must exist and be `WORKSPACE_STATE_PENDING`. Studio GET via `_read_studio_anywhere` (overlay then mainline): not `immutable`, not `from_package`.
3. Root read: `Inputs/all`, filter `studio-mss-service`, root key, prefer draft overlay, else mainline. Any `truncated_to_` / `ndjson_skip_invalid_line` warning → `preflight_failed` (a partial tree must never be re-posted). Compute `before_sha256`. Mismatch with `expected_inputs_sha256` → `inputs_digest_mismatch` with `current_inputs_sha256` and `inputs_source_workspace_id` in details, no POST.
4. Apply ops to a deep copy. Validate result (§4). Diff with `_changed_leaf_paths`; **every** changed path must be under `$.staticGroups`, `$.services`, `$.rules`, or `$.policies`, else `tree_diff_outside_mss_scope` (defence in depth against a buggy op). Lint the after-document with `_disruptive_hits`; new hits → `disruptive_content_forbidden`. Zero changed leaves → proceed with warning `inputs_unchanged`.
5. `confirm=False` → `outcome: preview`, `preview_token` bound over `{studio_id, workspace_id, expected_inputs_sha256, operations (canonical JSON), after_sha256}`. `confirm=True` → token check, then one POST:

```json
{
  "key": {"studioId": "studio-mss-service", "workspaceId": "<ws-mcp-…>", "path": {"values": []}},
  "inputs": "<json.dumps(after_document)>"
}
```

Mainline `""` is never written. First write on a fresh draft copies mainline into the overlay (that is what the POST does); later writes read the overlay.

Binding `after_sha256` into the token means a confirm cannot post a different document than the one previewed, even if the caller replays the token with edited operations.

## 6. Envelope

Preview and accepted share these `object` fields:

| Field | Value |
| --- | --- |
| `operation` | `set_mss_policy_inputs` |
| `studio_id` | `studio-mss-service` |
| `workspace_id` | draft id |
| `inputs_source_workspace_id` | `""` or the draft |
| `before_sha256`, `after_sha256` | digests |
| `operations_applied` | echo of validated ops, count |
| `changed_leaves`, `changed_leaf_paths` | from `_changed_leaf_paths`, capped at 10 paths |
| `posted_at_root` | `true` |
| `request_body` | the body above (preview shows it; accepted echoes it) |
| `resource_time` | from the POST response, `None` on preview |
| `next_action` | preview: `Re-call with confirm=True and this preview_token.` accepted: `studios_write.build` |

Refusal codes (new ones in bold): `writes_disabled`, `invalid_workspace_id`, `builtin_workspace_forbidden`, `workspace_not_found`, `workspace_read_failed`, `workspace_not_pending`, `workspace_state_unknown`, `studio_immutable`, `studio_from_package`, `preflight_failed`, `inputs_path_unresolved`, **`expected_inputs_sha256_required`**, **`inputs_digest_mismatch`**, **`mss_operations_required`**, **`mss_operations_too_many`**, **`mss_operation_invalid`**, **`mss_collection_not_allowed`**, **`mss_entry_not_found`**, **`mss_reference_unresolved`**, **`tree_diff_outside_mss_scope`**, `disruptive_content_forbidden`, `preview_token_required`, `preview_token_mismatch`, `resource_write_failed`.

`studios_write._refused` is reused as is (`next_action: None`). Hints go in `details.hint` / `error.message`.

## 7. Worked example (the 2026-09-02 change)

Rogue DHCP server: TRENDnet TPI-06 PDU at `10.0.3.4` (MAC `78:2d:7e:24:cd:6c`, 720xp-48 Ethernet33, VLAN 3) ACKing ceos-2's lease unicast. Operator intent: drop UDP/67 both ways and UDP/TCP 53 + UDP 5353 to the PDU, ahead of the forward-all `monitor` rule in `POL1`.

Read first:

```
studios(action="inputs", studio_id="studio-mss-service")  -> items[0].inputs_sha256 = "<digest>"
```

Then:

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

Preview must show `changed_leaves` covering `$.staticGroups`, `$.services`, `$.rules`, `$.policies[0].policyRules` only, no `mss_rule_shadowed` warning (drops precede `monitor`), and the full `request_body`. Confirm posts once. Then `studios_write.build`; the human reviews the generated `traffic-policy` in CVP and submits there.

If the operator had listed `monitor` first, preview would carry `mss_rule_shadowed:POL1:monitor` and still allow confirm; shadowing is a policy choice, not a safety refusal.

## 8. Tests (no live CVaaS)

Fixture: `tests/fixtures/inputs_mss_service_root_2026-09-02.json`, the live root row captured 2026-09-02 (4 static groups, 5 services, 2 rules, POL1 with `["monitor"]`, 32 AGNI accepted groups, 1 monitor object, 2 device mappers). Redact nothing; it contains no secrets.

- Digest: same document with shuffled key order and different wire whitespace → same `inputs_sha256`.
- `expected_inputs_sha256` omitted / `""` / not 64 hex → `expected_inputs_sha256_required`, no HTTP.
- Digest mismatch → `inputs_digest_mismatch`, `current_inputs_sha256` in details, no POST.
- Overlay row present → digest and diff use the overlay, `inputs_source_workspace_id` is the draft. Overlay absent → mainline.
- Truncated `/all` or skipped NDJSON line → `preflight_failed`, no POST.
- Each collection: `upsert` new, `upsert` replace by `name`, `remove`; unknown key in entry → `mss_operation_invalid` with path; bad CIDR, bad port, bad `action`, `monitorName` on `drop` → `mss_operation_invalid`.
- `remove` of a group still referenced by a rule → `mss_reference_unresolved` naming the rule.
- `set_policy_rules` with an unknown rule → `mss_reference_unresolved`; with a duplicate → `mss_operation_invalid`; on an unknown policy → `mss_entry_not_found`.
- Reference to an `acceptedGroups[].name` (AGNI group) is allowed.
- Forward-all rule before a drop → warning `mss_rule_shadowed:<policy>:<rule>`, outcome still preview.
- Ops that touch nothing → `inputs_unchanged` warning, preview returned.
- Injected op that mutates `securityDomains` (test-only monkeypatch of the applier) → `tree_diff_outside_mss_scope`.
- After-document containing `shutdown` in a description → `disruptive_content_forbidden`.
- Preview token replay with altered operations → `preview_token_mismatch`. Token bound to `after_sha256`.
- Confirm path: exactly one POST, body `key.path.values == []`, `inputs` is a JSON string, `workspaceId` is the draft, never `""`.
- Writes env off → `writes_disabled` before any HTTP; tool absent from registration when env is off.
- Worked example (§7) end to end against the fixture: 4 changed collections, 8 ops applied, no warnings.

## 9. Evidence (live, 2026-09-02, MCP image 1.60 on strongpod)

Refusal from 2.1 generic Inputs, verbatim:

```json
{"outcome": "refused", "dry_run": true,
 "error": {"code": "inputs_path_not_found",
           "message": "Could not read the current Inputs document at path_values.",
           "details": {"studio_id": "studio-mss-service", "path_values": ["rules"],
                       "available_path_values": [[]],
                       "hint": "Use studios_write.set_description for this studio’s only Resource row (path_values []). Generic Inputs cannot POST the root."}},
 "workspace_id": "ws-mcp-mss-block-pdu4-dhcp"}
```

Root row shape on this tenant (`studios.inputs`, `workspace_id=""`, `path_values: []`): top-level keys `acceptedGroups` (32 AGNI-CH groups), `acceptedSensors`, `hiddenDeviceMapper` (JPE19151499 and HBG254804R6 as TOR switches, Loopback1 172.16.0.1/.2), `hiddenIntersectedGroupsMapper`, `hiddenPolicyIdMapper` (POL1 → 256), `hiddenVrfMapper`, `ignoredGroups`, `monitorObjects` (ztx-7230, tunnel 172.16.0.4), `policies` (POL1: `["monitor"]`), `redirectObjects`, `rules` (`monitor`, `group-rule`), `securityDomains` (tag query `security-domain:ZTSEC`), `services` (ip-printing, http, https, ping-icmp, rtsp-554), `sslProfileName`, `staticGroups` (trogdor, pi5-pihole, laptops, fedora1-server).

Assigned tags for the studio: `T3:X3 AND Campus:campus-1709 OR monitor-device:true`.

What the studio renders (720XP, from a public MSS write-up and consistent with the live effect): `traffic-policies` / `vrf ALL` / `traffic-policy input <name> physical`, field-sets per group and service. Enforcement is at physical ingress, so same-VLAN traffic is covered; the PDU's DHCP replies stopped at 13:53 CDT once the policy (entered by hand through a change control) was live.

`studios_write.create_workspace` worked through the MCP the same day (`ws-mcp-mss-block-pdu4-dhcp`, `outcome: accepted`); only the Inputs write was missing.

Claude Code note (client side, not the MCP): in auto mode a permission classifier denied some write calls without showing a prompt. Allowlisting `mcp__cloudvision-mcp__studios_write` in `permissions.allow` avoids that; read groups such as `mcp__cloudvision-mcp__studios` and `mcp__cloudvision-mcp__compliance` can be allowlisted separately. Cursor has no such classifier.

## 10. Files

| File | Change |
| --- | --- |
| `cvp_mcp/grpc/studio_mss_inputs.py` | new: read root (generalise `_load_root_inputs` by studio id, or add a `studio_id` parameter to it), digest, op validation, apply, diff scope check, preview/confirm, POST |
| `cvp_mcp/grpc/studios.py` | add `inputs_sha256` to `studios.inputs` items |
| `cloudvision_mcp.py` | expose `studios_write.set_mss_inputs` behind the writes env gate |
| `tests/test_studio_mss_inputs.py`, `tests/fixtures/inputs_mss_service_root_2026-09-02.json` | §8 |
| `docs/studios-phase2-spec.md` | slice table: add **2.3** row; one sentence under `studios_write.set_inputs` pointing MSS root edits here |
| `README.md` | tool list entry |

Do not edit `studios_write.py` behaviour beyond adding an optional `studio_id` parameter to `_load_root_inputs` (default `ACCESS_INTERFACE_STUDIO_ID`, existing callers unchanged). Do not edit `studio_inputs_generic.py`.

## 11. Live verify (after code; writes on, submit off)

1. `studios.inputs` for `studio-mss-service` → item carries `inputs_sha256`.
2. `studios_write.create_workspace` with `ws-mcp-test-mss-*`.
3. Preview §7 with a stale digest → `inputs_digest_mismatch`. With the right digest → preview, 8 ops, `request_body.key.path.values == []`.
4. Confirm → `accepted`; re-read with the draft id → overlay row present with the new entries, mainline unchanged.
5. `studios_write.build` → `BUILD_STATE_SUCCESS`; inspect generated config in CVP.
6. `studios_write.delete_workspace`. Never submit from the MCP.

## Farm later

Three disjoint buckets. Do not farm until this spec is approved.

| ID | Own |
| --- | --- |
| M | `studio_mss_inputs.py` + tests + fixture |
| R | `studios.py` digest field + `_load_root_inputs(studio_id=...)` + their tests |
| D | registration in `cloudvision_mcp.py`, README, parent spec rows |
