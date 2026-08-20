# Bucket R2 — Phase 1 read tools completeness

**Spec:** `docs/studios-support-spec.md`  
**Scope:** Phase 1 — Read tools (through `get_cvp_workspace_build`)  
**Compared to:** `cvp_mcp/grpc/envelope.py` (`tool_envelope`), `cloudvision_mcp.py` (`@mcp.tool()` + `tool_enabled`), existing compliance GetConfig in `cvp_mcp/grpc/config.py` / `config_async_flow.py`  
**Date:** 2026-08-19  
**Verdict:** Phase 1 is the right tool *set* for studio inventory + device-level provenance, but it is **not complete enough to implement against** and **does not fully answer the Why question** (line-level “who emitted this CLI”).

---

## Answers to review questions

### 1. Are Phase 1 tools enough to answer “which studio generated this config line”?

**No — not at line granularity.** Heading **Why** asks which **studio or configlet** generated a **line** (duplicated `logging host` on 720xp-24). Heading **`get_cvp_designed_config`** only promises device-level studio keys. Heading **`search_cvp_studio_templates`** documents that a substring search for `logging` hits UI labels, not rendered `logging host`.

What Phase 1 *can* answer after implementation:

| Question | Tool | Confidence |
| --- | --- | --- |
| Which studios exist / what is this studio’s Mako? | `get_cvp_studios` / `get_cvp_studio` | High |
| Does any template/schema *contain* this string? | `search_cvp_studio_templates` | Medium (false positives without JSON path; misses generated CLI) |
| Which studios **contribute** to this device’s designed config? | `get_cvp_designed_config` → `studio_keys` | High *if* `sources` is as described |
| Which **configlet** produced this line? | *No Phase 1 tool* | Fail |
| Which studio emitted **this exact CLI line**? | Not specified | Fail |

Configlet `/all` is 200 in **Endpoint access matrix** but never appears in **Phase 1 — Read tools** or **Tool inventory**.

---

### 2. Missing parameters, response fields, or NDJSON parsing rules?

**Yes.** Global **Response shape** is a one-paragraph NDJSON hint. Phase 1 tools are prose-only (unlike Phase 2 tables). Envelope fields in `tool_envelope` (`collected_at`, `items` vs `object`, nullable `device_id`) are not bound to Phase 1.

---

### 3. Does `get_cvp_designed_config` specify enough of the compliance payload/response to code against?

**No.** The tool names `type: DESIGNED_CONFIG` and a few response keys. It does not specify POST URL, JSON body, timestamp, wrapper (`request` vs flat), field names for config text, or a captured `sources` blob. Existing running-config code already POSTs a concrete body (`config_async_flow.get_config`).

---

### 4. Are workspace/build poll tools sufficient as Phase 2 prerequisites?

**Not as written.** **Canonical provisioning workflow** step 4 and **`build_cvp_workspace` Follow-up** depend on these tools, but Phase 1 does not define `buildId` vs `request_id`, terminal states, poll cadence, or keyed GET query strings. **Phase 2 still to verify** already lists “Exact build state enum values.”

---

## Findings

### Critical — Line-level provenance is the product goal and is unspecified

**Headings:** Why; `search_cvp_studio_templates`; `get_cvp_designed_config`

The original incident is a **rendered CLI line**. Template search is explicitly the wrong layer (`logging` → Event Handler *label*). Designed-config `sources.source[]` is specified as studio *contributors*, not a map from config line → studio/configlet.

Implementers will ship three tools and still cannot close the Why loop without guessing (grep designed text + heuristic match to templates).

**Suggested spec text** (add under `get_cvp_designed_config` or a new “Attribution algorithm” subsection):

```markdown
#### Line attribution (phase 1 success criterion)

Phase 1 **does not** claim per-line source annotations unless GetConfig
DESIGNED_CONFIG includes them. Capture one live payload (720xp-24 /
`JPE19151499`) and document which of these is true:

A. `sources.source[]` is device-level only (studio keys, no line map).
B. Response includes per-block or per-line provenance (document path).
C. Configlets appear as `source_type` other than `CONFIG_TYPE_STUDIO`.

If A: document the **supported** question as “which studios apply to this
device,” and add a **phase 1.1** tool or algorithm:

  1. `get_cvp_designed_config` → `studio_keys` + optional designed text.
  2. For a caller `pattern`, search designed text for matching lines.
  3. `search_cvp_studio_templates(pattern)` **restricted to** those
     `studio_keys` (not fleet-wide schema labels).
  4. If no template hit, search configlets (`GET .../configlet/v1/Configlet/all`
     + keyed ConfigletConfig) — or state configlets out of scope.

If B: return `hits[]` of `{line, line_no, source_type, studio_id|configlet_id}`.
```

---

### Critical — Compliance GetConfig request/response is not a coding contract

**Headings:** `get_cvp_designed_config`; Tool inventory; Why (compliance vs configstatus)

Inventory table: `POST compliance GetConfig DESIGNED_CONFIG`. Tool body: “Use compliance GetConfig with `type: DESIGNED_CONFIG`.” Missing:

| Contract piece | Status in spec | Existing running-config code |
| --- | --- | --- |
| URL | implied only | `/api/v3/services/compliancecheck.Compliance/GetConfig` |
| HTTP method | inventory only | POST |
| Body | not given | `{"request":{"device_id":...,"timestamp": RFC3339, "type":"RUNNING_CONFIG"}}` |
| `timestamp` required? | omitted | yes (`now_ns` → RFC3339) |
| Response unwrap | `sources.source[]` mentioned, no sample | `_extract_config_from_response` looks for `config` / `config.value` |
| Designed **text** field name | “if a body field appears” | unknown for DESIGNED_CONFIG |
| `sources` camelCase vs snake | mixed (`source_type` vs `studioId` elsewhere) | N/A |
| Errors | none | 502/503/504 retry exists for running only |
| Envelope | lists `sources`, `studio_keys` at top level | `tool_envelope` puts payload in `object` |

**Suggested spec text:**

```markdown
#### `get_cvp_designed_config` (contract)

| | |
| --- | --- |
| **Endpoint** | `POST /api/v3/services/compliancecheck.Compliance/GetConfig` |
| **Parameters** | `device_id: str` (serial preferred). Hostname/FQDN: resolve via
  inventory (`Device/all` or existing `resolve_device_to_serial`) first;
  on 504 hostname lookup, caller must pass serial (Why section). |
| **Body** | `{ "request": { "device_id": "<serial>", "timestamp": "<RFC3339 UTC>",
  "type": "DESIGNED_CONFIG" } }` |
| **Reuse** | Same host allowlist + bearer as `fetch_uri_with_bearer` /
  `post_json_with_bearer`. Retry 502/503/504 like running-config GetConfig. |
| **Parse** | Decode JSON (single object or NDJSON/concat). Extract:
  - designed text from first of: `config`, `config.value`, `designedConfig`,
    `body` (record which key won in `warnings` if not `config`);
  - provenance from `sources` / `sources.source` (keep raw + normalize). |
| **Normalize `sources`** | Each element: `source_type` (string enum, e.g.
  `CONFIG_TYPE_STUDIO`), `key` object as returned. Derive
  `studio_keys: list[str]` from studio ids inside `key`. |
| **Envelope** | `tool_envelope(device_id=<serial>,
  data_source="service_api:compliancecheck.getconfig",
  coverage=full|partial|none, obj={...}, warnings=...)`.
  Put `sources`, `studio_keys`, and optional `designed_config_text` **inside
  `object`**, not as extra top-level keys beside the envelope. |
| **Do not** | Use configstatus Resource API URIs (403 in Endpoint access matrix). |

**Fixture (required before coding):** paste one truncated live GetConfig
DESIGNED_CONFIG JSON (keys + `sources` only; omit full CLI if huge).
```

---

### Important — NDJSON `/all` parse rules are incomplete for implementers

**Headings:** Response shape; Testing (phase 1); `get_cvp_studios`; `search_cvp_studio_templates`

**Response shape** says: newline-delimited JSON; skip blanks; do not `json.loads` the whole body. **Testing (phase 1)** adds trailing blank line and missing `displayName`. Still missing:

- Unwrap path: always `result.value`? Some rows `result` only?
- `type` values (`INITIAL`, updates, deletes) — keep all or first INITIAL per key?
- Duplicate keys across stream (initial + update) — last-write-wins?
- Non-JSON lines / `)]}'` XSSI prefix (already handled in `uri_fetch` / `config_async_flow`)
- Shared helper vs ad-hoc per tool
- `fetch_uri_with_bearer` today JSON-parses **first valid line only** (`uri_fetch.py`) — **wrong for `/all`**. Spec must say “do not reuse first-object JSON decode for Studio/all.”

**Suggested spec text** (expand **Response shape**):

```markdown
### Resource API `/all` NDJSON (phase 1 implementer rules)

1. GET the URI with bearer + host allowlist; read full body (size cap TBD,
   e.g. 32 MiB) — do not stop at the first JSON object.
2. Split on newlines. Skip empty / whitespace-only lines.
3. If body starts with `)]}'`, drop that line (XSSI prefix).
4. `json.loads` **each** line. Skip lines that fail decode; append
   `warnings` count `ndjson_skip_invalid_line`.
5. Yield `row = obj.get("result", {}).get("value")`. Skip if `value` is
   missing (do not require `displayName`).
6. Dedupe by resource key (`studioId`+`workspaceId` or equivalent): last
   occurrence wins.
7. Do **not** call `fetch_uri_json_object` / first-line-only helpers for `/all`.
8. Unit fixtures: blank trailing line; value without `displayName`; two
   lines same `studioId` (update wins).
```

---

### Important — Phase 1 tools lack parameter/return tables (inconsistent with Phase 2)

**Headings:** Phase 1 — Read tools (all `####` tools); Tool inventory

Concrete gaps per tool:

#### `get_cvp_studios`

- No `workspace_id` filter (mainline vs all workspaces). Studio keys include `workspaceId`.
- No pagination / timeout.
- “Template type” field path unspecified (`template.type` vs `templateType`).
- Envelope: `items[]` of summaries vs raw NDJSON.

**Suggested:**

```markdown
| **Endpoint** | `GET /api/resources/studio/v1/Studio/all` |
| **Parameters** | none in v1 (optional later: `workspace_id`) |
| **Returns** | `tool_envelope(items=[{studio_id, workspace_id, display_name,
  description, created_by, last_modified_at, template_type}],
  data_source="resource_api:studio.v1")` |
| **Omit** | `template` body / large nested template object |
```

#### `get_cvp_studio`

- **Tool inventory** says `GET StudioConfig`; list tool uses `Studio/all`. Which resource is the keyed GET?
- Query params for keyed GET not given (`key.studioId`, `key.workspaceId`).
- Default “mainline” workspace id not defined (empty string? sentinel?).
- `body: bool = False` — hash algo, encoding (utf-8), whether hash is of Mako source or whole JSON.
- Input schema: returned always, or only with `body=True`?

**Suggested:**

```markdown
| **Endpoint** | `GET /api/resources/studio/v1/Studio?key.studioId=&key.workspaceId=`
  (confirm Studio vs StudioConfig against one live GET; document the winner). |
| **Parameters** | `studio_id: str`, `workspace_id: str | None = None` (default:
  **document the exact mainline workspace id string used on this CVP**),
  `body: bool = False` |
| **When body=False** | omit template source; return `template_bytes`,
  `template_sha256` (hex, SHA-256 of UTF-8 Mako source). Always return
  `input_schema` summary (field names) so Phase 2 callers can draft inputs. |
| **When body=True** | include full Mako; warn if payload > 100 KB. |
```

#### `search_cvp_studio_templates`

- Regex vs literal substring? Case fold?
- Cap on hits / studios scanned?
- Search Studio vs StudioConfig `/all` (inventory: StudioConfig/all).
- Output schema for hits (`studio_id`, `workspace_id`, `json_path`, `snippet`, `match_kind: template|schema`).
- Restrict search to template body vs schema (flag) — needed to avoid Event Handler false positives.

**Suggested:**

```markdown
| **Parameters** | `pattern: str` (literal substring, case-sensitive v1),
  `include_input_schema: bool = True`, `max_hits: int = 100` |
| **Endpoint** | `GET /api/resources/studio/v1/StudioConfig/all` (bodies for search) |
| **Walk** | recursive strings on parsed `result.value`; `json_path` uses
  dotted keys + `[n]` for lists; unescape is **not** required if walking
  parsed JSON (do not search the raw NDJSON string). |
| **Returns** | `items[]`: `{studio_id, workspace_id, display_name, json_path,
  snippet, in_template: bool}` |
```

#### `get_cvp_workspaces`

- Fields: “state, build responses, and `ccIds`” — MCP snake_case vs raw camelCase?
- No filter (submitted vs pending).
- Build responses: embed full builds or ids only?

#### `get_cvp_workspace` / `get_cvp_workspace_build`

- Full paths and query param **exact** names (`workspaceId` vs `workspace_id`).
- How caller obtains `build_id` after `REQUEST_START_BUILD` (`request_id` is **not** obviously `buildId`).
- Poll: interval, timeout, terminal enum (`BUILD_STATE_SUCCESS` / …).
- Response fields needed by Phase 2: errors[], `ccIds`, build state.

**Suggested** (poll contract):

```markdown
#### Workspace build poll (phase 1)

`build_cvp_workspace` returns `request_id` only. After start:

1. Poll `get_cvp_workspace(workspace_id)` until `responses` / `buildId`
   for that `request_id` is present **or** 30s timeout (document the live
   field path from one homelab build).
2. Then poll `get_cvp_workspace_build(workspace_id, build_id)` until
   `state` is in `{SUCCESS, FAILED, CANCELED}` (replace with **captured
   enum strings** from WorkspaceBuild protobuf / one live response).
3. Do not call `submit_cvp_workspace` unless state is success and
   `errors` empty.

Parameters: `workspace_id: str`; build tool also `build_id: str`.
Endpoints:
  `GET /api/resources/workspace/v1/Workspace?key.workspaceId=`
  `GET /api/resources/workspace/v1/WorkspaceBuild?key.workspaceId=&key.buildId=`
```

---

### Important — Envelope / registration conventions underspecified vs live server

**Headings:** Phase 1 — Read tools (intro); `cloudvision_mcp.py`; `tool_envelope`; `tool_enabled`

Intro: “coverage, warnings, data_source as already used by inventory and config tools.” Live envelope also requires `collected_at` and either `items` or `object`. List tools that dump extra keys (`sources` at top level) will diverge from `get_cvp_device_config`.

`tool_enabled` today is **only** `CVP_MCP_DISABLED_TOOLS` skip — it does **not** take `writes=True` (that is Phase 2). Phase 1 should still say: `@mcp.tool()` + `@tool_enabled("<same_name>")` + FastMCP sync wrappers calling async helpers (match existing config tools).

**Suggested:**

```markdown
Each phase 1 tool returns `tool_envelope(...)` only. List tools use `items`;
singleton tools use `object`. Never add parallel top-level keys besides
envelope fields. Register in `cloudvision_mcp.py` with `@mcp.tool()` and
`@tool_enabled("get_cvp_...")`. Reuse `fetch_uri_with_bearer` for GET;
add a **full-body NDJSON** reader (new helper) for `/all`.
```

---

### Important — Missing Phase 1 reads that Phase 2 already assumes

**Headings:** Phase 2 information callers must provide; Endpoint access matrix; `set_cvp_studio_inputs`

Phase 2 table: input values from “`get_cvp_studio` (`input_schema`), CVP UI, or existing mainline inputs via `Inputs/all`.” There is **no** `get_cvp_studio_inputs` (or similar) in Phase 1. `Inputs/all` is 200.

Without a read of **current** inputs, agents cannot draft `set_cvp_studio_inputs` from MCP alone (schema ≠ instance values).

**Suggested:** add Phase 1 `get_cvp_studio_inputs(studio_id, workspace_id=mainline)` → `GET /api/resources/studio/v1/Inputs/all` filtered client-side, or keyed GET if documented.

Configlets: either add `get_cvp_configlets` / search, or amend **Why** to “studios only; configlets out of scope for v1.”

---

### Minor — Studio vs StudioConfig, camelCase, and inventory contradictions

**Headings:** Tool inventory; `get_cvp_studio`; Response shape

- List: `Studio/all`; search: `StudioConfig/all`; one studio: `StudioConfig`. Implementers need one sentence: Studio = published metadata; StudioConfig = workspace-scoped definition including template.
- Response example uses `studioId` camelCase; Phase 1 return bullets mix camel and snake. Phase 2 says POST bodies snake or camel, responses camelCase. Phase 1 MCP output should pick **snake_case in envelope** (match `device_id` in `tool_envelope`).
- `get_cvp_designed_config` response lists `data_source` with a line break that looks like two fields.

---

### Minor — Search tool cannot be “the tool that actually answers the original question”

**Heading:** `search_cvp_studio_templates`

That sentence conflicts with the worked example (no template emits `logging host`). Rephrase to: “finds studios whose **source** mentions the pattern; not a substitute for designed-config provenance.”

---

## Phase 1 completeness matrix

| Need | Spec coverage | Severity if missing |
| --- | --- | --- |
| List/get studios | Partial (no keyed GET params) | Important |
| Template search with JSON path | Partial (no match semantics) | Important |
| Device-level studio sources | Intent only; no POST body | **Critical** |
| Line → studio | Unspecified | **Critical** vs Why |
| Configlet provenance | Endpoints exist; no tool | Important |
| Current studio inputs | Phase 2 depends; no read tool | Important |
| Workspace list/get | Partial | Important |
| Build poll | Endpoints named; no state machine | Important (Phase 2 blocker) |
| NDJSON `/all` | Hint + two fixtures | Important |
| Envelope + `tool_enabled` | One sentence | Minor / Important |

---

## Recommended spec edits (priority)

1. Capture live DESIGNED_CONFIG JSON (`sources` + type enum) and write the GetConfig POST table.
2. Explicitly downgrade or add line-attribution algorithm; add or defer configlets.
3. NDJSON implementer rules + ban first-line JSON helpers for `/all`.
4. Parameter tables for all seven Phase 1 tools, including mainline `workspace_id` and build poll state machine.
5. Add `get_cvp_studio_inputs` or point Phase 2 “how to obtain inputs” only at UI until that tool exists.

---

## Out of scope for this bucket

- Implementing tools
- Editing `docs/studios-support-spec.md` (Wave 2 / user apply)
- Phase 2 write-gate review (R3/R4)
