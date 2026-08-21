# Spec: Studios and designed-config support in cloudvision-mcp

Status: proposed (revised 2026-08-19 after multi-model review). Written 2026-07-25;
**re-verified 2026-08-19** against live CVP with the MCP container service-account token.
Review findings: `docs/research/studios-support-review-synthesis.md`.

## Why

The server currently exposes telemetry and inventory. Operators still need **device-level
designed-config provenance**: which **studios** (and, later, configlets) contribute to a
device’s designed config. That came up while tracing a duplicated `logging host` statement
on 720xp-24; answering it required hand-rolled curl against CloudVision.

**Phase 1 answers**

- Which studios are assigned / contribute to a device (`get_cvp_designed_config` →
  `sources` with `CONFIG_TYPE_STUDIO`).
- Which studio **source templates / schemas** mention a substring
  (`search_cvp_studio_templates`), with JSON paths so UI labels are not mistaken for CLI.
- Workspace and build status so a human can review before any later submit.

**Phase 1 does not answer** per-line attribution (“this exact CLI line came from studio X
line N”). Template search is **not** a substitute for designed-config provenance: searching
`logging` can hit EOS Event Handler **input-schema labels** while no studio template emits
`logging host`. Configlet provenance is **out of scope for v1** (endpoints exist; no MCP
tools yet). Line-level merge of studio + configlet + CLI remains a later phase.

A second motivation: `get_cvp_device_config` tries configstatus first (403 on this
homelab’s Resource API), then falls back to compliance GetConfig for **running** config.
Designed-config read must use the same compliance RPC with `type` parameterized
(`DESIGNED_CONFIG`), not configstatus URIs. **`get_config` now accepts `config_type=`**
(`RUNNING_CONFIG` default). Use `extract_designed_sources` for provenance.

## Implementation phases

| Phase | Scope | Risk | Ship when |
| --- | --- | --- | --- |
| **1 — Read** | Studios, inputs, workspaces, template search, designed-config **studio** provenance, workspace/build status | Low | **First** (after contracts below) |
| **2 — Write** | Workspace/studio/inputs/tag CRUD, build, opt-in submit | **High** | **Not in the first MCP release.** Implement only after Phase 1 ships **and** an explicit homelab decision to enable writes. |

Phase 2 REST is **API-feasible today** (homelab token verified 2026-08-19). That does **not**
mean the write tools as originally drafted are safe to ship. See **Phase 2 — ship decision**.

## Verified environment facts

These were confirmed empirically. Several contradict reasonable assumptions.

### Dual APIs for device config (not a missing role checkbox)

Do **not** chase IAM / role-editor “config read” toggles for the configstatus 403.

CloudVision exposes **two** config surfaces:

| Surface | Homelab result (2026-08-19) | Use for |
| --- | --- | --- |
| **configstatus** Resource API + gRPC | **403 / PERMISSION_DENIED** | Not usable with this token |
| **compliancecheck GetConfig** | **200** | Running and designed config |

`network-admin` on a **human** user does not apply to the MCP **service account**. The
container JWT (`sid=019d4bab-9d59-7376-9ed2-fd66a69748a3`, org `chines-lab`) has no
embedded role name; permissions are evaluated server-side. Re-checked 2026-08-19:

| API family | Result | Notes |
| --- | --- | --- |
| inventory, studio, workspace, changecontrol (read) | **200** | Auth OK |
| studio/workspace/changecontrol (write probes) | **200** | See write section |
| **configstatus** REST + gRPC | **403 / PERMISSION_DENIED** | Separate Resource API auth boundary on this staging instance |
| **compliancecheck GetConfig** | **200** | `RUNNING_CONFIG` and `DESIGNED_CONFIG` for EOS serials |

Running config for serial `JPE19151499` (720xp-24) was ~19 KB. Hostname lookups can **504**;
prefer **device serial**.

**Two different tokens exist and only one works.**

| Source | Length | Works |
| --- | --- | --- |
| `~/.env` `CVPTOKEN` on the workstation | 1031 chars | no, 401 on every endpoint |
| `CVPTOKEN` in the `cloudvision-mcp-app` container | 398 chars | yes |

The 1031-char token is a valid unexpired JWT (`dsn=cvpadmin`, `exp=2029-06-03`) and is
rejected everywhere, including gRPC via grpcurl. Do not debug auth against the
workstation `~/.env` value. Read the container's env instead:

```
sudo podman exec cloudvision-mcp-app printenv CVPTOKEN
sudo podman exec cloudvision-mcp-app printenv CVP
```

**Endpoint base and auth.** `CVP=www.cv-staging.corp.arista.io`, and requests use
`Authorization: Bearer <token>`. Note `apiserver.cv-staging.corp.arista.io` from the
switches' TerminAttr config is the gRPC telemetry ingest address, not the REST API host.

**Studio vs StudioConfig (verified 2026-08-21):**

| Access | Mainline `workspaceId=""` | Notes |
| --- | --- | --- |
| `GET Studio?key.studioId=&key.workspaceId=` | **200** | Includes `template` + `inputSchema` |
| `GET StudioConfig?key…&key.workspaceId=` | **404** | `studio not found` |
| `GET Studio/all` | includes empty-`workspaceId` rows | List + search source for **mainline** |
| `GET StudioConfig/all` | **0** empty-`workspaceId` rows (345 nonempty) | Package/workspace copies only |

**Implication:** `search_cvp_studio_templates` walks **`Studio/all`** (or keyed `Studio` GETs), **not** `StudioConfig/all`. The old worked example that implied mainline bodies came from `StudioConfig/all` was wrong; EOS Event Handler mainline is readable via keyed `Studio` (`logging` appears in that body). `StudioConfig` keyed GETs work for **non-empty** package workspace ids (e.g. `eos-event-handler-pkg-v0.0.x-…`).

### Resource API `/all` NDJSON (phase 1 implementer rules)

`/all` endpoints stream newline-delimited JSON, one object per line, not a JSON array.
Example line:

```json
{"result":{"value":{"key":{"studioId":"..."},"displayName":"...","template":{...}},"time":"...","type":"INITIAL"}}
```

1. GET the URI with bearer + host allowlist; read the **full** body. Studios Phase 1
   uses a **96 MiB** cap (live `Studio/all` on this tenant was ~75 MiB on 2026-08-21;
   a 32 MiB cap truncated before mainline `workspaceId=""` rows).
2. Split on newlines. Skip empty / whitespace-only lines.
3. If the body starts with `)]}'`, drop that XSSI prefix line (same as existing URI fetch).
4. `json.loads` **each** line. Skip decode failures; increment `warnings` count
   `ndjson_skip_invalid_line`.
5. Yield `row = obj.get("result", {}).get("value")`. Skip if `value` is missing. Do **not**
   require `displayName`.
6. Dedupe by resource key (`studioId`+`workspaceId` or equivalent): **last occurrence wins**.
7. Do **not** call `get_json_with_bearer` for `/all` (first-line / first-object fallback).
   Use `get_ndjson_all_values_with_bearer` (default / studios callers: `max_bytes=96_000_000`).
8. Unit fixtures: blank trailing line; value without `displayName`; two lines same `studioId`
   (update wins).

### Envelope and registration (all new tools)

Each tool returns `tool_envelope(...)` only. List tools use `items`; singletons use
`object`. Never add parallel top-level keys besides envelope fields (`collected_at`,
`coverage`, `warnings`, `data_source`, `device_id` when applicable).

Register in `cloudvision_mcp.py` with `@mcp.tool()` and `@tool_enabled("<same_name>")`.
Today `tool_enabled(tool_name)` only honors `CVP_MCP_DISABLED_TOOLS` at **call time**; it
does **not** take `writes=True`. Phase 1 uses that decorator as-is. Phase 2 adds a
**separate** write registry gate (below), not a fictional `writes=` argument on the
current decorator.

Reuse `fetch_uri_with_bearer` for raw/keyed single-object GET; use
`get_ndjson_all_values_with_bearer` for `/all`. Do not use `get_json_with_bearer` on
`/all` streams.

**Helpers landed (2026-08-21):** `get_config(..., config_type=)` and
`extract_designed_sources` / `studio_keys_from_sources` in `config_async_flow.py`;
`get_ndjson_all_values_with_bearer` in `uri_fetch.py`. Fixture-backed unit tests cover both.

## Endpoint access matrix

Confirmed by probing with the container token on **2026-08-19** (supersedes 2026-07-25
where noted).

| Endpoint | Status | Notes |
| --- | --- | --- |
| `/api/resources/inventory/v1/Device/all` | 200 | |
| `/api/resources/studio/v1/Studio/all` | 200 | |
| `/api/resources/studio/v1/StudioConfig/all` | 200 | |
| `/api/resources/studio/v1/Inputs/all` | 200 | Was uncertain in July probe |
| `/api/resources/workspace/v1/Workspace/all` | 200 | |
| `/api/resources/workspace/v1/WorkspaceConfig/all` | 200 | |
| `/api/resources/configlet/v1/Configlet/all` | 200 | **No Phase 1 tool** (out of Why v1) |
| `/api/resources/configlet/v1/ConfigletConfig/all` | 400 | `nil workspace key` — use keyed GET, not `/all` |
| `/api/resources/changecontrol/v1/ChangeControl/all` | 200 | Read-only if needed later; **no Phase 2 CC write tools** |
| `/api/resources/configstatus/v1/ConfigDiff/all` | 403 | `user not authorized` |
| `/api/resources/configstatus/v1/Configuration?key.deviceId=<serial>` | 403 | same |
| gRPC `configstatus` Summary/Configuration GetOne | PERMISSION_DENIED | same principal as REST |

**Captured 2026-08-21** (see fixtures; no further probes required for these rows):

| Item | Result |
| --- | --- |
| Mainline `workspaceId` | `""` |
| Keyed Studio vs StudioConfig | **Studio** 200; StudioConfig keyed mainline **404** |
| `StudioConfig/all` mainline rows | **None** (empty `workspaceId` count = 0) |
| `GET Workspace` / `WorkspaceBuild` | Sample bodies in `tests/fixtures/workspace_*_sample.json` |
| Build / workspace / response enums | `tests/fixtures/workspace_build_enums.json` |
| Inputs shape | `key` includes `path: {}`; `inputs` is a JSON **string**; v1 read = filter `Inputs/all` |

### Write access (same token, 2026-08-19)

Minimal POST/DELETE probes (no production config changes retained):

| Operation | Endpoint | Status |
| --- | --- | --- |
| Create workspace | `POST .../workspace/v1/WorkspaceConfig` | 200 |
| Delete workspace | `DELETE .../workspace/v1/WorkspaceConfig?key.workspaceId=...` | 200 |
| Start build | `POST .../WorkspaceConfig` + `REQUEST_START_BUILD` | 200 |
| Submit workspace | `POST .../WorkspaceConfig` + `REQUEST_SUBMIT` | 200 (async; **accepted**, not done) |
| Set studio inputs | `POST .../studio/v1/InputsConfig` | 200 |
| Assign studio tags | `POST .../studio/v1/AssignedTagsConfig` | 200 |
| Create change control shell | `POST .../changecontrol/v1/ChangeControlConfig` | 200 — **same endpoint can `start` a CC**; do not wrap in MCP v1 |
| Delete change control | `DELETE .../changecontrol/v1/ChangeControlConfig?key.id=...` | 200 |
| Delete/modify studio | `POST .../studio/v1/StudioConfig` (`remove: true`) | 404 if studio missing; **not 403** |

**Conclusion:** This service account can **provision** (studios/workspaces/change controls)
and **read config via compliance GetConfig**, but not **configstatus Resource API**.
Phase 1 prefers compliance for device config. Phase 2, **if** enabled later, targets
provisioning endpoints below — **excluding** ChangeControlConfig writes.

Official REST write flow documented by Arista:
[Studios and Workspaces REST examples](https://aristanetworks.github.io/cloudvision-apis/examples/REST/studios%20and%20workspaces)

## Phase 1 — Read tools

### Compliance GetConfig (shared contract)

**Live `DESIGNED_CONFIG` wire fixture (2026-08-21):**
`tests/fixtures/designed_config_response_720xp24.json` — literal JSON **array** response
(config strings truncated). Analyst summary (optional): 
`tests/fixtures/designed_config_sources_720xp24.json`.

Important wire facts (diffed against raw capture):

- HTTP 200 body is a **JSON array** of message objects (not a single object, not NDJSON).
- One message is `{"sources":{"source":[...]}}`; a later message is `{"config":"<designed CLI>"}`.
- Each source entry uses **snake_case** on this RPC:
  `{"source_type":"CONFIG_TYPE_STUDIO"|"CONFIG_TYPE_STUDIO_STATIC","key":"<studioId string>"}`.
  Field name is `source_type` (not `sourceType`); `key` is a **string**, not a nested object.
- Observed on 720xp-24: both `CONFIG_TYPE_STUDIO` and `CONFIG_TYPE_STUDIO_STATIC`.

```json
[
  {
    "sources": {
      "source": [
        {"source_type": "CONFIG_TYPE_STUDIO", "key": "studio-authentication"},
        {"source_type": "CONFIG_TYPE_STUDIO_STATIC", "key": "avd-JPE19151499"}
      ]
    }
  },
  {"config": "<designed CLI text>"}
]
```

Normalize via `extract_designed_sources` / `studio_keys_from_sources` (dedupe, preserve order).

Existing running-config code POSTs to
`/api/v3/services/compliancecheck.Compliance/GetConfig`. **`get_config` now accepts
`config_type=`** (`RUNNING_CONFIG` default | `DESIGNED_CONFIG`). Do not add a second POST
client.

#### `get_cvp_designed_config`

| | |
| --- | --- |
| **Endpoint** | `POST /api/v3/services/compliancecheck.Compliance/GetConfig` |
| **Parameters** | `device_id: str` (serial preferred). Hostname/FQDN: resolve via inventory first; on 504 hostname lookup, caller must pass serial. |
| **Body** | `{ "request": { "device_id": "<serial>", "timestamp": "<RFC3339 UTC>", "type": "DESIGNED_CONFIG" } }` |
| **Reuse** | Same host allowlist + bearer as running-config GetConfig. Retry 502/503/504. |
| **Parse** | Decode as a **JSON array** of messages (live shape). Collect `sources` from any message that has them; designed text from a message with top-level `config` (string). Record `warnings` if either half is missing. Do not assume a single object. |
| **Normalize `sources`** | Each element: `source_type` (`CONFIG_TYPE_STUDIO` or `CONFIG_TYPE_STUDIO_STATIC`), `key` as **string** studio id. Derive `studio_keys: list[str]` from those strings (dedupe, preserve order). |
| **Envelope** | `tool_envelope(device_id=<serial>, data_source="service_api:compliancecheck.getconfig", coverage=full\|partial\|none, obj={sources, studio_keys, designed_config_text?}, warnings=...)`. Put payload **inside `object`**. |
| **Do not** | Use configstatus Resource API URIs. Claim per-line CLI attribution. |

Optional later: diff running vs designed by calling GetConfig twice with different `type`
and merging in the client — **not** in v1.

#### `get_cvp_studios`

| | |
| --- | --- |
| **Endpoint** | `GET /api/resources/studio/v1/Studio/all` |
| **Parameters** | none in v1 (optional later: `workspace_id`) |
| **Returns** | `tool_envelope(items=[{studio_id, workspace_id, display_name, description, created_by, last_modified_at, template_type, immutable, from_package, in_use}], data_source="resource_api:studio.v1")` |
| **Omit** | `template` body / large nested template object |
| **Parse** | Full-stream NDJSON rules above |

`immutable` / `from_package` / `in_use` come from `StudioSummary` when present — prefer
these over name regex for later write refusals.

#### `get_cvp_studio`

| | |
| --- | --- |
| **Endpoint** | `GET /api/resources/studio/v1/Studio?key.studioId=&key.workspaceId=` (**confirmed** — do not use StudioConfig for mainline keyed get; it 404s) |
| **Parameters** | `studio_id: str`, `workspace_id: str \| None = None`, `body: bool = False` |
| **Mainline** | When `workspace_id` is `None`, use `""` (empty string). Verified 2026-08-21; see `tests/fixtures/workspace_build_enums.json`. |
| **When body=False** | Omit template source; return `template_bytes`, `template_sha256` (hex SHA-256 of UTF-8 Mako source). Always return `input_schema` field names so Phase 2 callers can draft inputs. |
| **When body=True** | Include full Mako; warn if payload > 100 KB. |

#### `get_cvp_studio_inputs`

Current **instance** values (schema ≠ values). Required so later writes are not guessed.

| | |
| --- | --- |
| **Endpoint (v1)** | `GET /api/resources/studio/v1/Inputs/all` then **client-filter** by `studio_id` + `workspace_id` (and optional path) |
| **Why not keyed GET** | Keyed `GET .../Inputs?key.studioId=&key.workspaceId=` returns **400** `path cannot be nil`. Live keys include `"path": {}`. Encoding an empty path for keyed GET is unresolved (`key.path.values=` → 404). Ship filter-`/all` first. |
| **Parameters** | `studio_id: str`, `workspace_id: str \| None = None` (mainline default `""`) |
| **Wire** | `inputs` is a **JSON string** on the resource; parse once to an object for the envelope |
| **Returns** | Prefer `items[]` when multiple path rows match; if only the root `path: {}` row exists, still return `items` of length 1. Each item: `{studio_id, workspace_id, path_values, inputs}` |
| **Fixture** | `tests/fixtures/inputs_mainline_topology_sample.json` (truncated) |

#### `search_cvp_studio_templates`

Finds studios whose **source** (template body and/or input schema) mentions a pattern.
**Not** a substitute for designed-config provenance.

| | |
| --- | --- |
| **Endpoint** | `GET /api/resources/studio/v1/Studio/all` (mainline + others). **Do not** use `StudioConfig/all` for mainline search — that stream has **no** empty-`workspaceId` rows on this tenant. |
| **Parameters** | `pattern: str` (literal substring, case-sensitive v1), `include_input_schema: bool = True`, `max_hits: int = 100` |
| **Walk** | Recursive strings on parsed `result.value` via NDJSON helper; `json_path` uses dotted keys + `[n]` for lists. |
| **Returns** | `items[]`: `{studio_id, workspace_id, display_name, json_path, snippet, in_template: bool}` |

Worked example (re-verified 2026-08-21): keyed mainline
`Studio?key.studioId=studio-eos-event-handler-pkg&key.workspaceId=` returns display name
*EOS Event Handler* and the body contains `logging` (often in input-schema labels). Use
JSON paths so UI labels are not mistaken for rendered CLI.

#### `get_cvp_workspaces`

| | |
| --- | --- |
| **Endpoint** | `GET /api/resources/workspace/v1/Workspace/all` |
| **Parameters** | none in v1 |
| **Returns** | `items[]` with snake_case `state`, `cc_ids` (from `ccIds`), and **build id / request id** fields as present — not full nested build blobs |

#### `get_cvp_workspace` / `get_cvp_workspace_build`

| | |
| --- | --- |
| **Endpoints** | `GET /api/resources/workspace/v1/Workspace?key.workspaceId=` and `GET /api/resources/workspace/v1/WorkspaceBuild?key.workspaceId=&key.buildId=` |
| **Parameters** | `workspace_id: str`; build tool also `build_id: str` |
| **Returns** | Workspace: state, `cc_ids`, build responses mapping **as live JSON**. Build: `state`, `errors`, timestamps / version for proof-of-review |

### Workspace build poll (phase 1; used by humans and later Phase 2)

Live enums and mapping: `tests/fixtures/workspace_build_enums.json` (2026-08-21).

`build_cvp_workspace` (Phase 2) returns `request_id` only. On this tenant,
`Workspace.responses.values` is keyed by that `request_id`, and build success messages
are of the form `Build <uuid> finished successfully` where `<uuid>` equals the map key —
so **`request_id` is typically also `buildId`**. Confirm by reading
`WorkspaceBuild.key.buildId` after the response appears; do not invent a second id.

After `REQUEST_START_BUILD`:

1. Poll `get_cvp_workspace(workspace_id)` until `responses.values[<request_id>]` is
   present **or** 30s timeout.
2. Poll `get_cvp_workspace_build(workspace_id, build_id=request_id)` until `state` is
   terminal:
   - **Success:** `BUILD_STATE_SUCCESS`
   - **Terminal failure:** `BUILD_STATE_FAIL`, `BUILD_STATE_CANCELED`
   - **Non-terminal (keep polling):** `BUILD_STATE_IN_PROGRESS` (protobuf; not seen in
     historical `/all` dump), plus any unknown value → warn and keep polling until timeout
3. Do not call `submit_cvp_workspace` unless state is `BUILD_STATE_SUCCESS` and build
   `error` is empty.

Observed companion enums (not build state, but useful for envelopes):

- Workspace: `WORKSPACE_STATE_PENDING`, `WORKSPACE_STATE_SUBMITTED`, `WORKSPACE_STATE_ABANDONED`
- Response: `RESPONSE_STATUS_SUCCESS`, `RESPONSE_STATUS_FAIL`

Poll cadence: 2s, timeout 120s for a typical small workspace.
Polling is **only** in read tools / the agent loop — never inside a write tool.

---

## Phase 2 — Write tools

> **Design only / gated.** Phase 2 does **not** ship in the first MCP release. Tables below
> are the allowed design if writes are later enabled — not an implementation checklist for
> the Phase 1 PR.

### Ship decision

**Resolved:** Phase 2 write tools **do not ship in the first MCP release.** Phase 1 is
the product until a human explicitly opts the homelab server into writes.

This section is the **only allowed write design** if/when that opt-in happens. It is
not an invitation to implement writes in the same PR as Phase 1.

**Homelab policy:** designed config changes belong in CVP Studios → Workspace → review →
Change Control → approve/execute. Agents must not silently push config. Write tools exist
for **drafting** workspace content; submit requires a **separate** env gate plus proof of
a successful reviewed build.

**Verified on this CVaaS instance (2026-08-20):** workspace submit creates change controls
that sit in **pending approval**. They do **not** auto-approve or auto-execute. Running
config stays unchanged until a human approves/executes in the CVP UI — required policy for
homelab MCP (human in the loop). Re-check this if tenant General / Change Control settings
change.

### Global write gates (all phase 2 tools)

`tool_enabled` today cannot implement `@tool_enabled(..., writes=True)`. Specify:

1. `CLOUDVISION_MCP_ALLOW_WRITES` is on only when its stripped value is exactly `"1"`.
   Unset, empty, `"0"`, `"true"`, `"yes"`, and every other value are off.
2. Evaluate that variable when constructing the tool registry. When off, **register no**
   Phase 2 write tools (undiscoverable).
3. Check the same gate immediately before any mutating HTTP request. If off, return
   `error="writes_disabled"` and do not POST/DELETE.
4. `CLOUDVISION_MCP_ALLOW_SUBMIT` is a **second** env var, exact `"1"`, default off even
   when writes are on. Submit is unregistered unless both env vars are `"1"`. Runtime
   miss → `error="submit_disabled"`.
5. `CVP_MCP_DISABLED_TOOLS` remains an independent deny list. A tool must pass all gates.
6. `confirm: bool = False` on every write. `confirm=False` → dry-run preview only
   (argument validation + documented read-only preflights; **no** POST/DELETE).
7. No compound tools: never create → inputs → build → submit in one MCP invocation.
8. Audit log (INFO): tool name, `workspace_id`, `studio_id`, `request_id`, outcome.
   **Never** log token, Authorization, full inputs, template body, or input schema.
9. **Non-empty `workspace_id` on every write.** After `strip()`, refuse empty
   (`error="workspace_id_required"`). `""` is **mainline**; MCP never writes to mainline
   directly (client-side control; server behaviour for mainline InputsConfig writes is
   unverified — refuse either way).
10. **`^builtin-` denylist on every write**, not only delete.

**Hard-coded `request` enum per tool.** Never pass `Workspace.Request` through from the
model. Do **not** expose `REQUEST_SUBMIT_FORCE` or `REQUEST_ROLLBACK`. Do **not** send
`start` / `schedule` on ChangeControlConfig (CC write tools are out of v1 entirely).

### Field-level dangers (write helper contract)

`post_resource_config(path, body)` MUST enforce, independently of any caller:

1. **Path allowlist** — exactly
   `{workspace/v1/WorkspaceConfig, studio/v1/InputsConfig, studio/v1/AssignedTagsConfig,
   studio/v1/StudioConfig}`. Any other path raises before the request is built.
   `changecontrol/*` and `configlet/*` are not on the list in v1.
2. **`request` allowlist** — if the body contains a `request` key, its value must be in
   `{REQUEST_START_BUILD, REQUEST_SUBMIT}`. Reject every other value, including
   `REQUEST_SUBMIT_FORCE` and `REQUEST_ROLLBACK`.
3. **Body key denylist** — reject any body containing `start`, `schedule`, or `change` at
   any depth.
4. **Non-empty `key.workspace_id`** — same as global gate 9.

These are backstops. Each tool still constructs its own literal enum.

POST bodies accept **snake_case or camelCase** keys; responses are **camelCase** (Arista).
Shared helper: `post_resource_config(path, body) -> dict` using existing bearer + host
allowlist **plus** the field-level contract above; parse a single JSON object (writes are
not NDJSON `/all`). Prefer `StudioSummary.immutable` / `from_package` for studio refusals.

Dry-run precedence:

1. Writes disabled → tools undiscoverable; runtime backstop `writes_disabled` even if
   `confirm=False`.
2. Writes enabled, `confirm=False` → preview only.
3. `confirm=True` → exactly one mutating HTTP call (unless a documented preflight fails).

### Canonical provisioning workflow

Arista’s required sequence for config-impacting changes:

```text
1. create_cvp_workspace
2. set_cvp_studio_inputs        (per studio; see replace semantics)
3. assign_cvp_studio_tags       (per studio; see replace semantics)
4. build_cvp_workspace          (REQUEST_START_BUILD; poll Phase 1 read tools)
5. [human review in CVP UI]
6. submit_cvp_workspace         (REQUEST_SUBMIT; only if ALLOW_SUBMIT=1 + proof-of-review)
7. [human approve/execute CC in CVP UI — never MCP]
```

Submit updates **mainline designed config** immediately even when no device CC runs.
“Running config unchanged until CC execute” is **not** “submit is harmless.”

Deleting a studio from mainline uses the same workspace pattern: create workspace →
unassign tags (`query: ""`, destructive) → `remove: true` on StudioConfig → build →
submit.

### Tool reference (parameters, API, response)

#### `create_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Parameters** | `workspace_id: str`, `display_name: str`, `description: str = ""`, `confirm: bool = False` |
| **Body** | `{"key":{"workspace_id":"..."},"display_name":"...","description":"..."}` |
| **Preflight** | Strip ID; reject empty and `^builtin-`. Keyed GET: if exists → `error="workspace_id_exists"` (no POST). If the read fails, **fail closed** (no POST). Dry-run may perform this GET. Server-side conflict after a clean preflight is a race — do not overwrite or invent a new id. |
| **Recommended id** | `ws-mcp-<purpose>-<YYYYMMDD>-<uuid8>` (caller-supplied for audit) |
| **Returns** | `workspace_id`, `display_name`, `time`, `dry_run` preview when `confirm=False` |

#### `delete_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `DELETE /api/resources/workspace/v1/WorkspaceConfig?key.workspaceId=<id>` |
| **Parameters** | `workspace_id: str`, `confirm: bool = False` |
| **Notes** | Refuse `^builtin-`. Verify the workspace exists and is an eligible **draft** (not submitted/mainline). Existence failure → no DELETE. |

#### `set_cvp_studio_inputs`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/InputsConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `inputs: dict`, `path_values: list[str] \| None = None`, `replace_all_inputs: bool = False`, `confirm: bool = False` |
| **Body** | `{"key":{"studio_id","workspace_id","path":{"values": <path_values or []>}},"inputs":"<JSON string>"}` |
| **Serialize** | `inputs` is a **JSON-encoded string** on the wire. Accept **dict only**; `json.dumps` **once**. Never `path_values: list = []` as a Python default. |
| **Refuse** | Empty/`None` `path_values` without `replace_all_inputs=True` → `error="root_path_requires_replace_all_inputs"`. Empty `workspace_id` → `workspace_id_required`. |
| **Path syntax** | e.g. `["ntpServers", "[ip=10.10.10.10]", "vrf"]` per studio.v1; match against `get_cvp_studio` schema. |
| **Replace semantics (critical)** | Higher paths overwrite lower. Root (`values: []`) wipes the tree. Prefer read-modify-write via `get_cvp_studio_inputs` with before/after in dry-run. |
| **Caller must supply** | Schema from `get_cvp_studio` **and** current values from `get_cvp_studio_inputs`. Wrong shape fails at **build** time, not POST time. |
| **Returns** | `studio_id`, `workspace_id`, `path_values`, `full_tree_replace: bool`, `time` |

Refuse writes into `immutable` / `from_package` studios. Lint `inputs` for the same
disruptive EOS primitives as templates (`create_cvp_studio`) — preferred path is inputs/tags.

#### `assign_cvp_studio_tags`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/AssignedTagsConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `query: str`, `confirm: bool = False`, `unassign_all: bool = False`, `expected_current_query: str \| None = None` |
| **Body** | `{"key":{"studio_id","workspace_id"},"query":"datacenter:NY"}` |
| **Replace semantics (critical)** | `query` replaces the entire assignment. Empty query requires `unassign_all=True` or refuse. |
| **Preflight** | Add Phase 1 `get_cvp_studio_assigned_tags` before this write ships. If `expected_current_query` is set, refuse on mismatch. Dry-run shows previous query + device preview when resolvable; else `target_preview_unresolved` warning. |
| **Returns** | `studio_id`, `workspace_id`, `query`, `target_device_ids` (nullable), `time` |

#### `build_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Parameters** | `workspace_id: str`, `request_id: str \| None = None`, `confirm: bool = False` |
| **Body** | `{"key":{"workspace_id":"..."},"request":"REQUEST_START_BUILD","request_params":{"request_id":"<uuid>"}}` |
| **request_id** | If `None`, generate UUIDv4 **once** per invocation; use the same id in preview, POST, and response. Reject blank caller ids. Do **not** default `"b1"`. Retries get a new id unless the caller deliberately reuses one. |
| **Returns** | `outcome: "accepted"`, `operation: "build"`, `done: false`, `workspace_id`, `request_id`, `time`. HTTP 200 is **not** build success. **Do not poll inside this tool.** `next_action` points at Phase 1 poll tools. |

Refuse if workspace missing, builtin, or a build is already in progress (once live states are known).

#### `submit_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Body** | `{"key":{"workspace_id":"..."},"request":"REQUEST_SUBMIT","request_params":{"request_id":"<uuid>"}}` only — never `REQUEST_SUBMIT_FORCE` |
| **Parameters** | `workspace_id: str`, `request_id: str \| None = None`, `confirm: bool = False`, `allow_submit: bool = False`, `build_id: str`, `build_proof: str` |
| **Gates** | Env `CLOUDVISION_MCP_ALLOW_WRITES=1` **and** `CLOUDVISION_MCP_ALLOW_SUBMIT=1`; `confirm=True`; `allow_submit=True`; proof-of-review (below). |
| **Proof-of-review** | Caller passes `build_id` plus a **workspace** staleness token from `get_cvp_workspace` after review (`lastModifiedAt` or equivalent — **not** a field from the immutable `WorkspaceBuild` record alone; re-fetching a terminal build always matches). Tool re-fetches workspace + build; refuse if build not `BUILD_STATE_SUCCESS`, workspace token mismatch, or contents changed after that build. Until a live staleness field is confirmed, keep submit **unregistered** even when both env vars are set. |
| **Returns** | `outcome: "accepted"`, `operation: "submit"`, `done: false`, `workspace_id`, `request_id`, `cc_ids: null` unless IDs are in the **immediate** POST body, `next_action` telling the caller to poll `get_cvp_workspace` until a **terminal submit state**. Empty `ccIds` before terminal means **unknown**, not “no CC.” HTTP 200 must **never** be `outcome: "succeeded"`. **Do not poll** inside this tool. Do not create/approve/execute a CC. |

#### `create_cvp_studio`

This is an **upsert**. POSTing `StudioConfig` for an existing `studio_id` copies mainline
into the workspace and **replaces** `template` / `input_schema`.

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/StudioConfig` |
| **Parameters** | `studio_id`, `workspace_id`, `display_name`, `description`, `template_body`, `input_schema`, `confirm`, `overwrite_existing: bool = False` |
| **Preflight** | If studio exists in mainline and `overwrite_existing` is false → refuse. Refuse `immutable` / `from_package`. |
| **Template lint** | `template_body` is unrestricted Mako → EOS. Lint for disruptive primitives (`shutdown` under an interface, `no interface`, `reload`, `write erase`, `no ip routing`, management/uplink edits). Refuse unless `allow_disruptive` names the **specific interfaces** (same bar as homelab EOS safety rules). Surface matching lines in dry-run. |
| **Caller must supply** | Complete `input_schema` graph. Prefer inputs/tags on existing studios over creating templates from MCP. |
| **Returns** | `studio_id`, `workspace_id`, `time` |

#### `delete_cvp_studio`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/StudioConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `confirm: bool = False` |
| **Body** | `{"key":{"studio_id","workspace_id"},"remove": true}` |
| **Prerequisite** | Unassign tags first (`query=""`). Then build + submit. Refuse `immutable` / `from_package`. |

#### Change control tools — **out of v1**

Do **not** implement `create_cvp_change_control` or `delete_cvp_change_control`.

`POST /api/resources/changecontrol/v1/ChangeControlConfig` is documented for **starting**
a change control via `{"start":{"value":true}}`. `change.stages` can hold arbitrary device
tasks. Approve is a separate resource; **execute is not a separate URL**. Field validation
is the only exclusion, and that is too easy to get wrong. Studios submit already creates
CCs when device CLI is needed. Humans execute in the CVP UI.

### Phase 2 information callers must provide

| Step | Information needed | How to obtain (phase 1 tools) |
| --- | --- | --- |
| Pick studio | `studio_id`, immutable/from_package | `get_cvp_studios`, `get_cvp_studio` |
| Target devices | Tag query + resolved device list | Inventory/tags; dry-run must show count/ids |
| Input values | Schema **and** current document; path (root vs nested) | `get_cvp_studio`, `get_cvp_studio_inputs` |
| Workspace name | Unique `workspace_id`, `display_name` | Caller invents; tool uniqueness GET |
| Build tracking | `request_id` UUIDv4 | Tool generates if omitted |
| Review | Build errors, config diff, `build_id` + proof | `get_cvp_workspace_build`, CVP UI |
| Post-submit | Terminal submit state, then `cc_ids` | `get_cvp_workspace` (read tool only) |

### Phase 2 still to verify before coding

| Item | Why |
| --- | --- |
| ~~Auto-approve / auto-execute on this CVaaS~~ | **Done 2026-08-20:** CCs stay pending approval; human execute only |
| Mainline workspace id string | **Done:** `""` |
| `request_id` → `buildId` field path | **Done:** `responses.values[<request_id>]`; buildId typically equals request_id |
| Exact WorkspaceBuild / submit terminal enums | **Done:** see `tests/fixtures/workspace_build_enums.json` |
| Configlet write APIs | Out of scope; AVD-generated configlets |
| Workspace rebase / conflict REST | Mainline drift |
| Rate limits / concurrent builds | Avoid parallel builds on one workspace |
| Change control execute/approve | Remain UI-only; do not “just add start:true” |

### Phase 2 testing (when implemented)

- Mock POST/DELETE with fixed JSON responses (camelCase).
- `confirm=False` → dry-run, no HTTP mutate.
- Writes unregistered when `CLOUDVISION_MCP_ALLOW_WRITES` is not exactly `"1"`.
- Submit unregistered / `submit_disabled` unless `CLOUDVISION_MCP_ALLOW_SUBMIT` is `"1"`.
- Submit HTTP 200 fixture asserts `outcome == "accepted"` and `done is False`.
- Root-path inputs dry-run asserts `full_tree_replace is True`.
- Tag assign dry-run asserts replacement warning + device preview.
- Refuse `^builtin-` on **all** writes.
- Refuse submit without matching `build_proof`.
- Optional staging: create → inputs → assign → build → **delete workspace** without
  submit, prefix `ws-mcp-test-*`.

## Testing (phase 1)

Extend host-allowlist tests (`_is_uri_host_allowed_cvp_host`). Add NDJSON fixtures:
trailing blank line; missing `displayName`; last-write-wins duplicate key. Assert list
tools do **not** use first-object-only fetch.

For `search_cvp_studio_templates`, fixture with a match inside nested `inputSchema`
description and another inside a template body; assert JSON paths differ.

Add fixtures under `tests/fixtures/`:

- `designed_config_response_720xp24.json` — **literal** DESIGNED_CONFIG array (wire-shaped)
- `designed_config_sources_720xp24.json` — analyst summary (optional companion)
- `workspace_response_sample.json` / `workspace_build_response_sample.json` — poll mocks
- `workspace_build_enums.json` — enum tallies + mainline notes
- `inputs_mainline_topology_sample.json` — Inputs `path: {}` + string `inputs`

Unit-test GetConfig `config_type` and `extract_designed_sources` against the wire fixture.
Unit-test `get_ndjson_all_values_with_bearer` last-wins / invalid-line behaviour.

### Tool inventory (quick reference)

| Tool | Phase | HTTP (summary) |
| --- | --- | --- |
| `get_cvp_studios` | 1 | GET Studio/all (full NDJSON) |
| `get_cvp_studio` | 1 | GET Studio keyed (mainline `workspaceId=""`) |
| `get_cvp_studio_inputs` | 1 | GET Inputs/all + client filter |
| `search_cvp_studio_templates` | 1 | GET Studio/all + client search |
| `get_cvp_workspaces` | 1 | GET Workspace/all |
| `get_cvp_workspace` | 1 | GET Workspace keyed |
| `get_cvp_workspace_build` | 1 | GET WorkspaceBuild keyed |
| `get_cvp_designed_config` | 1 | POST compliance GetConfig `DESIGNED_CONFIG` |
| `create_cvp_workspace` | 2 (later) | POST WorkspaceConfig |
| `delete_cvp_workspace` | 2 (later) | DELETE WorkspaceConfig |
| `set_cvp_studio_inputs` | 2 (later) | POST InputsConfig (path replace) |
| `assign_cvp_studio_tags` | 2 (later) | POST AssignedTagsConfig (query replace) |
| `build_cvp_workspace` | 2 (later) | POST WorkspaceConfig `REQUEST_START_BUILD` |
| `submit_cvp_workspace` | 2 (later) | POST WorkspaceConfig `REQUEST_SUBMIT` |
| `create_cvp_studio` | 2 (later) | POST StudioConfig (upsert + lint) |
| `delete_cvp_studio` | 2 (later) | POST StudioConfig `remove` |

No MCP tools for ChangeControlConfig POST/DELETE in v1. No configlet tools in v1.

## Open questions

1. Which **service account display name** in CVP UI corresponds to `sid=019d4bab-9d59-7376-9ed2-fd66a69748a3`?
2. Correct keyed GET for `ConfigletConfig` (workspace + configlet id) — only if configlets
   enter a later phase.
3. ~~Exact mainline `workspaceId` / build enums / Studio vs StudioConfig~~ **Done.**
4. ~~Auto-execute CCs?~~ **No — pending approval (2026-08-20).**
5. ~~SHA-256 for omitted templates~~ — v1 choice is SHA-256 of UTF-8 Mako source.
6. Tag-query → device-id resolution endpoint for assign dry-run previews.
7. Workspace staleness field for submit proof-of-review (beyond immutable WorkspaceBuild).
8. Keyed `Inputs` empty-path encoding (v1 uses Inputs/all filter).
