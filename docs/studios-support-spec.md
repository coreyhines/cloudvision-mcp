# Spec: Studios and designed-config support in cloudvision-mcp

Status: proposed. Written 2026-07-25; **re-verified 2026-08-19** against live CVP with the
MCP container service-account token.

## Why

The server currently exposes telemetry and inventory. It has no way to answer
"which studio or configlet generates this line of device config", which came up while
tracing a duplicated `logging host` statement on 720xp-24. Answering it required
hand-rolled curl against the CloudVision resource API. Those findings are below so
the work does not need repeating.

A second motivation: `get_cvp_device_config` tries configstatus first (403 on homelab Resource
API), then falls back to compliance GetConfig for running config. Designed-config read should
use compliance `DESIGNED_CONFIG` directly (see read tools below).

## Implementation phases

| Phase | Scope | Risk | Ship when |
| --- | --- | --- | --- |
| **1 — Read** | Studios, workspaces, template search, designed-config provenance, workspace/build status | Low | First |
| **2 — Write** | Workspace/studio/inputs/tag/CC CRUD, build, optional submit | **High** — changes designed config / creates change controls | After phase 1 + explicit homelab opt-in |

Phase 2 is **API-feasible today** (homelab token verified 2026-08-19) but gated in MCP by
policy, not by missing CloudVision permissions.

## Verified environment facts

These were confirmed empirically. Several contradict reasonable assumptions, so they
are worth stating explicitly.

### Service account vs user role (important)

**`network-admin` on your human user does not apply to the MCP service account token.**

CloudVision service accounts have their **own** role assignments (Settings → Access
Management → Service Accounts). The MCP container token (398 chars) decodes to a
service-account JWT with claims like `sid`, `ogn`, `dsl`, `lbac` — **no embedded role
name**. Permissions are evaluated server-side from roles bound to that service account.

Re-checked **2026-08-19** with token `sid=019d4bab-9d59-7376-9ed2-fd66a69748a3` on
`www.cv-staging.corp.arista.io`:

| API family | Result | Notes |
| --- | --- | --- |
| inventory, studio, workspace, changecontrol (read) | **200** | Auth OK |
| studio/workspace/changecontrol (write probes) | **200** | See write section below |
| **configstatus** REST + gRPC | **403 / PERMISSION_DENIED** | Resource API layer only; **not** a missing UI “config read” toggle |
| **compliancecheck GetConfig** (`/api/v3/services/compliancecheck.Compliance/GetConfig`) | **200** | `RUNNING_CONFIG` and `DESIGNED_CONFIG` both work for EOS serials |

**Re-checked 2026-08-19:** Config read does **not** require fixing a configstatus permission in the role editor. The MCP
service account can fetch running config (~19 KB for `JPE19151499` / 720xp-24) and designed-config **studio sources**
via compliance GetConfig. configstatus Resource API remains 403 — likely a separate Resource API auth boundary on this
staging instance, not an absent network-admin checkbox.

`get_cvp_device_config` already falls back to compliance when configstatus fails; use **device serial** when hostname
lookups 504. Designed-config tooling should use compliance `DESIGNED_CONFIG`, not configstatus URIs.

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

**Response shape.** The `/all` endpoints stream newline-delimited JSON, one object per
line, not a JSON array:

```json
{"result":{"value":{"key":{"studioId":"..."},"displayName":"...","template":{...}},"time":"...","type":"INITIAL"}}
```

Parse line by line and skip blanks. A whole-body `json.loads` will fail.

## Endpoint access matrix

Confirmed by probing with the container token on **2026-08-19** (supersedes 2026-07-25
where noted).

| Endpoint | Status | Notes |
| --- | --- | --- |
| `/api/resources/inventory/v1/Device/all` | 200 | |
| `/api/resources/studio/v1/Studio/all` | 200 | |
| `/api/resources/studio/v1/StudioConfig/all` | 200 | |
| `/api/resources/workspace/v1/Workspace/all` | 200 | |
| `/api/resources/workspace/v1/WorkspaceConfig/all` | 200 | |
| `/api/resources/studio/v1/Inputs/all` | 200 | Was uncertain in July probe |
| `/api/resources/configlet/v1/Configlet/all` | 200 | Was uncertain in July probe |
| `/api/resources/configlet/v1/ConfigletConfig/all` | 400 | `nil workspace key` — use keyed GET, not `/all` |
| `/api/resources/changecontrol/v1/ChangeControl/all` | 200 | |
| `/api/resources/configstatus/v1/ConfigDiff/all` | 403 | `user not authorized` |
| `/api/resources/configstatus/v1/Configuration?key.deviceId=<serial>` | 403 | same |
| gRPC `configstatus` Summary/Configuration GetOne | PERMISSION_DENIED | same principal as REST |

### Write access (same token, 2026-08-19)

Minimal POST/DELETE probes (no production config changes retained):

| Operation | Endpoint | Status |
| --- | --- | --- |
| Create workspace | `POST .../workspace/v1/WorkspaceConfig` | 200 |
| Delete workspace | `DELETE .../workspace/v1/WorkspaceConfig?key.workspaceId=...` | 200 |
| Start build | `POST .../WorkspaceConfig` + `REQUEST_START_BUILD` | 200 |
| Submit workspace | `POST .../WorkspaceConfig` + `REQUEST_SUBMIT` | 200 (async; accepts request) |
| Set studio inputs | `POST .../studio/v1/InputsConfig` | 200 |
| Assign studio tags | `POST .../studio/v1/AssignedTagsConfig` | 200 |
| Create change control shell | `POST .../changecontrol/v1/ChangeControlConfig` | 200 |
| Delete change control | `DELETE .../changecontrol/v1/ChangeControlConfig?key.id=...` | 200 |
| Delete/modify studio | `POST .../studio/v1/StudioConfig` (`remove: true`) | 404 if studio missing; **not 403** |

**Conclusion:** This service account can **provision** (studios/workspaces/change controls)
and **read config via compliance GetConfig**, but not **configstatus Resource API**.
Phase 2 write tools can target the provisioning endpoints below; phase 1 read tools should
prefer compliance for device config, not configstatus URIs.

Official REST write flow documented by Arista:
[Studios and Workspaces REST examples](https://aristanetworks.github.io/cloudvision-apis/examples/REST/studios%20and%20workspaces)

## Phase 1 — Read tools

Follow the existing module conventions: an async fetch helper, a `coverage` field, a
`warnings` list, and `data_source` naming as already used by the inventory and config
tools. Reuse `fetch_uri_with_bearer` and keep the `_CVP_HOST_SUFFIXES` allowlist check.

#### `get_cvp_studios`

Lists studios with `studioId`, `workspaceId`, `displayName`, `description`,
`createdBy`, `lastModifiedAt`, and template type. Omits template bodies, which are
large. Reads `/api/resources/studio/v1/Studio/all`.

#### `get_cvp_studio`

One studio by `studio_id`, including the Mako template body. Accepts an optional
`workspace_id`, defaulting to mainline. Template bodies routinely exceed 100 KB, so
support a `body: bool = False` argument and return a length plus a hash when omitted.

#### `search_cvp_studio_templates`

The tool that actually answers the original question. Takes a `pattern` and returns
every studio whose template or input schema contains it, with the matching lines and
their JSON paths.

Implementation note that matters: template bodies are Mako source stored deeply nested
and JSON-escaped, and input schemas carry human-readable descriptions that produce
false positives. A flat substring search over the raw response finds hits in
`inputSchema.fields.values.*.description` that have nothing to do with rendered config.
Walk the parsed object recursively, yield `(json_path, string)` pairs, and report the
path alongside each hit so callers can distinguish a template body from a UI label.

Worked example: searching `logging` matched only the *EOS Event Handler* studio, at
paths like `inputSchema.fields.values.inputfield_onLoggingConfig.label`. No studio
template emits `logging host`. Without the JSON path in the output that result reads as
a false positive.

#### `get_cvp_workspaces`

Lists workspaces with state, build responses, and `ccIds`. Useful for spotting pending
or submitted changes not yet in mainline. Already confirmed 200.

#### `get_cvp_designed_config`

Designed config for a device, plus studio source provenance. Use compliance GetConfig with
`type: DESIGNED_CONFIG` (confirmed 200 on homelab). Returns at minimum a `sources.source[]`
list with `source_type` (e.g. `CONFIG_TYPE_STUDIO`) and studio `key` — sufficient to answer
“which studios contribute to this device’s designed config.” Optional: fetch rendered config
text if a body field appears in the response for this CVP version.

**Parameters:** `device_id` (serial preferred; resolve hostname via inventory first).

**Response fields:** `sources`, `studio_keys`, `device_id`, `data_source:
service_api:compliancecheck.getconfig`, `coverage`, `warnings`.

#### `get_cvp_workspace` / `get_cvp_workspace_build` (phase 1 add-ons)

Single workspace state (`GET .../Workspace?key.workspaceId=`) and build output
(`GET .../WorkspaceBuild?key.workspaceId=&key.buildId=`). Needed to poll build progress
before any phase 2 submit.

---

## Phase 2 — Write tools

CloudVision **does** expose write APIs for studios, workspaces, and change controls.
The MCP service account token in homelab **already has write access** to these endpoints
(verified 2026-08-19). MCP tools wrap the same REST Resource API POST/DELETE flows Arista
documents.

**Homelab policy:** designed config changes belong in CVP Studios → Workspace → review →
Change Control → approve/execute. Agents must not silently push config. Write tools exist for
**drafting** workspace content; submit and execute require explicit human intent.

### Global write gates (all phase 2 tools)

| Gate | Purpose |
| --- | --- |
| `CLOUDVISION_MCP_ALLOW_WRITES=1` in server env | Master switch; default unset/off |
| `@tool_enabled(..., writes=True)` or separate registry | Write tools not registered unless env set |
| `confirm: bool = False` parameter | Every write call requires `confirm=True` or returns a dry-run preview |
| No compound tools | Never combine create → inputs → build → submit in one MCP invocation |
| Audit log line | INFO log: tool name, `workspace_id`, `studio_id`, `request_id`, outcome |

POST bodies accept **snake_case or camelCase** keys; responses are **camelCase** (Arista).

Shared helper: `post_resource_config(path, body) -> dict` using existing bearer + host
allowlist; parse single JSON object responses (writes return one object, not NDJSON stream).

### Canonical provisioning workflow

Arista’s required sequence for config-impacting changes:

```text
1. create_cvp_workspace
2. set_cvp_studio_inputs        (per studio, optional repeat)
3. assign_cvp_studio_tags       (per studio; tag query selects devices)
4. build_cvp_workspace          (REQUEST_START_BUILD; poll get_cvp_workspace_build)
5. [human review in CVP UI]
6. submit_cvp_workspace         (REQUEST_SUBMIT; optional — creates CC if device CLI needed)
7. [human approve/execute CC in CVP UI — NOT an MCP default]
```

Deleting a studio from mainline uses the same workspace pattern: create workspace → unassign
tags (`query: ""`) → `remove: true` on StudioConfig → build → submit.

### Tool reference (parameters, API, response)

#### `create_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Parameters** | `workspace_id: str` (caller-chosen, unique), `display_name: str`, `description: str = ""`, `confirm: bool = False` |
| **Body** | `{"key":{"workspace_id":"..."},"display_name":"...","description":"..."}` |
| **Returns** | `workspace_id`, `display_name`, `time`, `dry_run` preview when `confirm=False` |
| **Caller must supply** | Unique `workspace_id` (convention: `ws-mcp-<purpose>-<date>`) |

#### `delete_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `DELETE /api/resources/workspace/v1/WorkspaceConfig?key.workspaceId=<id>` |
| **Parameters** | `workspace_id: str`, `confirm: bool = False` |
| **Returns** | `workspace_id`, `time` |
| **Notes** | Do not delete builtin workspaces (`builtin-studios-*`). Refuse IDs matching `^builtin-`. |

#### `set_cvp_studio_inputs`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/InputsConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `inputs: dict \| str`, `path_values: list = []`, `confirm: bool = False` |
| **Body** | `{"key":{"studio_id","workspace_id","path":{"values":[]}},"inputs":"<JSON string>"}` |
| **Critical detail** | `inputs` is a **JSON-encoded string**, not a nested object. Serialize with `json.dumps(inputs)`. |
| **Caller must supply** | Valid inputs shape for that studio’s input schema — obtain from `get_cvp_studio` or CVP UI. Wrong shape fails at build time, not POST time. |
| **Returns** | `studio_id`, `workspace_id`, `time` |

#### `assign_cvp_studio_tags`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/AssignedTagsConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `query: str`, `confirm: bool = False` |
| **Body** | `{"key":{"studio_id","workspace_id"},"query":"datacenter:NY"}` |
| **Query syntax** | CVP tag query string (same as Studios UI). Use `device:<hostname>` or tag labels from `get_cvp_*` tag tools. Empty string unassigns all tags (studio delete flow). |
| **Returns** | `studio_id`, `workspace_id`, `query`, `time` |

#### `build_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Parameters** | `workspace_id: str`, `request_id: str = "b1"`, `confirm: bool = False` |
| **Body** | `{"key":{"workspace_id":"..."},"request":"REQUEST_START_BUILD","request_params":{"request_id":"b1"}}` |
| **Returns** | `workspace_id`, `request_id`, `time` |
| **Follow-up** | Poll `get_cvp_workspace` / `get_cvp_workspace_build` until build state succeeds or returns errors. Do not expose submit until build OK. |

#### `submit_cvp_workspace`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/workspace/v1/WorkspaceConfig` |
| **Body** | `{"key":{"workspace_id":"..."},"request":"REQUEST_SUBMIT","request_params":{"request_id":"s1"}}` |
| **Parameters** | `workspace_id: str`, `request_id: str = "s1"`, `confirm: bool = False`, **`allow_submit: bool = False`** |
| **Extra gate** | Requires `allow_submit=True` in addition to `confirm=True` and env master switch. |
| **Returns** | `workspace_id`, `request_id`, `time`, `cc_ids` (from subsequent `get_cvp_workspace` poll if CC created) |
| **Behavior** | Submit is async and **cannot be canceled**. Device-impacting changes create change controls; running config unchanged until CC execute. |

#### `create_cvp_studio`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/StudioConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `display_name: str`, `description: str`, `template_body: str`, `input_schema: dict`, `confirm: bool = False` |
| **Body** | Full studio definition per Arista example (Mako `template`, nested `input_schema`). |
| **Caller must supply** | Complete `input_schema` graph — large payload. Prefer modifying existing studios via inputs/tags over creating new studios from MCP unless template is already drafted offline. |
| **Returns** | `studio_id`, `workspace_id`, `time` |

#### `delete_cvp_studio`

| | |
| --- | --- |
| **Endpoint** | `POST /api/resources/studio/v1/StudioConfig` |
| **Parameters** | `studio_id: str`, `workspace_id: str`, `confirm: bool = False` |
| **Body** | `{"key":{"studio_id","workspace_id"},"remove": true}` |
| **Prerequisite** | Unassign tags first (`assign_cvp_studio_tags` with `query=""`). Then build + submit workspace. |

#### `create_cvp_change_control` / `delete_cvp_change_control`

| | |
| --- | --- |
| **Endpoints** | `POST` / `DELETE .../changecontrol/v1/ChangeControlConfig` |
| **Parameters** | `change_control_id: str`, `name: str`, `confirm: bool = False` (create); `change_control_id`, `confirm` (delete) |
| **Body (create)** | `{"key":{"id":"..."},"change":{"name":"..."}}` |
| **Scope** | Create empty CC shell only. **Do not implement execute/approve in MCP** by default — use CVP UI. |
| **Note** | Studios submit usually auto-creates CCs; manual CC create is for ad-hoc actions outside this spec’s happy path. |

### Phase 2 information callers must provide

Document in README / tool docstrings so agents know what to gather **before** calling writes:

| Step | Information needed | How to obtain (phase 1 tools) |
| --- | --- | --- |
| Pick studio | `studio_id` | `get_cvp_studios`, `search_cvp_studio_templates` |
| Target devices | Tag query string | Inventory/tags; e.g. `device:720xp-24` or `datacenter:NY` |
| Input values | JSON matching studio input schema | `get_cvp_studio` (`input_schema`), CVP UI, or existing mainline inputs via `Inputs/all` |
| Workspace name | Unique `workspace_id`, human `display_name` | Caller invents; check `get_cvp_workspaces` for collisions |
| Build tracking | `request_id` (opaque, unique per build attempt) | Caller or tool generates UUID/short id |
| Review | Build errors, config diff | `get_cvp_workspace_build`, CVP UI |
| Post-submit | `cc_ids` | `get_cvp_workspace` → `ccIds` field |

### Phase 2 still to verify before coding

| Item | Why |
| --- | --- |
| Change control **execute/approve** REST paths and bodies | Out of scope for v1 writes; confirm with Arista changecontrol API docs if ever needed |
| Configlet write APIs (`ConfigletConfig` POST) | Read works; write not probed — homelab uses AVD configlets |
| `REQUEST_SUBMIT_FORCE` behavior | Documented in workspace protobuf; do not expose without extreme safeguards |
| Workspace rebase / conflict handling | What REST request performs rebase when mainline drifted |
| Rate limits / concurrent build rules | Avoid parallel builds on same workspace |
| Exact build state enum values | Map `WorkspaceBuild.state` to user-facing strings in poll helper |

### Phase 2 testing

- Mock POST/DELETE with fixed JSON responses (camelCase).
- Refuse `confirm=False` → returns dry-run body without HTTP call.
- Refuse writes when `CLOUDVISION_MCP_ALLOW_WRITES` unset.
- Refuse `submit` unless `allow_submit=True`.
- Refuse `workspace_id` matching `^builtin-`.
- Integration test (optional, staging): create → inputs → assign → build → **delete workspace**
  without submit, using `ws-mcp-test-*` prefix.

## Testing (phase 1)

Extend the existing host-allowlist tests, which already cover
`_is_uri_host_allowed_cvp_host`. Add fixtures for the NDJSON stream shape, including a
blank trailing line and a row whose `result.value` lacks `displayName`, since some rows
key only on `studioId`.

For `search_cvp_studio_templates`, add a fixture with a match inside a nested
`inputSchema` description and another inside a template body, and assert the returned
JSON paths differ. That is the behavior that makes the tool trustworthy.

### Tool inventory (quick reference)

| Tool | Phase | HTTP (summary) |
| --- | --- | --- |
| `get_cvp_studios` | 1 | GET Studio/all |
| `get_cvp_studio` | 1 | GET StudioConfig |
| `search_cvp_studio_templates` | 1 | GET StudioConfig/all + client search |
| `get_cvp_workspaces` | 1 | GET Workspace/all |
| `get_cvp_workspace` | 1 | GET Workspace |
| `get_cvp_workspace_build` | 1 | GET WorkspaceBuild |
| `get_cvp_designed_config` | 1 | POST compliance GetConfig DESIGNED_CONFIG |
| `create_cvp_workspace` | 2 | POST WorkspaceConfig |
| `delete_cvp_workspace` | 2 | DELETE WorkspaceConfig |
| `set_cvp_studio_inputs` | 2 | POST InputsConfig |
| `assign_cvp_studio_tags` | 2 | POST AssignedTagsConfig |
| `build_cvp_workspace` | 2 | POST WorkspaceConfig REQUEST_START_BUILD |
| `submit_cvp_workspace` | 2 | POST WorkspaceConfig REQUEST_SUBMIT |
| `create_cvp_studio` | 2 | POST StudioConfig |
| `delete_cvp_studio` | 2 | POST StudioConfig remove |
| `create_cvp_change_control` | 2 | POST ChangeControlConfig |
| `delete_cvp_change_control` | 2 | DELETE ChangeControlConfig |

## Open questions

1. Which **service account display name** in CVP UI corresponds to `sid=019d4bab-9d59-7376-9ed2-fd66a69748a3`?
2. Correct keyed GET for `ConfigletConfig` (workspace + configlet id query params).
3. Whether phase 2 write tools should ship in homelab MCP at all, or stay read-only with human CVP UI for submits.
4. Configlet write path for homelab AVD-generated configlets (if ever needed).
5. Whether `get_cvp_designed_config` should diff running vs designed (needs both compliance GetConfig types + merge logic).
