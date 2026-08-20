# Bucket R4 review — caller information, gates, dry-run/confirm/submit

## Verdict

Bucket R4 is not yet tight enough to implement safely without making policy and
response-shape decisions in code. The main workflow is sound, but the write
gate conflicts with the existing `tool_enabled` implementation, the caller
information table does not fully describe several payloads and preconditions,
and asynchronous operations do not have a precise completion contract.

## Findings

### Critical — The master write gate has no implementable contract

The spec proposes `@tool_enabled(..., writes=True)`, but the current
`tool_enabled(tool_name)` accepts no `writes` argument. It only checks
`CVP_MCP_DISABLED_TOOLS` at call time and returns `tool_disabled`; it does not
prevent FastMCP registration. Consequently, an implementer must guess whether
write tools should be absent from discovery, present but blocked, or both.

Specify all of the following:

1. `CLOUDVISION_MCP_ALLOW_WRITES` is enabled only when its stripped value is
   exactly `"1"`. Unset, empty, `"0"`, `"true"`, `"yes"`, and every other value
   are disabled. Do not reuse a permissive truthiness parser.
2. Evaluate the variable when the MCP server constructs its tool registry.
   When disabled, register no phase 2 write tools.
3. Also check the same gate immediately before the mutating HTTP request as
   defense in depth. A disabled runtime check returns
   `error="writes_disabled"` and performs no POST or DELETE.
4. `CVP_MCP_DISABLED_TOOLS` remains an independent per-tool deny list. A tool
   must pass both gates.
5. Do not log the token, authorization header, full input values, template
   body, or input schema. The required audit line should contain identifiers
   and outcome only.

This requires extending or supplementing `tool_enabled`; the decorator shown
in the spec cannot be implemented against the current signature.

### Critical — Submit acceptance is conflated with completion

`REQUEST_SUBMIT` returning HTTP 200 means that CloudVision accepted the
request, not that submission completed, a change control was created, or the
change was executed. The current return text promises `cc_ids` “from subsequent
poll,” which encourages hidden polling inside the write tool and conflicts
with “no compound tools.”

`submit_cvp_workspace` must return an asynchronous acceptance result:

- `outcome: "accepted"`
- `operation: "submit"`
- `done: false`
- `workspace_id`
- `request_id`
- `cc_ids: null` unless IDs are present in the immediate POST response
- the CloudVision response timestamp or resource version when available
- `next_action: "Poll get_cvp_workspace(workspace_id) until submission reaches a terminal state; then inspect ccIds and review/approve/execute in CVP UI."`

HTTP 200 must never produce `outcome: "succeeded"` for submit. The write tool
must not poll and must not create, approve, or execute a change control.
Likewise, `build_cvp_workspace` returns `outcome: "accepted"`, `done: false`,
and directs the caller to poll the read tools. A later read result, not the
POST status, establishes success or failure.

### Important — `request_id` defaults are collision-prone

Defaults `"b1"` and `"s1"` violate the table's requirement that request IDs be
unique per attempt. They collide across workspaces, retries, concurrent agents,
and process restarts.

Use these exact signatures:

```python
build_cvp_workspace(
    workspace_id: str,
    request_id: str | None = None,
    confirm: bool = False,
) -> dict

submit_cvp_workspace(
    workspace_id: str,
    request_id: str | None = None,
    confirm: bool = False,
    allow_submit: bool = False,
) -> dict
```

When `request_id is None`, generate a UUIDv4 string once per invocation and
use that same value in the preview or HTTP request and response. Reject blank
caller-supplied IDs. A retry is a new attempt and therefore gets a new ID
unless the caller deliberately reuses the original ID for recovery; the spec
should not claim idempotency until CloudVision's request-ID semantics are
verified.

### Important — Workspace uniqueness needs a mandatory preflight

“Caller-chosen, unique” is insufficient because callers can miss a collision
or race. Before a confirmed create, perform a keyed workspace GET (or an
equivalent exact-ID lookup):

- If the ID exists, return `error="workspace_id_exists"` and do not POST.
- If uniqueness cannot be checked because the read fails, fail closed and do
  not POST.
- A dry-run may perform this read-only preflight, but never a mutating request.
- Treat a server-side conflict after a clean preflight as a collision/race;
  do not overwrite, update, or retry with another generated ID.

The ID should remain caller-supplied for auditability, with a documented
recommended form such as `ws-mcp-<purpose>-<YYYYMMDD>-<uuid8>`. The tool must
strip surrounding whitespace, reject empty IDs, and reject `^builtin-`.
Deleting a workspace should additionally verify that it exists and is an
eligible draft workspace; “not builtin” alone is not a sufficient deletion
precondition.

### Important — The caller information table is incomplete

The table is adequate for the happy-path concept but not for drafting without
schema guesses. Add these rows or requirements:

| Operation | Caller/preflight information required |
| --- | --- |
| Set inputs | Exact studio input schema, current/mainline input document, desired merged-or-replacement semantics, and exact input path. State whether the tool replaces the whole document at that path. |
| Input path | Define `path_values` element type and meaning, and show root and nested examples. `list = []` is also a mutable default and should not appear in a Python signature. |
| Assign tags | Validated query syntax plus a read-only target preview containing resolved device IDs/count. Empty query must be called out as destructive unassignment, not merely another valid query. |
| Create studio | The complete canonical request schema: template wrapper/type fields, input-schema node types and required keys, key casing, and a fully valid minimal example. “Per Arista example” is not an implementable schema contract. |
| Modify existing studio | Existing workspace/mainline revision or source object to copy, and conflict/rebase behavior. The spec lists create/delete but not an explicit update tool despite discussing modification. |
| Build | Workspace must exist, be mutable, and have no build already in progress; record the returned build ID if CloudVision supplies one. |
| Submit | Latest build must be terminal-success for the current workspace contents, with no subsequent edits; human review must have occurred. Define how this is verified rather than trusting a caller assertion. |
| Delete workspace/studio/CC | Existence, lifecycle state, ownership/scope, and dependency checks. A CC must not be approved, executing, executed, or otherwise non-deletable. |
| Post-submit | Polling terminal states, timeout/cadence guidance, and `ccIds` interpretation. An empty list before terminal state means “not known yet,” not “no CC required.” |

`inputs: dict | str` is ambiguous because a JSON string can be serialized a
second time. Prefer `inputs: dict` with no default. If strings must be accepted,
parse and validate them as a JSON object first, then serialize exactly once.

Use this exact input-path default:

```python
path_values: list[str] | None = None
```

Normalize `None` to `[]` internally. Do not use `path_values: list = []`.

### Important — Dry-run and confirmation behavior needs precedence rules

Define dry-run as a first-class, side-effect-free result, not a failed
confirmation:

1. With writes disabled, tools are undiscoverable. The runtime backstop, if
   reached, returns `writes_disabled` even when `confirm=False`.
2. With writes enabled and `confirm=False`, validate all arguments and perform
   only documented read-only preflights. Return a preview; issue no POST or
   DELETE.
3. With `confirm=True`, a normal write may make exactly one mutating HTTP
   request after validation and preflight.
4. For submit, `confirm=False` returns a preview even when
   `allow_submit=False`, but reports that the extra gate is unsatisfied.
   `confirm=True, allow_submit=False` returns
   `error="submit_not_allowed"` and performs no mutating request.
5. `allow_submit=True, confirm=False` is still dry-run. `allow_submit` never
   implies confirmation.

The preview should contain the normalized method, endpoint path, identifiers,
and request body (with large/sensitive fields summarized by length and SHA-256
rather than logged or echoed in full), plus preflight results and unsatisfied
gates. It must not claim that CloudVision accepted or validated the payload.

### Important — “No compound tools” needs a mechanical definition

Define it as: one MCP write invocation may perform at most one mutating
CloudVision HTTP request and may represent only one named lifecycle action.
Read-only validation/preflight GETs are allowed. It may not invoke another MCP
write tool, auto-create dependent resources, start a build after editing,
submit after building, poll asynchronous work to completion, or approve/execute
a CC.

This closes loopholes such as hiding create → input → build behind a helper or
performing submit plus CC creation/polling in one call.

### Minor — Exact common response contract is missing

All write tools should use one machine-readable envelope. Suggested minimum:

#### Dry-run

```json
{
  "outcome": "dry_run",
  "dry_run": true,
  "mutation_performed": false,
  "operation": "<tool action>",
  "identifiers": {},
  "request_preview": {
    "method": "POST",
    "path": "/api/resources/...",
    "body": {}
  },
  "preflight": {},
  "gates": {
    "writes_enabled": true,
    "confirm": false
  },
  "warnings": [],
  "next_action": "Re-call with confirm=true after reviewing this preview."
}
```

Submit previews also include `allow_submit` under `gates`. Large template and
schema values should be represented by size and SHA-256 in the preview.

#### Synchronous success

Use for create/delete workspace, set inputs, assign tags, create/delete studio,
and create/delete an empty CC only when the API response establishes that the
resource mutation succeeded:

```json
{
  "outcome": "succeeded",
  "dry_run": false,
  "mutation_performed": true,
  "operation": "<tool action>",
  "identifiers": {},
  "api_time": "<if returned>",
  "warnings": [],
  "next_action": "<explicit next workflow step>"
}
```

Echo normalized identifiers and safe scalar fields, not credentials or large
payloads. A non-2xx response is an error, never success. If a resource write is
itself eventually consistent, use `accepted` rather than `succeeded` and
document the corresponding read verification.

#### Asynchronous acceptance

Use for build and submit:

```json
{
  "outcome": "accepted",
  "dry_run": false,
  "mutation_performed": true,
  "done": false,
  "operation": "build_or_submit",
  "workspace_id": "<id>",
  "request_id": "<uuid>",
  "build_id": null,
  "cc_ids": null,
  "api_time": "<if returned>",
  "warnings": [],
  "next_action": "<specific read tool and terminal condition>"
}
```

Populate `build_id` or `cc_ids` only when present in the immediate response.
Do not use empty arrays to represent values that are not known yet.

## Exact parameter defaults

| Parameter | Default | Requirement |
| --- | --- | --- |
| `confirm` | `False` | Every write tool |
| `allow_submit` | `False` | Submit only; independent of `confirm` |
| `request_id` | `None` | Build/submit; generate UUIDv4 once per invocation |
| `path_values` | `None` | Normalize to an empty `list[str]` |
| Workspace `description` | `""` | Safe optional metadata |
| Studio `description` | `""` | Prefer optional metadata unless the API requires presence |
| `inputs` | no default, `dict` | Required; serialize exactly once |
| Tag `query` | no default | Required; empty string must be explicit to unassign |
| IDs, names, template, schema | no default | Required; reject blank required strings |

No boolean gate should default to `True`. No write tool should infer a target
workspace, studio, device set, or change control.

## `cc_ids` polling contract

After submit acceptance, the caller should poll `get_cvp_workspace` separately
using a bounded cadence (recommended initial interval 2 seconds with
exponential backoff capped at 15 seconds, and a caller-selected overall
timeout). The read response must distinguish:

- `submission_state: "pending"` with `cc_ids: null`
- terminal success with `cc_ids: []` when no CC is required
- terminal success with non-empty `cc_ids`
- terminal failure with build/submission errors
- timeout, which is not failure and must preserve the last observed state

The exact CloudVision state enum mapping remains an explicit prerequisite in
the spec. Until verified, preserve raw state values and do not guess that an
unknown state is terminal. Each returned CC ID should be inspected through a
read-only CC tool or the CVP UI. Approval and execution remain out of scope.

## Recommended acceptance tests

1. Writes-disabled startup exposes no phase 2 tools.
2. Runtime writes-disabled backstop makes no POST/DELETE.
3. Every `confirm=False` path makes no mutating request.
4. Submit exercises all four `confirm`/`allow_submit` combinations.
5. Build/submit generate distinct UUIDs when omitted and preserve supplied IDs.
6. Create refuses existing workspace IDs and fails closed when collision
   preflight cannot complete.
7. A confirmed write makes at most one mutating HTTP request.
8. Submit HTTP 200 returns `accepted`, `done=false`, and does not poll.
9. Unknown `cc_ids` is `null`, not `[]`.
10. `inputs` is encoded exactly once and `path_values=None` produces `[]`.
