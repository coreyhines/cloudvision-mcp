# Preliminary spec: Compliance group — config + image compliance status

Status: **draft** (2026-09-02). Parent: `docs/mcp-tool-consolidation-spec.md`
(`compliance` group). Does **not** implement tools yet.

**Intent:** read **compliance status** (in / out of compliance), not a “sync”
operation or remediation flow. CVP’s wire enums still say `*_IN_SYNC` /
`*_OUT_OF_SYNC`; we expose those as status strings to the agent.

## What exists today

| Need | Tool / path | Gap |
| --- | --- | --- |
| Bug exposure | `get_cvp_all_bugs` | Keep as `compliance` action `bugs` |
| Hardware/software lifecycle (EoL/EoS) | `get_cvp_all_device_lifecycle` | Keep as `compliance` action `lifecycle` |
| Designed-config **text + studio provenance** | `get_cvp_designed_config` → `compliancecheck.Compliance/GetConfig` `DESIGNED_CONFIG` | Works on this tenant; move under `compliance` as `designed_config` |
| Running-config text | `get_cvp_device_config` | Stays on `device` (config body), not a compliance verdict |
| **Config compliance status** | **None** | Need new action |
| **Image compliance status** | **None** | Need new action |

Lifecycle EoL is not image compliance. Designed-config provenance is not a
status code. Agents cannot ask “is config/image compliant?” without guessing
from raw CLI.

## Target APIs (canonical)

### Config compliance status — `arista.configstatus.v1`

| Resource | Role |
| --- | --- |
| **Summary** (`key.deviceId`) | Status from `ConfigSummary.sync` (`CONFIG_SYNC_CODE_IN_SYNC` / `OUT_OF_SYNC`); line counts; timestamps. Map to agent-facing `status: in_compliance \| out_of_compliance \| unspecified` (keep raw enum in `details`). |

Docs: [configstatus.v1](https://aristanetworks.github.io/cloudvision-apis/models/configstatus.v1).

### Image compliance status — `arista.imagestatus.v1`

| Resource | Role |
| --- | --- |
| **Summary** / **ImageSummary** (`key.deviceId`) | Aggregate `compliance_status`; per-component image / TerminAttr / extensions codes; `reboot_required`. Same agent-facing `status` mapping. |

Docs: [imagestatus.v1](https://aristanetworks.github.io/cloudvision-apis/models/imagestatus.v1).

Prefer gRPC GetOne; serialize enums as stable strings. No push / remediate.

## Capture gate (this tenant — 2026-09-02)

Probed via the deployed MCP credentials against CVaaS staging.

**Service account already has the built-in `network-admin` role.** There is still
no role-editor control for individual Resource API models
(`configstatus.v1`, `imagestatus.v1`). The 403s below are therefore **not** a
missing checkbox — same dual-API boundary documented in
`docs/studios-support-spec.md` for configstatus.

| Call | Result |
| --- | --- |
| `GET …/configstatus/v1/Summary?key.deviceId=<serial>` | **403** |
| `GET …/configstatus/v1/ConfigDiff?…` | **403** |
| `GET …/configstatus/v1/Summary/all` | **403** |
| `GET …/imagestatus/v1/ImageSummary?…` | **403** |
| `GET …/imagestatus/v1/ImageSummary/all` | **403** |
| `compliancecheck.Compliance/GetConfig` | **200** (already used) |

**Stop / branch:**

1. **Do not** keep hunting SA role toggles for status APIs on this instance.
   Escalate to Arista/TAC only if product compliance status is required.
2. **Ship status actions as stubs until Resource API works:** parent
   consolidation registers `config_status` / `image_status` and returns
   `coverage=none` + `*_forbidden` on 403. Do **not** omit them from the enum
   (so `help` documents intent). Escalate to Arista/TAC only if product status
   codes are required.
3. **Interim digest-compare:** skip by default (not product status).
4. Re-probe Summary/ImageSummary only after Arista confirms Resource API access
   changed; details in operator notes outside `docs/research/`.

## Proposed `compliance` actions (catalog consolidation)

Hard cut: one MCP tool `compliance`, required `action`, plus `action=help`.

| action | Maps from / to | Notes |
| --- | --- | --- |
| `bugs` | today’s `get_cvp_all_bugs` | Fleet list |
| `lifecycle` | today’s `get_cvp_all_device_lifecycle` | Fleet list |
| `designed_config` | today’s `get_cvp_designed_config` | Per device; studio sources |
| `config_status` | **new** → configstatus Summary | Per device; compliance status + counts |
| `image_status` | **new** → imagestatus Summary/ImageSummary | Per device; software compliance status |

`device_id` resolution: same inventory resolve chain (serial preferred).

Envelope: `tool_envelope`; `data_source` like
`resource_api:configstatus.v1.summary` /
`resource_api:imagestatus.v1.summary`. On 403: `coverage=none`, no fake
`in_compliance`.

## Non-goals

- Anything named or framed as “sync” / remediating drift (CC, image push).
- Line-by-line config diff as a first action (optional later if status works).
- Running-config **body** under `compliance` (stays `device` / `config`).
- Claiming product status from GetConfig digest compare without `derived`.

## Implementation sketch (after access exists)

1. `cvp_mcp/grpc/compliance_status.py` — GetOne helpers + status string maps.
2. Register under grouped `compliance`.
3. Fixtures from proto once a 200 response is capturable; else synthetic.
4. Live verify on one compliant and one non-compliant device if available;
   operator notes untracked.

## Open

- ~~SA permission for configstatus + imagestatus.~~ **Closed:** SA is
  `network-admin`; 403 persists — instance/entitlement, not a local toggle.
- gRPC `SummaryService.GetOne` vs REST path once access exists.
- Digest-compare interim: ship or skip (default **skip**).
