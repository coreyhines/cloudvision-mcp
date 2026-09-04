# Spec: MCP tool surface consolidation (full catalog, hard cut)

Status: **implemented on `feat/mcp-tool-consolidation`** (2026-09-03;
acceptance below verified by `tests/test_tool_surface.py`,
`tests/test_tool_catalog.py`, `tests/test_tool_groups_dispatch.py`). Review record:
`docs/mcp-tool-consolidation-adversarial-review.md` (C1–C3, I1–I8, M1–M5
applied). Sibling: `docs/compliance-config-image-spec.md`. Pattern reference:
opnsense-mcp `GroupedTool` + `tool_groups.py` (presentation-only grouping).

## Decision summary

| Decision | Choice |
| --- | --- |
| Scope | **Full catalog** — every current flat MCP tool becomes exactly one `group` + `action` |
| Cutover | **Hard cut** — old flat names removed from `list_tools` in the same release; **no** aliases, **no** `TOOL_SURFACE` flag |
| Grouping axis | Domain / subsystem |
| Shape | Required `action` (enum of members + `help`); every group supports `help` |
| Always-on groups | **12** |
| Writes | Group `studios_write` registered only when `CLOUDVISION_MCP_ALLOW_WRITES` is exactly `"1"` (import-time; **restart** to change) → **13** tools when on |
| Status actions | `compliance.config_status` / `image_status` **registered**; on known 403 return forbidden envelopes (sibling spec) |
| Disable list | `CVP_MCP_DISABLED_TOOLS` accepts `group` and `group.action` (soft-disable; tool still listed) |
| Implementation | Presentation layer only — extract today’s tool bodies as member callables; do not merge grpc business logic into the dispatcher |

## Why

~**44** flat FastMCP tools (~35 always + up to **9** writes). Near-duplicate
verbs invite wrong picks; Claude allowlists and agent context pay per name.
opnsense-mcp showed: keep one implementation per op, expose subsystem tools
with `action`. Schema hygiene (shared fields, no duplicate action lists in
descriptions) matters more for tokens than renaming alone.

## Non-goals

- Changing Studios write semantics, preview tokens, or “MCP never submits”.
- Fixing configstatus/imagestatus **403** (SA is already `network-admin`;
  instance boundary — sibling spec).
- Digest-compare fake compliance status.
- Retiring `create_studio` / `delete_studio` actions (low priority; Inputs on
  existing studios remains the product focus).
- Rewriting Connector / LLDP / MSS helpers.
- Soft deprecation / dual catalog.
- Renaming Python modules off `get_cvp_*` (optional later).

## Canonical surface

| Writes env | `list_tools` count | Names |
| --- | ---: | --- |
| off / unset | **12** | all groups except `studios_write` |
| `"1"` | **13** | twelve + `studios_write` |

Group names (always on): `inventory`, `endpoints`, `device`, `overlay`,
`routing`, `topology`, `events`, `flow`, `probes`, `compliance`, `meta`,
`studios`.

One-action groups (`flow`, `meta`) still use `action=` for catalog uniformity
(I4); the hop is accepted.

### Member map (flat → group.action)

| Flat tool (removed) | group.action |
| --- | --- |
| `get_cvp_one_device` | `inventory.get` |
| `get_cvp_all_inventory` | `inventory.list` |
| `search_cvp_inventory` | `inventory.search` |
| `get_cvp_endpoint_location` | `endpoints.get` |
| `get_cvp_all_endpoint_locations` | `endpoints.list` |
| `get_cvp_endpoint_locations_filtered` | `endpoints.filter` |
| `get_cvp_device_config` | `device.config` |
| `get_cvp_interfaces` | `device.interfaces` |
| `get_cvp_vlans` | `device.vlans` |
| `get_cvp_ip_interfaces` | `device.ip_interfaces` |
| `get_cvp_features` | `device.features` |
| `get_cvp_system_health` | `device.health` |
| `get_cvp_evpn` | `overlay.evpn` |
| `get_cvp_vxlan` | `overlay.vxlan` |
| `get_cvp_bgp_status` | `routing.bgp` |
| `get_cvp_routes` | `routing.routes` |
| `get_cvp_lldp_neighbors` | `topology.lldp` |
| `map_cvp_network_topology` | `topology.map` |
| `get_cvp_events` | `events.list` |
| `search_cvp_events` | `events.search` |
| `get_cvp_flow_data` | `flow.get` |
| `get_cvp_all_connectivity_probes` | `probes.list` |
| `get_cvp_one_connectivity_probe` | `probes.get` |
| `get_cvp_all_bugs` | `compliance.bugs` |
| `get_cvp_all_device_lifecycle` | `compliance.lifecycle` |
| `get_cvp_designed_config` | `compliance.designed_config` |
| *(new)* | `compliance.config_status` |
| *(new)* | `compliance.image_status` |
| `get_cvp_probe_arista_apis` | `meta.probe_apis` |
| `get_cvp_studios` | `studios.list` |
| `get_cvp_studio` | `studios.get` |
| `get_cvp_studio_inputs` | `studios.inputs` |
| `search_cvp_studio_templates` | `studios.search_templates` |
| `get_cvp_workspaces` | `studios.list_workspaces` |
| `get_cvp_workspace` | `studios.get_workspace` |
| `get_cvp_workspace_build` | `studios.get_build` |
| `get_cvp_studio_assigned_tags` | `studios.tags` |
| `create_cvp_workspace` | `studios_write.create_workspace` |
| `delete_cvp_workspace` | `studios_write.delete_workspace` |
| `build_cvp_workspace` | `studios_write.build` |
| `set_cvp_access_interface_description` | `studios_write.set_description` |
| `set_cvp_studio_inputs` | `studios_write.set_inputs` |
| `assign_cvp_studio_tags` | `studios_write.assign_tags` |
| `create_cvp_studio` | `studios_write.create_studio` |
| `delete_cvp_studio` | `studios_write.delete_studio` |
| `set_cvp_mss_policy_inputs` | `studios_write.set_mss_inputs` |

**Counts:** 44 legacy flats + 2 status stubs = **46** member slots behind **12**
or **13** names. A test must assert the flat-name set is empty and the
group.action set equals this table (M1).

### Group descriptions (required phrases)

| Group | Must include |
| --- | --- |
| `studios` | Cross-ref: designed-config provenance is `compliance` action `designed_config` (I5) |
| `studios_write` | **Never submits** a workspace; never approves/executes change controls; dry-run unless `confirm` + matching `preview_token`; drafts only `ws-mcp-*` (M2) |
| `compliance` | Config/image **status** (not remediation); `config_status` / `image_status` may be unavailable on some tenants |

## Status actions

Normative API detail: `docs/compliance-config-image-spec.md`.

In **this** release:

1. Both actions appear in `compliance`’s `action` enum and `help`.
2. Implementation may short-circuit to `coverage=none` +
   `configstatus_forbidden` / `imagestatus_forbidden` until GetOne returns 200.
3. No derived digest “compliance.” Consolidation is not blocked on Resource API
   access.
4. Full GetOne + fixtures = follow-on PR when a 200 is capturable.

## Dispatcher contract

Adapt opnsense `GroupedTool` ideas; **do not** import that package.

### MemberSpec

Each member has:

- `action: str`
- `description: str` (for `help`)
- `required: list[str]` / `optional` field metadata for `help` only
- `properties: dict` — JSON-Schema property defs for this action’s params
- `call: Callable[..., Any]` — today’s tool **body** (env + try/except + return)

Schemas are authored (or generated once into `MemberSpec`) from current
signatures; do not rely on naive `inspect` alone for nested types
(`operations: list[dict]`, etc.) (C2).

### Runtime rules

1. `action` required; enum = members + `help`.
2. `action=help` → JSON listing each action’s description, required, optional,
   defaults. Do not also paste the full action list into the group description.
3. Unknown `action` → `{"error":"action_unknown","tool":"<group>","action":...}`
   and hint `help` (M5).
4. Missing required fields for the selected action →
   `{"error":"action_args_invalid","tool":"<group>","action":...,"required":[...]}`
   (M5). Same family as `tool_disabled` / `rate_limit_exceeded`.
5. **Strip** kwargs not in that member’s property set after alias remap (I6).
   Do not pass surprise keys into helpers.
6. Field aliases only if a member needs a field literally named `action`
   (none today for Studios; MSS uses `operations[].op`).
7. **Shared fields** (`device_id`, `workspace_id`, `confirm`, `preview_token`,
   `studio_id`, …): one canonical schema snippet reused across members
   (`cvp_mcp/schema_fields.py`).
8. Envelope: member return values unchanged **except** `inventory.get`, which
   must return a **dict** (structured device or error), not a JSON `str` (C3).
9. `CVP_MCP_DISABLED_TOOLS`: if `group` ∈ set → all actions return
   `tool_disabled` with `tool`=`group` or `group.action`. If `group.action` ∈
   set → that member only. Catalog entry remains (I3).
10. Rate limits: retarget buckets (I2):

| Bucket key | Former | Limit (keep) |
| --- | --- | --- |
| `inventory.list` | `get_cvp_all_inventory` | 6 / 60s |
| `topology.map` | `map_cvp_network_topology` | 4 / 60s |
| `events.search` | `search_cvp_events` | 10 / 60s |

Remove the dead `@rate_limited_tool` on inventory search unless a bucket is
added deliberately.

## Registration architecture

| Piece | Role |
| --- | --- |
| `cvp_mcp/grpc/*` | Unchanged business logic |
| `cvp_mcp/schema_fields.py` | Shared property defs |
| `cvp_mcp/grouped_tool.py` | Union schema, help, validate, strip, dispatch |
| `cvp_mcp/tool_groups.py` | `GROUPS` + docstring counts; builds group tools from MemberSpecs |
| `cvp_mcp/tool_access.py` | Disable matching for `group` / `group.action` |
| `cvp_mcp/rate_limit.py` | Buckets keyed by `group.action` |
| `cloudvision_mcp.py` | Register **only** group tools; `studios_write` under `if writes_enabled()` |

Hard cut: no flat `@mcp.tool` left; no pass-through “until mapped.”

## Docs and client breakages

Same PR as code:

| Target | Change |
| --- | --- |
| `README.md` | Group + `action` tables; allowlist → `mcp__cloudvision-mcp__studios_write`, `…__studios`, `…__compliance`, etc. |
| `docs/studios-*.md` (product specs, not historical research dumps) | Replace flat MCP tool names with group.action (M4) |
| Claude `permissions.allow` | Operator update (document in README) |
| Deploy | New image tag; **restart**; clients **reload MCP tools** |

PR title/body: **breaking** catalog rename.

## Testing

| Test | Assert |
| --- | --- |
| Surface writes off | Exactly the 12 always-on names; no `studios_write`; no `get_cvp_`* |
| Surface writes on | Those 12 + `studios_write`; never submit |
| Flat→action bijection | Frozen set of 46 member ids (or 44 if status stubs deferred — **not** deferred: 46) matches `tool_groups` |
| `help` | Every group lists every member |
| Dispatch smoke | Mocked happy path per group |
| Strip / required | Wrong-action extra fields ignored; missing required → `action_args_invalid` |
| Disable | `inventory` disables all inventory actions; `inventory.search` disables one |
| Rate limit | Exceeding `inventory.list` bucket → `rate_limit_exceeded` |
| `inventory.get` | Returns `dict`, not `str` |
| Docstring counts | “46 operations behind 12 names (13 with writes)” locked by test |

Reuse reload pattern from `tests/test_write_registration.py`.

## Implementation waves

Do not farm until this spec is approved. **Merge only when acceptance passes**
(I7) — early waves stay on a feature branch.

| Wave | Own | Depends |
| --- | --- | --- |
| 0 | Spec approved (this doc + sibling status behaviour) | — |
| 1 | `schema_fields`, `grouped_tool`, `tool_groups` skeleton + surface test failing red | 0 |
| 2 | Wire all always-on groups; delete flat read tools; normalize `inventory.get` | 1 |
| 3 | Wire `studios_write`; delete flat writes; registration tests | 2 |
| 4 | README + `docs/studios-*.md` renames | 3 |
| 5 | `config_status` / `image_status` forbidden stubs (or real GetOne if 200 appears) | 2 |

## Acceptance

- [x] `list_tools` ∈ {12 names} or {13 names with `studios_write`}.
- [x] No flat legacy MCP names.
- [x] Every row in the member map reachable; `help` complete.
- [x] Writes env gate unchanged in spirit; import-time; no submit.
- [x] Disable + rate-limit keys use `group` / `group.action`.
- [x] `inventory.get` is dict-valued.
- [x] README/allowlist examples use new names only.
- [x] pytest + pre-commit green; count test green (674 passed, 2026-09-03).

## Closed product picks (were Open)

| Topic | Pick |
| --- | --- |
| Disable grammar | `group` and `group.action` |
| Status actions | Register + forbidden until Resource API works |
| Helper renames | Out of scope |

## Related

- Review: `docs/mcp-tool-consolidation-adversarial-review.md`
- Compliance status APIs: `docs/compliance-config-image-spec.md`
