# Spec: Studios Phase 2 — workspace write tools

Status: 2.0 / 2.1 / 2.2 shipped. **Submit retired 2026-09-02**; 2.3 MSS root Inputs CAS
and the standing live verifies are in `docs/studios-phase2-final-spec.md`. AssignedTags `/all` is live on this tenant. Revised 2026-08-22 after live
Inputs capture + adversarial review
(`docs/research/studios-phase2-adversarial-review.md`). Parent:
`docs/studios-support-spec.md`.

Do **not** mix Phase 2 implementation into leftover Phase 1 work. Do **not**
register write tools until a human sets env gates on the homelab MCP host.

## Canonical names (do not invent aliases)

Use these strings in code, tests, and docs. If CVP wire JSON is camelCase, map it
to the snake_case in the envelope.

| Concept | Canonical |
| --- | --- |
| Envelope helper | `tool_envelope(..., obj=)` → JSON key `object` |
| Call-time deny list | `CVP_MCP_DISABLED_TOOLS` via `tool_enabled` |
| Writes env | `CLOUDVISION_MCP_ALLOW_WRITES` exact `"1"` |
| Resource prefix | `/api/resources/` (Phase 1 `cvp_mcp/grpc/studios.py`) |
| Mainline workspace | `""` (empty string). MCP **never** writes it. |
| Build request enum | `REQUEST_START_BUILD` (the **only** allowed request; submit retired) |
| Build success | `BUILD_STATE_SUCCESS` |
| Build fail / cancel | `BUILD_STATE_FAIL`, `BUILD_STATE_CANCELED` |
| Draft workspace | `WORKSPACE_STATE_PENDING` |
| Studio flags | `immutable`, `from_package` (wire `fromPackage`) |
| Inputs field on wire | JSON **string** named `inputs` |
| Description JSON path | `adapterDetails.description` (live Inputs 2026-08-22) |
| Port locator | `tags.query` = `interface:<IfName>@<serial>` (e.g. `interface:Ethernet6@JPE19151499`) |
| Inputs resource key for this studio | **root**: `path: {}` / `path_values: []` — all ports in **one** document |
| Access studio (this tenant) | `studio-campus-access-interfaces` |
| 720xp-24 | serial `JPE19151499`, hostname `720xp-24` |
| 720xp-48 | serial `HBG254804R6`, hostname `720xp-48` (live `search_cvp_inventory` 2026-08-22) |
| Write helper module | `cvp_mcp/grpc/studios_write.py` |
| Gate module | `cvp_mcp/write_access.py` |

Unset, `""`, `"0"`, `"true"`, `"yes"`, and JSON `true` are **off** for the writes env gate. Only the stripped string `"1"` is on.

## Why, and what 2.0 is for

Phase 1 can show designed config and current studio inputs. It cannot draft a
workspace. The first operator job is **description-only** edits in Access Interface
Configuration (`studio-campus-access-interfaces`).

Live 2026-08-22: mainline inputs and running config on `720xp-24` already match the
intended descriptions. 2.0 must still be able to do that class of edit when labels
drift again.

That job does **not** need: tag replace, studio create/delete, or submit.

## Slices

| Slice | Tools | Ship when |
| --- | --- | --- |
| **2.0** | `create_cvp_workspace`; `delete_cvp_workspace`; `set_cvp_access_interface_description`; `build_cvp_workspace` | Writes env `"1"`. Locator fixture: `tests/fixtures/inputs_ethernet6_720xp24_locator.json` |
| **2.1** | `assign_cvp_studio_tags` (no unassign-all); generic `set_cvp_studio_inputs` | Shipped. Tags: expected-current required (`""` valid = unassigned) |
| **2.2** | `create_cvp_studio`; `delete_cvp_studio` | Shipped. Templates must never contain interface shutdown; no ChangeControlConfig |
| **2.3** | `set_cvp_mss_policy_inputs` | `docs/studios-phase2-final-spec.md` §D |
| **Submit** | retired 2026-09-02 | never — the human submits the reviewed workspace in the CVP UI |

`get_cvp_studio_assigned_tags` can ship with 2.0 as a **best-effort read** (`GET AssignedTags/all` is live; no-row is `query=""`) but is **not** required for description CAS. Generic `set_cvp_studio_inputs` is **not** 2.0.

## Goals

1. Same CVP loop as a human: workspace → inputs → build → human review and submit in the CVP UI.
2. Default **dry-run**. Mutate only with `confirm=True` and writes env `"1"`.
   Changing env after process start does **not** register tools; restart the MCP
   process. Dry-run is unavailable when writes are unregistered (tools are absent).
3. The MCP never submits. The human reviews the workspace diff in the CVP UI and submits there.
4. Never write mainline. Never call ChangeControlConfig.
5. Never emit `shutdown` / `no shutdown` on a switchport from MCP.

## Non-goals (all slices)

- CC create / start / approve / execute (UI).
- Configlet CRUD.
- One-shot “do the whole flow” tools.
- Polling inside write tools (Phase 1 `get_cvp_workspace` / `get_cvp_workspace_build`).
- Running-config via Connector, eAPI, or SSH.

## Phase 1 tools reused

| Tool | Role |
| --- | --- |
| `get_cvp_studios` / `get_cvp_studio` | Pick studio; refuse `immutable` / `from_package` |
| `get_cvp_studio_inputs` | Current document + path discovery |
| `search_cvp_studio_templates` | Which studio owns a string |
| `get_cvp_workspaces` / `get_cvp_workspace` | Existence, `state`, `responses.values` |
| `get_cvp_workspace_build` | `BUILD_STATE_*` |
| `get_cvp_designed_config` | Before/after designed CLI |

## 2.0 read tool

### `get_cvp_studio_assigned_tags`

| | |
| --- | --- |
| **Endpoint** | `GET /api/resources/studio/v1/AssignedTags/all` then client-filter |
| **Parameters** | `studio_id: str`, `workspace_id: str \| None = None` (mainline `""`) |
| **Returns** | `tool_envelope` `items[]`: `{studio_id, workspace_id, query}` |
| **Why** | 2.1 assign **replaces** the query. Ship this read even if writes stay off. |

`GET AssignedTags/all` is live (HTTP 200) on this tenant. HTTP 404 or an empty
**body** still returns `coverage="none"` and `assigned_tags_unavailable`. A
complete 200 with 0 rows for this studio+workspace is `query=""` and
`coverage="full"`. Incomplete `/all` (truncation or skipped NDJSON) is
`assigned_tags_read_failed`, not `query=""`. Not on the 2.0 description CAS path.

## HTTP helper (all write slices)

Prefix: `/api/resources/` (same as Phase 1). Same host allowlist and bearer.

Verified 2026-08-19: **container** token (398 chars) works. Workstation `~/.env`
token (1031 chars) → 401. Do not test writes with the workstation token.

| Operation | Method + path | 2.0? |
| --- | --- | --- |
| Workspace create / request | `POST /api/resources/workspace/v1/WorkspaceConfig` | yes |
| Delete workspace | `DELETE /api/resources/workspace/v1/WorkspaceConfig?key.workspaceId=` | 2.0 drafts only |
| Set inputs | `POST /api/resources/studio/v1/InputsConfig` | 2.0 |
| Assign tags | `POST /api/resources/studio/v1/AssignedTagsConfig` | 2.1 |
| Studio upsert / remove | `POST /api/resources/studio/v1/StudioConfig` | 2.2 |
| ChangeControlConfig | forbidden | never |

Wire JSON may be camelCase. Envelopes stay snake_case.

### `post_resource_config(path, body)` / `delete_resource_config(path)`

Enforce **before** building the HTTP request. Tests must hit the helper directly
(no network).

1. **Path allowlist (exact):**
   - `/api/resources/workspace/v1/WorkspaceConfig`
   - `/api/resources/studio/v1/InputsConfig`
   - `/api/resources/studio/v1/AssignedTagsConfig` (2.1)
   - `/api/resources/studio/v1/StudioConfig` (2.2)
   Any other path, including `changecontrol/*` and `configlet/*`, raises.
2. **`request` allowlist:** if `request` is present on the **top-level body**, value
   must be `REQUEST_START_BUILD`. Reject **any other string** (`request_not_allowed`),
   including `REQUEST_SUBMIT` — submit is retired, not gated. Do not hard-code
   guessed names such as `REQUEST_SUBMIT_FORCE` / `REQUEST_ROLLBACK`.
3. **Envelope key denylist** (WorkspaceConfig / StudioConfig **only**, top-level and
   `requestParams` / `request_params`): reject `start`, `schedule`. Do **not** scan
   the InputsConfig `inputs` JSON string for these keys — that false-positives
   descriptions and schema. Inputs safety is the EOS lint below, not this denylist.
4. **`key.workspaceId` / `key.workspace_id`** after strip is non-empty. Empty is
   mainline → `error="workspace_id_required"` (client-side; server behavior unverified).
5. Response is one JSON object, not NDJSON.

## Process / env gates

`tool_enabled` only reads `CVP_MCP_DISABLED_TOOLS`. It has no `writes=` flag.
Registration of write tools is a **separate** filter in `cvp_mcp/write_access.py`.

| Gate | On | Default | Off |
| --- | --- | --- | --- |
| `CLOUDVISION_MCP_ALLOW_WRITES` | `"1"` | off | Do not register 2.0/2.1/2.2/2.3 writes. Runtime: `error="writes_disabled"`, no POST/DELETE. |
| `CVP_MCP_DISABLED_TOOLS` | comma names | empty | Independent deny |
| `confirm` | every write | `False` | Dry-run: validate + documented GETs only. **Not sufficient alone.** |
| `workspace_id` | `strip()`, non-empty, `^ws-mcp-`, not `^builtin-` (case-insensitive) | — | else `workspace_id_required` / `builtin_workspace_forbidden` / `invalid_workspace_id` |
| `preview_token` | required on `confirm=True` | — | `preview_required` |

Dry-run order:

1. Writes env off → tools missing; if called → `writes_disabled`.
2. Writes on, `confirm=False` → preview, no mutate. Response includes
   `preview_token = sha256(tool_name + "|" + canonical JSON of args)`.
3. `confirm=True` → recompute token from **this** call’s args; mismatch or
   missing → `preview_required`, no HTTP. Match → **one** mutating HTTP.

Every preflight GET that a refuse/preview needs must return HTTP 200. Any other
status → `preflight_failed`, no POST/DELETE. A warning is never enough to proceed.

`get_cvp_designed_config` is **mainline**. It is a before-snapshot, not the
workspace review. Humans review the workspace diff in the CVP UI.

DELETE uses `delete_resource_config(path, params)` — path matched exactly
(no query string in the allowlist entry); query values URL-encoded by the helper.

Audit INFO: tool, `workspace_id`, `studio_id`, `request_id`, outcome. Never log
token, Authorization, full inputs, template, or schema. Redact values whose **key
names** match `(?i)(password|secret|token|credential)` — do not redact studio
resource `key` / `studio_id`.

Hard-code `request` per tool. Never take it from the model.

## Canonical workflow

```text
2.0  create_cvp_workspace
     set_cvp_access_interface_description   (description CAS; preserve siblings)
     build_cvp_workspace       (REQUEST_START_BUILD)
     poll get_cvp_workspace     until responses.values[<request_id>] or 30s
     poll get_cvp_workspace_build until BUILD_STATE_SUCCESS|FAIL|CANCELED

2.1  assign_cvp_studio_tags    (optional; expected_current_query required)
2.3  set_cvp_mss_policy_inputs (MSS Service root; digest CAS)

     human reviews the workspace diff, submits, approves/executes CC in CVP UI — never MCP
```

Human submit (CVP UI) updates **mainline designed config** even with no device CC executed.
This CVaaS tenant (2026-08-20): submit CCs stay **pending approval**. Re-check if
tenant Change Control settings change.

Poll: 2s, timeout 120s. Never inside a write tool.

## 2.0 worked example — descriptions only

Studio: `studio-campus-access-interfaces`.
Devices: `JPE19151499` (`720xp-24`), `HBG254804R6` (`720xp-48`).

Live Inputs (2026-08-22): **one** resource, `path_values: []`. Do not assume a
fixed JSON pointer for the campus nest. Walk the parsed `inputs` object and find
the unique dict that has `tags.query == "interface:Ethernet6@JPE19151499"`
(siblings analogously). Wire field: `adapterDetails.description`. See
`tests/fixtures/inputs_ethernet6_720xp24_locator.json`.

Intended descriptions (already true on 720xp-24 as of 2026-08-22):

| Device | Interface | If stale, set to |
| --- | --- | --- |
| 720xp-24 | Ethernet6 | `pi5 - dns` |
| 720xp-24 | Ethernet2 | `330ddungeon` (LLDP hostname; not “330unused ap”) |
| 720xp-24 | Ethernet19 | `720xp-48-ma1` |
| 720xp-24 | Ethernet14 | `samsung75-tv` |
| 720xp-24 | Ethernet17 | `atv-basement` |
| 720xp-48 | Ethernet51 | `ds1821-10g` |

**Allowed leaf:** `adapterDetails.description` on the matching `tags.query` row.
Sibling keys (`enabled`, `portProfile`, `vlans`, …) must be copied unchanged.

**Write shape:** POST InputsConfig at **root** `path.values: []` with the **full**
inputs tree. That looks like a whole-document replace on the wire because this
studio has no per-port Inputs key. Safety is the **tree diff**, not a nested path:

1. GET mainline Inputs (`studio-campus-access-interfaces`, `workspaceId=""`).
2. Deep-copy `inputs`. Find the unique row whose `tags.query` equals
   `interface:<Interface>@<serial>` (`Ethernet6` not `Et6`).
3. Compare `adapterDetails.description` to `expected_current_description`
   (`null` in JSON matches `""` in the tool arg). Mismatch →
   `current_description_mismatch`.
4. Set only that description. JSON-dump both trees (sorted keys). Diff must be
   exactly one leaf. Any other change → `tree_diff_not_description_only`, no POST.
5. POST `{key:{studioId, workspaceId, path:{values:[]}}, inputs: <json string>}`.

First write into a new workspace copies the **mainline** tree then patches. Later
writes GET the workspace overlay if present, else mainline.

Dry-run must include: locator query, before/after description, `changed_leaves: 1`,
`posted_at_root: true`, `disruptive: false`.

POST of this body against CVaaS is **not** live-verified (read-only capture). First
implementer run: dry-run against mainline, then one `ws-mcp-test-*` workspace, build,
delete workspace — no submit.

EOS lint on inputs **string** and on templates: refuse `shutdown`, `no shutdown`,
`no interface`, `reload`, `write erase`. Description-only patches must not trip
this; if they do, the path is wrong.

## Write envelope and errors

Python: `tool_envelope(..., obj=...)`. JSON key is `object`, never a keyword
argument named `object`. Envelope `collected_at` is MCP time. If CVP returns a
resource timestamp, put it in `object.resource_time`.

`object` shape:

```json
{
  "outcome": "accepted|preview|refused",
  "dry_run": true,
  "error": null,
  "workspace_id": "...",
  "next_action": null
}
```

On refusal, `error` is `{ "code": "<code>", "message": "...", "details": {} }`.
`coverage` is `none` on refuse, `full` on preview/accepted when preflights ran.
Do not put `error` as a top-level envelope key.

Normative codes: `writes_disabled`, `workspace_id_required`,
`builtin_workspace_forbidden`, `workspace_not_found`, `workspace_id_exists`,
`workspace_state_unknown`, `workspace_not_pending`, `build_in_progress`,
`workspace_read_failed`, `studio_not_found`, `studio_immutable`,
`studio_from_package`, `inputs_path_unresolved`, `inputs_path_not_found`,
`current_description_mismatch`, `tree_diff_not_description_only`,
`root_path_forbidden`, `current_query_mismatch`,
`preview_required`, `preflight_failed`,
`invalid_request_id`, `disruptive_content_forbidden`, `resource_write_failed`.

Unknown Workspace `state` or unknown build state → fail closed
(`workspace_state_unknown` / `build_in_progress` as appropriate). Do not treat
unknown as pending.

## Tool reference

Every write: `tool_envelope(..., obj=...)` with `object.dry_run: true` when
`confirm=False`.

### `create_cvp_workspace` (2.0)

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Parameters** | `workspace_id: str`, `display_name: str`, `description: str = ""`, `confirm: bool = False` |
| **Body** | `{"key":{"workspaceId":"..."},"displayName":"...","description":"..."}` |
| **Preflight** | Strip; reject empty and `^builtin-`. GET Workspace; exists → `workspace_id_exists`. GET fail → no POST. |
| **Id** | Caller supplies `ws-mcp-<purpose>-<YYYYMMDD>-<uuid8>` |
| **Returns** | `workspace_id`, `display_name`, `resource_time`, `dry_run` |

### `delete_cvp_workspace` (2.0)

| | |
| --- | --- |
| **Endpoint** | `DELETE /api/resources/workspace/v1/WorkspaceConfig?key.workspaceId=` |
| **Parameters** | `workspace_id: str`, `confirm: bool = False` |
| **Preflight** | GET Workspace. Missing → no DELETE. `state` must be `WORKSPACE_STATE_PENDING`. Submitted/abandoned → refuse. |

### `set_cvp_access_interface_description` (2.0)

Purpose-built CAS. Not a generic Inputs POST. Studio is fixed:
`studio-campus-access-interfaces`.

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/InputsConfig` |
| **Parameters** | `workspace_id: str`, `device_id: str` (serial), `interface: str`, `expected_current_description: str`, `new_description: str`, `confirm: bool = False` |
| **Behavior** | Follow the five-step write shape in the worked example. Locator: `interface:<interface>@<device_id>`. Interface names as EOS (`Ethernet6`). |
| **Refuse** | Empty/`builtin-` workspace; `immutable` / `from_package`; 0 or >1 locator matches (`inputs_path_not_found`); CAS mismatch; tree diff ≠ one description leaf; caller extra fields; EOS lint (`disruptive_content_forbidden`). |
| **Returns** | `workspace_id`, `device_id`, `interface`, `locator`, before/after description, `posted_at_root: true` |

### `set_cvp_studio_inputs` (2.1, not 2.0)

Generic path POST. Same helper rules. Diff proposed `inputs` against current
document; refuse `input_key_not_allowed` if any **changed leaf** is not in
`allowed_input_keys` (default `["description"]`). Keys that mean admin/forwarding/power
(`enabled`, `disabled`, `shutdown`, `vlan`, `poe`, `profile`, `mode`) are never
allowed here — that is how a studio emits `shutdown` without the word appearing.
**No** `replace_all_inputs` until a later
explicit revision. Empty `path_values` → `root_path_forbidden`. Resource
`path.values` is not a JSON key path into `inputs`. Access Interfaces' only
Resource row is `[]` and stays 2.0 `set_cvp_access_interface_description`. MSS Service
(`studio-mss-service`) is also a single root row; its edits are **2.3**
`set_cvp_mss_policy_inputs` (`docs/studios-phase2-final-spec.md` §D), not this tool.

### `build_cvp_workspace` (2.0)

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Parameters** | `workspace_id: str`, `request_id: str \| None = None`, `confirm: bool = False` |
| **Body** | `{"key":{"workspaceId":"..."},"request":"REQUEST_START_BUILD","requestParams":{"requestId":"<uuid>"}}` |
| **request_id** | Confirm call must pass the `request_id` from the **preview**. If omitted on `confirm=True`, generate UUIDv4 **only then**. A dry-run id is not reused unless the caller supplies it. Reject blank. |
| **Refuse** | Missing/builtin workspace; `state` not `WORKSPACE_STATE_PENDING`; existing `responses.values` entry whose build is not terminal. |
| **Returns** | `outcome: "accepted"`, `operation: "build"`, `done: false`, `workspace_id`, `request_id`. HTTP 200 ≠ success. `next_action` → Phase 1 poll. |

On this tenant `responses.values` is keyed by `request_id` ≈ `buildId`. Confirm
`WorkspaceBuild.key.buildId`.

### `assign_cvp_studio_tags` (2.1)

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/AssignedTagsConfig` |
| **Parameters** | `studio_id`, `workspace_id`, `query`, `expected_current_query: str` (**required**; `""` is valid = unassigned), `confirm` |
| **Replace** | Whole query. Empty **new** query is **forbidden** in 2.1 (no unassign-all). |
| **Preflight** | Overlay-then-mainline (draft overlay if present, else mainline `""`). Omitted/`None`/non-str `expected_current_query` → `expected_current_query_required`. Mismatch vs resolved current → `current_query_mismatch`. Dry-run: previous query; device preview or `target_preview_unresolved`. |

### `submit_cvp_workspace`

Retired 2026-09-02. Never registered; library removed. The MCP stops at build;
the human reviews and submits the workspace in the CVP UI. See
`docs/studios-phase2-final-spec.md` §A.

### `create_cvp_studio` / `delete_cvp_studio` (2.2)

Upsert StudioConfig / `remove: true`. Refuse `immutable` / `from_package`.
MCP studio templates **must never** contain interface `shutdown` / `no shutdown`.
No `allow_disruptive` exception. Also refuse `no interface`, `reload`,
`write erase`. Prefer inputs on existing studios. Delete sequence: unassign tags
→ remove studio in the **same** workspace → build → human review and submit in the CVP UI.

## Caller inputs

| Step | Need | From |
| --- | --- | --- |
| Studio | id + flags | `get_cvp_studios` / `get_cvp_studio` |
| Inputs | document + `path.values` | `get_cvp_studio_inputs` + path fixture |
| Tags | query | `get_cvp_studio_assigned_tags` |
| Workspace | unique id | caller; GET uniqueness |
| Build | `request_id` | UUIDv4 if omitted |

## Files (when implementing 2.0)

| File | Role |
| --- | --- |
| `cvp_mcp/grpc/studios_write.py` | Helper + allowlists |
| `cvp_mcp/write_access.py` | Env + registry |
| `cloudvision_mcp.py` | Register 2.0 writes only if writes env is `"1"` |
| `tests/test_studios_write.py` | Helper unit tests without HTTP |
| `tests/fixtures/inputs_ethernet6_720xp24_locator.json` | Live locator + `adapterDetails` excerpt |
| `tests/fixtures/workspace_config_*.json` | POST bodies |

No mutate logic in `studios.py`.

## Testing

Helper (no HTTP):

- Disallowed path, `REQUEST_ROLLBACK`, empty `workspaceId` → no request built.
- InputsConfig body may contain `"change"` inside the inputs string; helper must
  **not** reject it.
- WorkspaceConfig top-level `start` → reject.

Tools:

- `confirm=False` → no mutate HTTP.
- Writes unregistered unless `CLOUDVISION_MCP_ALLOW_WRITES=="1"`.
- 2.1 generic Inputs: empty `path_values` → `root_path_forbidden`. 2.0 description CAS **must** POST `path.values: []` for this studio.
- Delete non-`WORKSPACE_STATE_PENDING` → refuse.
- Lint: interface `shutdown` in template/inputs.
- Staging: create → description CAS → build → delete workspace, no submit; ids `ws-mcp-test-*`.
- `expected_current_description` mismatch → `current_description_mismatch`, no POST.
- Mutate with missing/mismatched `preview_token` → `preview_required`, no HTTP.
- `REQUEST_SUBMIT` on a WorkspaceConfig body → `request_not_allowed`, no request built.
- `adapterDetails.enabled: false` on generic Inputs → `input_key_not_allowed`, no HTTP.

## Open (blocking)

`GET AssignedTags/all` is **closed**: HTTP 200 on this tenant; no-row is `query=""`.

| Item | Blocks |
| --- | --- |
| InputsConfig POST of a patched root tree | **Closed 2026-09-02:** live §B verified on deployed image (operator notes outside this repo) |
| Full `Workspace.Request` protobuf enum | **Closed 2026-09-02:** submit retired; allowlist is `{REQUEST_START_BUILD}` |
| Workspace `last_modified_at` | **Closed 2026-09-02:** submit retired; no staleness proof needed |
| Tag query → device serial preview | 2.1 dry-run warning only |
| Server reject of mainline `workspaceId=""` on InputsConfig | Client already refuses; probe optional |
| CC auto-approve/execute | **Closed 2026-08-20:** pending; human execute |

## Homelab policy

- Designed config: Studios → Workspace → review → CC → **human** approve/execute.
- MCP drafts. It does not silently push.
- Never `shutdown` a port unless the user named **that** device and interface.
- DNS for `freeblizz.com` is OPNsense. `pi5 - dns` is a label only.

## Inventory

| Tool | Slice |
| --- | --- |
| Phase 1 eight reads | shipped |
| `get_cvp_studio_assigned_tags` | 2.0 optional read (URL probed; no-row is `query=""`) |
| `create_cvp_workspace` | 2.0 |
| `delete_cvp_workspace` | 2.0 |
| `set_cvp_access_interface_description` | 2.0 (path fixture required) |
| `set_cvp_studio_inputs` | 2.1 generic; no root replace |
| `build_cvp_workspace` | 2.0 |
| `assign_cvp_studio_tags` | 2.1 |
| `submit_cvp_workspace` | retired 2026-09-02 (never registered; library removed) |
| `create_cvp_studio` / `delete_cvp_studio` | 2.2 |
| `set_cvp_mss_policy_inputs` | 2.3 (`docs/studios-phase2-final-spec.md`) |

No ChangeControlConfig. No configlets.
