# Adversarial review — MCP tool consolidation spec

**Spec:** `docs/mcp-tool-consolidation-spec.md` (pre-update draft)
**Date:** 2026-09-02
**Sibling:** `docs/compliance-config-image-spec.md`
**Verdict:** Spec is directionally right (hard cut + domain groups + presentation
layer) but had counting errors, unresolved product forks, and FastMCP-specific
gaps that would block a clean implement. Findings below are applied in the
updated consolidation spec unless marked deferred.

## Findings

### Critical

| ID | Finding | Resolution |
| --- | --- | --- |
| C1 | **Group count wrong.** Tables list groups 1–13 (= 13 with writes). Decision text said “14 when writes on / 13 when off.” Off-by-one would fail the surface-count test on day one. | Canonical: **12** always-on groups + optional `studios_write` → **13** with writes, **12** without. |
| C2 | **FastMCP adaptation underspecified.** opnsense tools are classes with `input_schema` / `execute`. This repo uses `@mcp.tool()` functions + `get_env_vars()` + `client_error`. “Call the helper” is ambiguous (raw grpc helper vs wrapper that injects env). | Normative: each member is a **bound callable** that already performs env load + error envelope (extract today’s tool bodies). Dispatcher never opens gRPC itself. Member schemas from explicit `MemberSpec` (signature + required list), not hoped-for inspect magic alone. |
| C3 | **`inventory.get` return type is `str` JSON**, while almost every other tool returns a `dict` envelope. A grouped `inventory` tool would return inconsistent types by action and break clients that assume objects. | Normative: `inventory.get` returns the same **dict** shape as other inventory tools (parse today’s JSON string into structured result / errors). Behavioural parity for fields; wire type normalized. |

### Important

| ID | Finding | Resolution |
| --- | --- | --- |
| I1 | Open items left forked (`DISABLED_TOOLS` grammar; register status vs omit). Implementers would pick differently. | **Closed:** disable list accepts `group` and `group.action`. Status actions **registered** with forbidden envelopes until Resource API works. |
| I2 | Rate-limit migration incomplete. Live buckets: `get_cvp_all_inventory`, `map_cvp_network_topology`, `search_cvp_events`. Spec omitted that `search_cvp_inventory` is decorated but **has no bucket** (dead decorator). | Migrate the **three real** buckets to `inventory.list`, `topology.map`, `events.search`. Do not invent a bucket for `inventory.search` unless product asks. |
| I3 | `tool_enabled` today soft-disables but **keeps the tool in `list_tools`**. Spec mixed “disable” with writes **absence**. | Writes: **unregister** `studios_write`. `CVP_MCP_DISABLED_TOOLS`: soft-disable (still listed); matching `group` disables all actions; `group.action` disables one member. |
| I4 | One-action groups (`flow`, `meta`) add an `action=` hop with no choice reduction (opnsense left `arp`/`system` flat for this reason). | **Keep grouped** for hard-cut uniformity (every catalog name takes `action`). Document the hop cost; do not special-case. |
| I5 | Moving `designed_config` from Studios reads to `compliance` hurts discoverability for agents taught “studios → designed config.” | `studios` group description + `help` must cross-reference `compliance.designed_config`. README Studios section links compliance. |
| I6 | Extra kwargs on union schemas: clients may send fields for the wrong action. | Dispatcher **strips** unknown keys for the selected action (after alias remap) rather than forwarding silently into helpers; refuse only if required missing. |
| I7 | Wave 1 “empty/help-only shell” vs “hard cut map complete before merge” conflict if Wave 1 could merge. | Waves are branch-internal; **merge gate** = all members wired + flat tools deleted + surface test green. |
| I8 | Sibling compliance draft still said “omit or soft-refuse” status actions; parent recommended register+forbidden. | Parent wins; sibling updated to match. |

### Minor

| ID | Finding | Resolution |
| --- | --- | --- |
| M1 | No explicit audit that every flat name maps 1:1 (easy to drop a tool). | Add inventory appendix: full flat→group.action table; test locks the set. |
| M2 | `studios_write` description must restate **never submits** (easy to lose in group blurb). | Required sentence in group description + `help` preamble. |
| M3 | Env `CLOUDVISION_MCP_ALLOW_WRITES` is read at **import**; restart required — true today, easy to forget after regroup. | Restate under writes + deploy notes. |
| M4 | Phase 2 specs / session notes naming flat tools — update scope was README-heavy. | Wave 4 includes grep of `docs/studios-*.md` + README for flat MCP tool names (not every historical research note). |
| M5 | Error shape for bad `action` / missing fields undefined vs Studios `_refused` / `client_error`. | Missing/unknown action → small dict `{"error":"…","tool":"<group>","action":…}` consistent with `tool_disabled` / `rate_limit_exceeded`; do not invent a second envelope family for dispatcher gates. |

## Deferred (out of consolidation scope)

- Unlocking configstatus/imagestatus 403 (Arista/instance).
- Renaming Python helpers off `get_cvp_*`.
- Digest-compare interim for config status.
- Retiring studio create/delete actions.

## Post-update checklist for implementers

Use the **updated** `docs/mcp-tool-consolidation-spec.md` only. Surface counts:
**12** groups writes-off, **13** writes-on; **44** legacy members + **2** status
stubs = **46** member slots when status actions are registered.
