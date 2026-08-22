# Bucket R3 — Phase 2 writes vs Arista REST + EOS safety

Reviewer: Cursor (opus). Rerouted from the Claude Code CLI farm.
Scope: `docs/studios-support-spec.md`, Phase 2 write tools and their safety model only.
Date: 2026-08-19. Findings only — no spec or Python edits.

## Sources used

| Source | What it settled |
| --- | --- |
| [Studios and Workspaces REST examples](https://aristanetworks.github.io/cloudvision-apis/examples/REST/studios%20and%20workspaces) | Canonical call sequence and POST bodies |
| [studio.v1 model](https://aristanetworks.github.io/cloudvision-apis/models/studio.v1) | `InputsConfig` path-overwrite semantics, `AssignedTagsConfig` replace semantics, `StudioConfig` copy-on-write, `StudioSummary.immutable` / `in_use` |
| [workspace.v1 model](https://aristanetworks.github.io/cloudvision-apis/models/workspace.v1) | `Request` enum (incl. `REQUEST_SUBMIT_FORCE`, `REQUEST_ROLLBACK`), submit semantics, `cc_ids` |
| [changecontrol.v1 model](https://aristanetworks.github.io/cloudvision-apis/models/changecontrol.v1) + [Change Control REST examples](https://aristanetworks.github.io/cloudvision-apis/examples/REST/changecontrol) | `ChangeControlConfig.start` executes a change control on the same endpoint the spec exposes |
| `~/.cursor/rules/arista-eos-safe-changes.mdc` | Homelab hard stops; the 720xp-24 `Ethernet18`/`Ethernet21` incident |

## Verdict

The **endpoints, sequence, and body skeletons are correct**. Every URL and JSON shape in the
Phase 2 tool reference matches Arista's published examples; I found no wrong endpoint and no
malformed body.

The problem is not the API surface, it is the **safety model wrapped around it**. The spec
treats "which endpoint" as the risk boundary. In these APIs the risk lives in **which fields
you put in the body of an endpoint you have already decided to expose**. Three of the five
Critical findings below are the same bug in different clothes: a shared `post_resource_config`
passthrough helper plus a body the caller can shape means the "excluded" dangerous operation is
one JSON key away from the "allowed" safe one.

| Severity | Count | Theme |
| --- | --- | --- |
| Critical | 5 | Field-level passthrough, silent full-replacement writes, model-settable submit gate |
| Important | 11 | Missing read-side tools for diffing, guard scope, recovery paths, secret handling |
| Minor | 8 | Doc fidelity, response shapes, parameter completeness |

---

## Q1 — Does create → inputs → tags → build → review → submit match Arista?

**Yes, exactly.** Arista's "Use an existing studio" walkthrough is create workspace → set
inputs (`InputsConfig`) → assign tags (`AssignedTagsConfig`) → build (`REQUEST_START_BUILD`) →
submit (`REQUEST_SUBMIT`). The spec's canonical workflow is the same order with a human review
step inserted between build and submit, which is a homelab addition, not a deviation.

Two ordering gaps, both Minor:

- The workflow block never places `create_cvp_studio`. For a brand-new studio Arista's order is
  create workspace → **create studio** → inputs/tags → build → submit. Inputs cannot be set for a
  studio that does not yet exist in the workspace.
- Arista's preamble says step 2 is "add objects that you want to modify into the workspace." For
  inputs and tags this happens implicitly by POSTing with `workspace_id` set, so no explicit copy
  call is missing. Worth stating in the spec so an implementer does not go looking for one.

---

## Q2 — Are the POST bodies complete?

| Tool | Body vs Arista | Verdict |
| --- | --- | --- |
| `create_cvp_workspace` | `{"key":{"workspace_id"},"display_name","description"}` | Correct |
| `delete_cvp_workspace` | `DELETE ?key.workspaceId=` | Correct |
| `set_cvp_studio_inputs` | `{"key":{studio_id,workspace_id,path:{values:[]}},"inputs":"<JSON string>"}` | Shape correct; semantics dangerously under-documented (C2) |
| `assign_cvp_studio_tags` | `{"key":{studio_id,workspace_id},"query"}` | Shape correct; semantics mis-described (C3) |
| `build_cvp_workspace` | `REQUEST_START_BUILD` + `request_params.request_id` | Correct; bad default (I3) |
| `submit_cvp_workspace` | `REQUEST_SUBMIT` + `request_params.request_id` | Correct; gate is the problem (C4) |
| `create_cvp_studio` | Nested `template` + `input_schema` | Incomplete parameter list (M3); upsert hazard (C5) |
| `delete_cvp_studio` | `{"key":{...},"remove":true}` | Correct |
| `create_cvp_change_control` | `{"key":{"id"},"change":{"name"}}` | Correct **and one field from executing a CC** (C1) |

The `inputs` JSON-string detail is right and correctly called out as a trap. The
`REQUEST_START_BUILD` / `REQUEST_SUBMIT` enum strings and the `request_params.request_id`
nesting are right. Nothing is missing from the bodies as written; what is missing is a
statement of what the server does with them.

---

## Critical findings

### C1 — `create_cvp_change_control` posts to the change-control **execute** endpoint

The spec says "Do not implement execute/approve in MCP by default — use CVP UI," and lists CC
execute under "still to verify." That framing assumes execute is a different endpoint. It is not.

From the changecontrol.v1 model, `ChangeControlConfig` has exactly four fields: `key`, `change`,
`start`, `schedule`. `start` is a `FlagConfig`, and Arista's own REST example starts a change
control like this:

```bash
curl -X POST '.../api/resources/changecontrol/v1/ChangeControlConfig' \
  -d '{"key":{"id":"VhkkzxK4U"},"start":{"value":true,"notes":"Starting change via REST call"}}'
```

That is the same URL and the same verb `create_cvp_change_control` uses. `schedule` is the same
hazard on a timer. Approve is genuinely separate (`ApproveConfig`), so excluding it works, but
**execute is not excludable by endpoint** — only by field.

Worse, `change` is a `ChangeConfig` containing `stages` → `StageConfig` → `action` with
`name` and `args`, e.g. `{"name":"task","args":{"TaskID":"101"}}`, plus `device_ids`. A CC body
is arbitrary device actions. If `post_resource_config(path, body)` is used as a generic
passthrough and the tool accepts anything shaped like a body, an agent can compose a CC with
task stages and start it in two calls, both of which are nominally "create a CC shell."

Required: `create_cvp_change_control` must construct the body itself from `change_control_id`
and `name`, accept no other caller input, and the write helper must reject any body containing
`start`, `schedule`, or `change.stages` regardless of tool. Given the CC shell has no
demonstrated use in the happy path (the spec itself says studios submit auto-creates CCs), the
cheapest fix is to **cut both CC tools from v1**.

### C2 — `set_cvp_studio_inputs` at the default root path silently replaces the entire input tree

The studio.v1 model states it outright:

> NOTE: Setting an input at a higher path overwrites any prior `Set`s at lower paths. E.g.
> 1. Set `["A","X"]` to `"foo"` 2. Set `["A","Y"]` to `"bar"` 3. Set `["A"]` to `{"X":"bar"}`
> → result `{ "A": { "X": "bar" } }`

`InputsKey.path` empty (`[]`) "stands for the root of the inputs, or the entire set of inputs for
the studio." The spec's documented body hardcodes `"path":{"values":[]}`, so **every call made
through this tool as specified is a whole-tree replacement.**

Concretely: a studio has NTP inputs for `datacenter:NY` and timezone inputs for
`datacenter:SFO`. An agent asked to "add an NTP server" posts an `inputs` blob containing only
the NTP resolver. The SFO timezone inputs are gone. Build succeeds, submit succeeds, and the
resulting change control removes timezone config from every SFO device. Nothing about that
sequence looks like an error to the agent or to a human skimming the tool call.

The spec's only warning is "Wrong shape fails at build time, not POST time." That describes the
benign failure mode and omits the destructive one, where the shape is perfectly valid and merely
incomplete.

Required, in order of preference:

1. Refuse the root path unless an explicit `replace_all_inputs=True` is passed, and make the
   normal path a scoped subtree write (`path_values` non-empty). `InputsKey.path` supports
   key-based bracket notation — `["ntpServers","[ip=10.10.10.10]","vrf"]` — which is precisely
   the tool that makes narrow writes possible.
2. Read-modify-write: GET the current `InputsConfig` for the studio in the workspace, deep-merge,
   and include a before/after diff in the dry-run preview. This needs a read tool that does not
   exist yet (I1).

### C3 — `assign_cvp_studio_tags` replaces the tag query; the name says it adds

`AssignedTagsConfig.query` is a single string, not a list. Setting it overwrites the studio's
assignment wholesale. The spec documents `query=""` as the unassign-everything case for the
delete flow, which implies the author knew, but the tool is still named `assign_*` and described
as "tag query selects devices."

Failure mode: a studio assigned `datacenter:NY` in mainline. An agent is asked to also apply it
to one lab switch and calls `assign_cvp_studio_tags(query="device:720xp-24")`. Every NY device
loses that studio's designed config on submit. This is the exact shape of a config-removal
change control against production switches, produced by a tool call that reads as additive.

Required: rename to `set_cvp_studio_tag_query`; require the caller to echo the current query as
an `expected_current_query` parameter (optimistic concurrency, cheap and effective); refuse `""`
unless `unassign_all=True`; and have the dry-run report the device sets matched by the old and
new queries, not just the query strings.

### C4 — `allow_submit` is a parameter the model sets, so submit has no human-controlled gate

The gate stack for submit is: `CLOUDVISION_MCP_ALLOW_WRITES=1` (human, env), `confirm=True`
(model), `allow_submit=True` (model). The env var is shared with all the harmless drafting
operations, so anyone who enables workspace drafting has, in the same flip, enabled submit —
everything past that point is chosen by the LLM. Two model-settable booleans in front of an
operation the spec itself describes as async and uncancelable is not a gate, it is a speed bump.

Compounding it, the tool has no precondition check. The workflow lists `[human review in CVP UI]`
as step 5, but nothing in `submit_cvp_workspace` verifies a build ran, that it succeeded, or that
anyone looked at the diff. `confirm=True, allow_submit=True` on a workspace built ten seconds
ago is a valid call.

Required:

- A separate env var, `CLOUDVISION_MCP_ALLOW_SUBMIT=1`, defaulting off even when writes are on.
- A proof-of-review precondition: require the caller to pass the `build_id` **and** a value only
  obtainable from `get_cvp_workspace_build` (build hash, or `last_modified` timestamp), and have
  the tool re-fetch and refuse if the workspace changed since. This is the same safeguard Arista
  put on `ApproveConfig.version`, which "is intended to safeguard against approving a Change
  Control that has been updated since last read." Borrow the pattern.
- Refuse when workspace state is not a successful build.

### C5 — `create_cvp_studio` is an upsert, and a Mako template body is unrestricted EOS config

Two problems in one tool.

**It overwrites.** `StudioConfig` docs: "Changes to fields other than `key` and `remove` are
applied to a copy of the mainline." There is no create-vs-update distinction in the API. POSTing
`StudioConfig` with a `studio_id` that already exists does not fail — it copies the mainline
studio into the workspace and replaces its `template` and `input_schema`. A tool called
`create_cvp_studio` that silently rewrites an existing production studio's template is a
name/behavior mismatch with a very large blast radius. Required: pre-check existence in mainline
and refuse unless `overwrite_existing=True`; check `StudioSummary.immutable` and `from_package`
first (see I4).

**The template is arbitrary config.** The spec's safety argument is structural: changes go
through Studios, therefore they are reviewed, therefore they are safe. But `template_body` is a
Mako script that emits EOS config. A template containing `interface Ethernet18` / `shutdown` is
accepted by this tool, builds cleanly, and produces a change control that shuts the port. The
homelab rule file exists because an agent shut `Ethernet18`/`Ethernet21` on `720xp-24`. The
Studios path does not prevent a recurrence, it only routes it through a workspace — and with
`allow_submit` model-settable (C4), the human may never be in the loop.

Required: lint `template_body` and `inputs` for disruptive primitives — `shutdown` on an
interface, `no interface`, `reload`, `write erase`, `no ip routing`, changes to management/uplink
interfaces — and refuse unless an `allow_disruptive` parameter names the specific interfaces,
mirroring the rule file's "user named the device and interface" checklist. Surface any such line
verbatim in the dry-run preview.

---

## Q3 — Is CC execute correctly excluded? Any path that could push running-config?

Excluded in intent, **not in mechanism**. C1 is the direct hole. Beyond it:

### I7 — "running config unchanged until CC execute" is stated as unconditional and is not

Two qualifications the spec omits.

First, submit changes mainline **designed** config immediately. From workspace.v1 on
`REQUEST_SUBMIT`: "Once submitted, changes are applied and change controls are created (if
necessary)." Mainline designed config, compliance state, and every downstream diff move at submit
time even when no device is touched. The spec's phrasing ("running config unchanged until CC
execute") is true but reads as "submit is harmless," which it is not — it is the irreversible
step short of rollback.

Second, whether a CC auto-executes is a CVP instance setting, not an API invariant. The spec
never records what this staging instance does. Before Phase 2 ships, verify on
`www.cv-staging.corp.arista.io` whether change controls created by workspace submit are
auto-approved or auto-executed, and whether any homelab device is set to automatic config push.
If auto-execute is on anywhere, `submit_cvp_workspace` pushes running config, and the entire
safety story collapses. This belongs in "still to verify" at the top, not absent.

### I10 — the `request` enum must be hard-coded per tool, never passed through

`Workspace.Request` includes `REQUEST_SUBMIT_FORCE` ("submit without making any checks that could
normally cause the submission to fail") and `REQUEST_ROLLBACK` ("rollback a submitted workspace,
undoing corresponding changes in the mainline"). Both are far more dangerous than anything the
spec exposes, and both are reachable from the exact endpoint and body shape that
`build_cvp_workspace` and `submit_cvp_workspace` already use — one string differs.

The spec flags `REQUEST_SUBMIT_FORCE` under "still to verify" but never says the implementation
must refuse it. If build and submit share a `_workspace_request(workspace_id, request)` helper,
the enum becomes a parameter and the refusal has to be remembered rather than structural.
Required: literal enum strings inside each tool, plus a helper-level allowlist of
`{REQUEST_START_BUILD, REQUEST_SUBMIT}`.

### I5 — nothing refuses `workspace_id=""`, which is mainline

Throughout the studio API, `workspace_id: ""` means mainline — the spec's own Phase 1 section uses
`?key.workspaceId=` for "single studio config in mainline." No Phase 2 tool validates that
`workspace_id` is non-empty. Whether the server rejects a mainline `InputsConfig` or
`StudioConfig` write is unverified, and "the server probably says no" is not a control. Required:
refuse empty/whitespace `workspace_id` client-side in every write tool, and verify server
behavior before shipping.

### Paths that are correctly closed

Worth stating so the synthesis does not re-litigate them: there is no EOS CLI path, no
`configure terminal`, no direct device write, and no configlet write in Phase 2. Compliance
`GetConfig` is read-only. Approve (`ApproveConfig`) is a genuinely separate endpoint and is
correctly absent. The instinct to keep MCP out of change-control execution is right; it just
needs to be enforced at the field level rather than the endpoint level.

---

## Q4 — Is `^builtin-` enough?

No, on four counts.

### I4 — wrong scope, wrong mechanism, wrong namespace

**Scope.** The `^builtin-` refusal appears only under `delete_cvp_workspace` and in the test list.
Nothing stops `set_cvp_studio_inputs`, `assign_cvp_studio_tags`, `build_cvp_workspace`, or
`submit_cvp_workspace` from targeting a builtin workspace. You cannot delete
`builtin-studios-V0-l3ls`, but you can write inputs into it and submit it.

**Mechanism.** CVP publishes authoritative immutability signals that a name regex approximates
badly. `StudioSummary` carries `immutable` ("if studio is immutable, its display name,
description, schema and template cannot be modified"), `from_package` ("created by a package, and
can only be modified by the packaging service"), and `in_use` ("non-empty inputs, and assigned to
some devices"). Checking those is both stricter and more accurate than pattern-matching an id.

**Namespace.** `^builtin-` is a *workspace* id convention. It protects nothing in the studio id
namespace — Arista-provided studios are `studio-*`. The AVD/L3LS studios that actually matter in
this homelab are not protected by this rule at all.

**Matching.** `^builtin-` is case-sensitive and unanchored against whitespace. Normalize
(`strip().lower()`) before matching, or `Builtin-foo` sails through.

The stronger invariant, which I would put ahead of the regex: **MCP may only write to workspaces
whose id matches `^ws-mcp-`**, a prefix it creates itself. That converts an open-ended denylist
into a closed allowlist and makes the builtin case a special case of a general rule.

Note that Arista's own docs demonstrate deleting `builtin-studios-V0-l3ls` as a legitimate
operation, so the refusal is a deliberate homelab policy rather than an API constraint. Worth
one sentence in the spec so a future reader does not "fix" it.

---

## Q5 — Delete-studio unassign-then-remove: missing steps?

The sequence matches Arista's "Delete a Studio" walkthrough exactly: create workspace → unassign
tags (`query:""`) → `remove:true` on `StudioConfig` → build → submit. Nothing is missing
mechanically. What is missing is impact framing and guards.

### I11 — the tool does not surface that this removes config from live devices

Deleting a studio removes every line it generated from the designed config of every assigned
device. On submit that becomes a change control full of negation config. The spec's
`delete_cvp_studio` entry is a four-row table whose only caution is "unassign tags first" — it
reads like a cleanup operation.

Required: before the write, fetch the studio's current `AssignedTags` query and
`StudioSummary.in_use`, resolve the matched device list, and put "this will remove designed
config from N devices: ..." in the dry-run preview. Refuse outright when `immutable` or
`from_package` is set. `in_use` exists precisely to answer "is anything depending on this."

### Smaller gaps in the delete flow

- The spec does not state that unassign and remove must happen **in the same workspace**. Arista's
  example uses `del-studio` for both. Split across workspaces, the delete builds against stale
  assignments. (Minor)
- `AssignedTagsConfig` has a native `remove: true` field for unassignment; the spec uses
  `query:""` per Arista's example, which is fine, but note that `remove` forbids other data
  fields — a tool that always sends `query` can never use it. Worth one line. (Minor)
- No mention that `InputsConfig` for the removed studio need not be cleaned up separately.
  Removing the studio covers it, but an implementer will wonder. (Minor)
- Arista's unassign example output shows `workspaceId: "ws-timezone-delete"` for a request that
  used `del-studio` — a copy-paste error in Arista's docs. Their `workspace.json` bodies also
  carry trailing commas (invalid JSON), and the first "Build the workspace" curl references
  `workspace.json` instead of `ws-build.json`. The spec correctly cleaned all of these up; the
  point is that the upstream examples are not verbatim-trustworthy and the spec should say it
  normalized them. (Minor)

---

## Remaining Important findings

### I1 — no read tool for current inputs or tag assignments

The "information callers must provide" table says to obtain existing inputs "via `Inputs/all`,"
but no Phase 1 tool exposes `Inputs` or `AssignedTags`, and neither appears in the tool inventory.
Without them, C2's read-modify-write and C3's expected-current-query check are unimplementable,
and every dry-run preview is a request body with no before-state to compare against. Add
`get_cvp_studio_inputs` and `get_cvp_studio_assigned_tags` to Phase 1 and make them prerequisites
for the corresponding writes.

Implementation note: the keyed GET for `InputsConfig` needs `key.path` as a repeated query
parameter, which is awkward over REST and is unverified. Probe it before relying on it.

### I2 — `path_values` is a parameter that the documented body ignores

`set_cvp_studio_inputs` takes `path_values: list = []` but the body template hardcodes
`"path":{"values":[]}`. Either the parameter is dead or the body is wrong. Given C2, this should
resolve toward the parameter being live, required, and defaulting to something other than root.

### I3 — `request_id` defaults contradict the spec's own guidance

`build_cvp_workspace(request_id="b1")` and `submit_cvp_workspace(request_id="s1")` copy Arista's
example literals, but the caller-information table says "`request_id` (opaque, unique per build
attempt) — caller or tool generates UUID/short id." Both cannot be right. Reusing `b1` across
builds of one workspace is the more dangerous half: `WorkspaceBuild` is keyed on
`(workspaceId, buildId)`, so a repeated id risks polling and reporting a **previous** build's
result as if it validated the current changes — a silent stale-review failure feeding straight
into submit. Generate a unique id per attempt and echo it back.

### I6 — dry-run previews the request, not the consequence

`confirm=False` returns "a dry-run preview" of the body. For a human deciding whether to approve,
the body is nearly worthless: it does not say which devices are affected, what changes relative to
current state, or what config the change produces. Dry-run should return matched devices for any
tag query, the before/after input diff, and — where a build already exists — the config delta.
Relatedly, the audit log line (tool, `workspace_id`, `studio_id`, `request_id`, outcome) omits the
payload, so after an incident you can prove *that* inputs were written but not *what*. Add a
payload hash plus the tag query, redacted per I8.

### I8 — studio inputs can contain secrets that dry-run and audit logs would capture

studio.v1 has a `SecretInput` service returning `plain_text` "unmasked value of a secret," so
secret-typed fields exist in studio input schemas. Any tool that echoes `inputs` into a dry-run
preview, an MCP response, or an INFO audit line can put credentials into agent context and
container logs. Required: consult the studio's `input_schema` for secret-typed fields and redact
them from previews, responses, and logs. Never expose `SecretInput`.

### I9 — no recovery path for a workspace that must not proceed

Phase 2 exposes build and submit but nothing to stop them. `Workspace.Request` has
`REQUEST_CANCEL_BUILD` ("stop building a workspace") and `REQUEST_ABANDON` ("does not delete the
workspace, but closes it to any further updates"). Both are strictly safety-increasing, and
abandon is the documented way to render a drafted workspace un-submittable. Delete-workspace is a
blunter substitute that discards the evidence. Adding these two is the rare case where more write
surface makes the system safer.

---

## Remaining Minor findings

| # | Finding |
| --- | --- |
| M1 | Arista's examples authenticate with `--cookie access_token=$token`; the repo uses `Authorization: Bearer`. Both appear in the upstream docs (the delete-workspace curl uses Bearer) and the spec verified Bearer works. Note it so nobody "fixes" it toward cookies. |
| M2 | DELETE responses are shaped `{"key":..., "time":...}` while POST responses are `{"value":..., "time":...}`. The spec only defines `post_resource_config`; the delete path needs its own parsing or the helper needs to handle both. |
| M3 | `create_cvp_studio` parameters list `template_body: str` but the body requires `template.type: "TEMPLATE_TYPE_MAKO"`. Add a `template_type` parameter defaulting to Mako. |
| M4 | Canonical workflow omits where `create_cvp_studio` goes (before inputs, for new studios). |
| M5 | Delete-studio should refuse `immutable` / `from_package` studios — same check as I4, stated for this tool. |
| M6 | `delete_cvp_change_control` has no state precondition; deleting a running or scheduled CC should be refused client-side rather than relying on the API to object. Moot if the CC tools are cut per C1. |
| M7 | Refusing to delete builtin workspaces contradicts an Arista-documented operation. Intentional, but say so. |
| M8 | Spec asserts POST bodies accept snake_case or camelCase and responses are camelCase. Confirmed correct upstream. No action; recorded so the synthesis can mark it verified. |

---

## Recommended spec edits, in priority order

1. Add a **"Field-level dangers" subsection** to Phase 2 stating the rule that makes C1/I10
   comprehensible: the write helper must validate the *body*, not just the path. Explicit denylist:
   `start`, `schedule`, `change.stages` on `ChangeControlConfig`; `REQUEST_SUBMIT_FORCE`,
   `REQUEST_ROLLBACK`, and any non-allowlisted `request` value on `WorkspaceConfig`.
2. Cut `create_cvp_change_control` / `delete_cvp_change_control` from v1, or reduce them to a
   self-constructed body with no caller-supplied fields.
3. Rewrite `set_cvp_studio_inputs` around scoped paths, with root-path replacement behind an
   explicit flag and the proto's overwrite NOTE quoted in the table.
4. Rename `assign_cvp_studio_tags` → `set_cvp_studio_tag_query`, add `expected_current_query`,
   gate `""` behind `unassign_all=True`.
5. Split the env gate: `CLOUDVISION_MCP_ALLOW_WRITES` for drafting,
   `CLOUDVISION_MCP_ALLOW_SUBMIT` for submit. Add the build-proof precondition to submit.
6. Replace the `^builtin-` note with a general guard section: `^ws-mcp-` workspace allowlist,
   normalized `^builtin-` denylist, `StudioSummary.immutable` / `from_package` checks, non-empty
   `workspace_id` requirement.
7. Add `get_cvp_studio_inputs` and `get_cvp_studio_assigned_tags` to Phase 1; make dry-run
   previews show before/after and matched devices.
8. Add template linting for disruptive EOS primitives to `create_cvp_studio`, cross-referencing
   the 720xp-24 incident.
9. Add `abandon_cvp_workspace` and `cancel_cvp_workspace_build`.
10. Move "verify whether this CVP instance auto-approves or auto-executes change controls created
    by submit" into "still to verify" as a **blocking** item.

## Suggested additions to Phase 2 testing

The existing list is good and covers the gates it knows about. Add:

- Body-validation tests: reject `start`, `schedule`, `change.stages` on any CC write; reject any
  `request` value outside `{REQUEST_START_BUILD, REQUEST_SUBMIT}`.
- Reject empty or whitespace-only `workspace_id` on every write tool.
- Reject `workspace_id` not matching `^ws-mcp-`; reject normalized `^builtin-` including
  `Builtin-` and leading whitespace.
- `set_cvp_studio_inputs` with an empty `path_values` and no `replace_all_inputs` → refused.
- `set_cvp_studio_tag_query` with a stale `expected_current_query` → refused.
- `submit_cvp_workspace` without a matching build id / unchanged-workspace proof → refused.
- Secret-typed input fields are redacted from dry-run output and audit log lines.
- `create_cvp_studio` against an existing `studio_id` without `overwrite_existing` → refused.
- Template lint: a `template_body` emitting `shutdown` under an interface → refused without
  `allow_disruptive`.
