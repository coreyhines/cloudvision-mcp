# Studio root-CAS framework + Telemetry plugin design

**Date:** 2026-11-23
**Status:** Approved (awaiting implementation plan)
**Related systems:** CloudVision Studios (`studio-telemetry-config`, `studio-mss-service`, `studio-avd-campus-fabric`), cloudvision-mcp `studios_write`
**Prior context:** Phase 2 Studios specs (`docs/studios-phase2-*.md`); MSS root inputs (`set_mss_inputs`); Campus SVI inputs (`set_campus_svi_inputs`); 2026-11-23 ztx-7230 TerminAttr re-onboard (generic `set_inputs` refused root)

## Goal

Give agents a **reusable, fail-closed way to draft root-row Studio input changes** through cloudvision-mcp, without submitting workspaces or executing Change Controls.

v1 delivers:

1. A shared **root-CAS** library extracted from the MSS / Campus pattern.
2. Thin adapters so existing MSS and Campus writers call that library without changing their public MCP action contracts.
3. A first plugin: **`studios_write.set_telemetry_inputs`** for Streaming Telemetry Agent (`studio-telemetry-config`), with a minimal ops vocabulary that unblocks TerminAttr re-onboard (token path, VRF, disableAAA, profile assignment).

## Success criteria

1. From MCP, create `ws-mcp-*` draft → `set_telemetry_inputs` dry-run → confirm → `build` succeeds.
2. Ops can set onboarding token path to `/tmp/cv-onboarding-token`, VRF `mgmt`, `disableAAA` true, and assign profile to `device:WTW23350044` (ztx-7230).
3. `studios.inputs` for `studio-telemetry-config` in that workspace shows the patched document; `inputs_sha256` changes as expected.
4. MSS and Campus public actions keep the same names, required fields, and refusal codes; behavior is equivalent after the helper extract.
5. Generic `studios_write.set_inputs` remains root-refusing and description-scoped (no loosening).
6. No MCP path submits a workspace or creates/approves/executes Change Control.

## Non-goals

- `submit_cvp_workspace` or any Change Control write.
- Free-form root document replace on telemetry in v1 (`ops_only`).
- Loosening Phase 2.1 generic `set_inputs` (`root_path_forbidden`, description allowlist).
- Rewriting MSS / Campus operation vocabularies or migrating them onto a single mega MCP tool.
- Other studios (Software Management, Access Interfaces beyond existing tools, Segmentation, etc.).
- Editing EOS on the device (token file copy, TerminAttr bounce) from this MCP.

## Decisions (locked)

| Topic | Choice |
|-------|--------|
| Product shape | Shared root-CAS **core** + **per-studio MCP actions** (not one mega `set_root_inputs`) |
| Write modes | Hybrid framework: plugins declare `ops_only` or `document_ok`; telemetry v1 = **`ops_only`** |
| Existing writers | **Thin adapters** onto shared helpers; public MCP APIs unchanged |
| First plugin | Streaming Telemetry Agent (`studio-telemetry-config`) |
| Telemetry v1 ops | Minimal: upsert/remove onboarding profile; set profile assignment by tag; patch profile leaves (token, VRF, disableAAA, multi_cluster, cluster addr) |
| CAS | `expected_inputs_sha256` from `studios.inputs` (same digest helper as MSS/Campus) |
| Draft scope | `ws-mcp-*` only; dry-run → `preview_token` → confirm |
| Submit | Never from MCP |

## Problem statement

Most CloudVision Studios store the entire input tree in **one** Inputs Resource row at `path.values []`.

Phase 2.1 `studios_write.set_inputs` refuses empty `path_values` (`root_path_forbidden`) and only allows description-like leaves. That is correct safety for a generic tool, but it means **the original write goal of cloudvision-mcp cannot be met** for root-row studios without dedicated writers.

MSS and Campus already prove the sanctioned pattern: read root → digest CAS → bounded edit → scoped diff → POST root `InputsConfig` into a draft workspace. Telemetry (and future studios) need the same pattern without copy-paste.

## Architecture

```text
MCP studios_write.set_telemetry_inputs
        |
        v
 studio_telemetry_inputs.py   (ops validate + apply + writable-key scope)
        |
        v
 studio_root_cas.py           (load root, CAS, preview/confirm, POST)
        ^
        |
 studio_mss_inputs.py / studio_campus_svi_inputs.py
        (thin: keep op apply locally; call shared CAS for load/post/preview)
```

### Core responsibilities (`studio_root_cas.py`)

Owned by the shared module:

- `writes_enabled` / draft workspace validation / pending workspace check
- Studio GET (overlay then mainline; refuse immutable / packaged as today)
- `_load_root_inputs` (or thin wrapper) for a given `studio_id`
- Digest compare against `expected_inputs_sha256`
- Preview token bind/check keyed by tool name + after-digest (+ studio/workspace)
- POST `InputsConfig` at `path.values []` with `posted_at_root: true` envelope fields
- Common refusal/outcome envelope helpers reused by plugins

Not owned by the core (plugin-local):

- Operation parsing and entry validation
- Applying ops to a deep copy
- Writable top-level key allowlist for blast-radius / tree-diff scope
- Whether `document_ok` full replace is offered (telemetry: no)

### Plugin contract

Each plugin provides:

| Field | Meaning |
|-------|---------|
| `studio_id` | Fixed studio resource id |
| `mode` | `ops_only` or `document_ok` |
| `tool_name` | Preview-token / envelope tool identity |
| `writable_top_level_keys` | Diff may only touch these roots |
| `max_operations` | Cap (telemetry: 20, same spirit as MSS) |
| `validate_and_apply(document, operations) -> (after, error)` | Pure transform |

`document_ok` plugins (none in v1) would pass a full replacement document instead of ops; core still requires CAS + scoped diff against that plugin’s writable keys (or an explicit “replace entire document” flag with a louder refusal path — deferred; do not implement until a studio needs it).

## Telemetry v1 operation vocabulary

Studio: `studio-telemetry-config`
MCP action: `studios_write.set_telemetry_inputs`
Required: `workspace_id`, `expected_inputs_sha256`, `operations`
Optional: `confirm`, `preview_token`

| `op` | Fields | Semantics |
|------|--------|-----------|
| `upsert_onboarding_profile` | `profile` (object with `profileName`) | Replace profile whose `profileName` matches, else append. Validated subset of live schema. |
| `remove_onboarding_profile` | `profileName` | Remove matching profile. Missing → not-found refusal. |
| `set_profile_assignment` | `query`, `onboardingProfile` (string or null), optional `default_config` | Upsert `profileAssignment` row matched by `tags.query`. |
| `patch_onboarding_profile` | `profileName`, `patch` | Patch allowed leaves on existing profile. |

### Allowed `patch` / profile leaves (v1)

- `cvopt_auth_token` (string path, e.g. `/tmp/cv-onboarding-token`, or null)
- `cvopt_vrf` / profile `sourceInterfaceAndVrf.source_vrf` as agreed with live schema (prefer matching existing `dev_collection[].cvopt_vrf` and/or `sourceInterfaceAndVrf`)
- `disableAAA` under `disableAAAResolver`
- `multi_cluster` (bool)
- Cluster address: `cluster_profiles[].cvopt_cvaddr_group.addr1` and `ingestPort` for the named cluster profile (default name `cvaas` if single)

Exact JSON shape must match the live root fixture captured from staging (see Tests). Prefer minimal patches that only touch fields already present on `campus-1709` / new `staging-mgmt-ztx` profiles.

### Writable top-level keys (diff scope)

At minimum:

- `onboardingProfiles`
- `profileAssignment`

Optionally include if an op touches them:

- `disableAAAResolver` (only if we allow global AAA rows; v1 prefers AAA inside the profile object)
- `sourceVrfAssignment` (out of scope for v1 ops unless needed; ztx already has `device:WTW23350044` → `mgmt`)

v1 should **not** require changing `sourceVrfAssignment` for the ztx acceptance case.

### Out of scope for telemetry ops v1

- `customFlags` rows
- Global `multi_cluster` / multi-cluster cluster profile definitions beyond the single active cluster addr on a profile
- `device_configuration_resolver`
- Studio tag query (`assign_tags` remains separate)

## Safety invariants (unchanged)

1. Never submit; never approve/execute Change Control.
2. Draft workspaces only (`ws-mcp-*`).
3. Dry-run default; confirm requires matching `preview_token`.
4. Digest CAS: stale read → refuse; re-read and retry.
5. Tree diff outside writable keys → refuse.
6. Generic `set_inputs` stays as-is.
7. Agent-fence must allowlist the new action explicitly.

## Agent-fence / deploy

- Add `set_telemetry_inputs` to `studios_write` allowlist in agent-fence `policy/cloudvision.yaml` (and rendered gateway config).
- Bump `hub.freeblizz.com/cloudvision_mcp` image; pin in `podman/strongpod/cloudvision-mcp`.
- Writes remain gated by `CLOUDVISION_MCP_ALLOW_WRITES=1` on the server.

## Tests

1. Unit: op validation, apply, diff scope, CAS mismatch, preview/confirm token bind (mirror `tests/test_studio_mss_inputs.py` / campus SVI tests).
2. Fixture: capture live mainline root for `studio-telemetry-config` (redact secrets if any; token paths are paths not secrets).
3. Contract: MemberSpec registration + help text for `set_telemetry_inputs`.
4. Regression: existing MSS and Campus tests still pass after helper extract.

## Acceptance scenario (ztx)

1. `studios.inputs` `studio_id=studio-telemetry-config` → copy `inputs_sha256`.
2. `studios_write.create_workspace` `ws-mcp-ztx-terminattr-*` (or reuse pending draft).
3. `set_telemetry_inputs` operations:
   - upsert profile `staging-mgmt-ztx` with token `/tmp/cv-onboarding-token`, VRF `mgmt`, `disableAAA` true, `multi_cluster` false, addr `apiserver.cv-staging.corp.arista.io:443`
   - `set_profile_assignment` for `device:WTW23350044` → `staging-mgmt-ztx`
4. Confirm with preview token; `build`.
5. Human reviews designed TerminAttr in CV UI; copies token file on device; executes CC outside MCP.

## Open points (resolved at implement time, not blockers)

- Whether `cvopt_vrf` lives only on `dev_collection[]` vs also `sourceInterfaceAndVrf` — follow live fixture.
- Whether upsert of a brand-new profile must include a full minimal `cluster_profiles` + `dev_collection` skeleton — yes if schema requires it; derive from `campus-1709` clone in tests.

## Implementation order

1. Extract `studio_root_cas.py`; adapt MSS + Campus call sites; green tests.
2. Implement `studio_telemetry_inputs.py` + MemberSpec + tests + fixture.
3. Agent-fence allowlist + image publish + strongpod pin.
4. Live dry-run against staging on `ws-mcp-*` (no submit).
