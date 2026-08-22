# Studios Phase 2 follow-on — bucket plan (2.1 + 2.2 + ops)

Generated from: `docs/studios-phase2-spec.md` slices **2.1**, **2.2**, and homelab ops
Base branch: `feat/studios-phase2-impl` (PR #14)
Integration branch (execute): `feat/studios-phase2-followon`
Coordination skill: Parallel Buckets (Cursor adapter)

## Approval status

| Field | Value |
|-------|-------|
| Status | **`approved`** |
| Approved by | user (2026-08-22; follow-up `approve`) |
| Approved waves | all |
| Notes | User asked to farm **all** follow-on slices and prefer Anthropic (session ~21%, resets ~15:10 CDT / week-all 63% ~15:59 CDT). Combined “plan and go” does **not** waive this gate. |

**Do not farm or implement until status is `approved` or `approved_wave_N`.**

## Bucket sizing summary

| Metric | Value |
|--------|-------|
| Total buckets | 10 (0, 1a, 1b, 1c, 1d, W, R1, R2, R3, O) |
| Parallel wave 1 | 0 |
| Wave 2 | 1a ∥ 1b ∥ 1c ∥ 1d |
| Wave 3 | W (MCP wiring) |
| Wave 4 | R1 ∥ R2 ∥ R3 |
| Wave 5 | O (ops, coordinator) |
| Estimated phases | 5 |

## Session status

| Field | Value |
|-------|-------|
| **Level** | GREEN (Anthropic session 21%; Cursor 10% / API 18%) |
| **Updated** | 2026-08-22 14:03 CDT |
| **Claude session / week-all %** | 21% / 63% — **prefer claude-opus to burn before reset** |
| **Cursor total / API %** | 10% / 18% |
| **Codex week %** | 0% — **not used** (user: spend Anthropic) |
| **Ollama cloud week %** | **89% YELLOW** — do not farm here |
| **Capacity probe** | burnbar_primary |
| **Before snapshot** | `docs/research/pb-sessions/studios-phase2-followon/before.json` |

## Bucket registry (schedule)

| Wave | ID | Title | Profile | Anthropic | Owner | Backend | Model | Exec | Files (own) | Depends | Fits one session? |
|------|-----|-------|---------|-----------|-------|---------|-------|------|-------------|------------|-------------------|
| 1 | 0 | Allowlist 2.1/2.2 Resource paths | `contract` | opus | **claude-opus** | claude-cli | opus | — | `cvp_mcp/grpc/resource_write.py`, `tests/test_resource_write.py` | — | yes |
| 2 | 1a | AssignedTags GET + assign CAS | `write_crud` | opus | **claude-opus** | claude-cli | opus | — | `cvp_mcp/grpc/studio_tags.py`, `tests/test_studio_tags.py` | 0 | yes |
| 2 | 1b | Generic `set_cvp_studio_inputs` | `write_crud` | opus | **claude-opus** | claude-cli | opus | — | `cvp_mcp/grpc/studio_inputs_generic.py`, `tests/test_studio_inputs_generic.py` | 0 | yes |
| 2 | 1c | Submit library (unregistered) | `write_crud` | opus | **claude-opus** | claude-cli | opus | — | `cvp_mcp/grpc/workspace_submit.py`, `tests/test_workspace_submit.py` | 0 | yes |
| 2 | 1d | Studio create/delete (2.2) | `write_crud` | opus | **claude-opus** | claude-cli | opus | — | `cvp_mcp/grpc/studio_crud.py`, `tests/test_studio_crud.py` | 0 | yes |
| 3 | W | Register MCP tools | `mcp_wiring` | opus | **claude-opus** | claude-cli | opus | — | `cloudvision_mcp.py` | 1a, 1b, 1c, 1d | yes |
| 4 | R1 | Defect-first review | `pure_logic` | opus | **claude-opus** | claude-cli | opus | — | `docs/research/studios-phase2-followon-review-R1.md` | W | yes |
| 4 | R2 | Safety/adversarial review | `pure_logic` | opus | **claude-opus** | claude-cli | opus | — | `docs/research/studios-phase2-followon-review-R2.md` | W | yes |
| 4 | R3 | Contract vs spec | `pure_logic` | opus | **claude-opus** | claude-cli | opus | — | `docs/research/studios-phase2-followon-review-R3.md` | W | yes |
| 5 | O | Homelab writes on, submit off | `mcp_wiring` | none | cursor-auto | inline | auto | **inline** | deploy env / runbook only | W, #14 | yes |

### Probe overrides

| Bucket | Probe primary | Posted owner | Why |
|--------|---------------|--------------|-----|
| 0, 1a–1d, W, R1–R3 | mix (mcp_wiring → cursor-auto) | **claude-opus** | User: farm all; spend Anthropic session before reset (~15:10 CDT session / ~15:59 week-all) |
| O | — | **cursor-auto inline** | Live homelab env; do not farm `CLOUDVISION_MCP_ALLOW_WRITES` to a CLI agent |
| Codex | — | unused | User override: Anthropic, not Codex |

## Deliverables by bucket

### 0 — Path allowlist

- Add `/api/resources/studio/v1/AssignedTagsConfig` and `/api/resources/studio/v1/StudioConfig` to POST allowlist
- `REQUEST_SUBMIT` already allowlisted; still requires `submit_enabled()`
- Tests: new paths POST; still no HTTP on unknown path; `ChangeControlConfig` still forbidden

**Do NOT:** MCP tools, description CAS, live CVaaS

### 1a — Tags

- `get_cvp_studio_assigned_tags`: `GET …/AssignedTags/all`, client-filter; 404/empty → `coverage="none"`, `assigned_tags_unavailable`
- `assign_cvp_studio_tags`: require `expected_current_query`; empty query forbidden; preview_token; pending workspace
- Live-probe AssignedTags/all in unit tests with recorded fixture (no invent query)

**Do NOT:** `cloudvision_mcp.py`, submit, studio CRUD

### 1b — Generic Inputs

- `set_cvp_studio_inputs`: path required; empty `path_values` → `root_path_forbidden`
- Diff leaves; `allowed_input_keys` default `["description"]`
- Never allow `enabled`/`disabled`/`shutdown`/`vlan`/`poe`/`profile`/`mode`
- No `replace_all_inputs`

**Do NOT:** root POST for access-studio description CAS (that stays 2.0)

### 1c — Submit library

- `submit_cvp_workspace` **library only** — do **not** `@mcp.tool` it
- Gates: writes `"1"`, `CLOUDVISION_MCP_ALLOW_SUBMIT=="1"`, `SUBMIT_STALENESS_FIELD` set, confirm, allow_submit, preview_token
- Staleness: `build_id` + `object.last_modified_at`; re-GET; `BUILD_STATE_SUCCESS`; no edits after build
- Leave `SUBMIT_STALENESS_FIELD = None` so submit stays unregistered even if both env vars are 1
- Tests: unregistered when field None; empty build_id/token → `staleness_token_required`

### 1d — Studio CRUD (2.2)

- `create_cvp_studio` / `delete_cvp_studio` on pending workspace
- Refuse `immutable` / `from_package`
- Lint templates: never `shutdown` / `no shutdown` / `no interface` / `reload` / `write erase`
- No ChangeControlConfig; no `allow_disruptive`

### W — Wiring

- Register **2.1** tools except submit: assigned-tags read (always, even if writes off), assign + generic inputs only if writes `"1"`
- Register **2.2** create/delete studio only if writes `"1"`
- **Do not** register submit

### R1–R3 — Review

Read-only. Notes under `docs/research/studios-phase2-followon-review-R*.md`.

### O — Ops (after code merge)

- Homelab: set `CLOUDVISION_MCP_ALLOW_WRITES=1` on **container** env (strongpod `/opt/containerdata/cloudvision-mcp/environment`), restart app unit
- Keep `CLOUDVISION_MCP_ALLOW_SUBMIT` unset
- Use container token (398), not workstation `~/.env`
- Do not merge to main from this farm

## Merge order

```text
Wave 1: 0
Wave 2: 1a ∥ 1b ∥ 1c ∥ 1d  (worktrees)
Wave 3: W
Wave 4: R1 ∥ R2 ∥ R3
Wave 5: O (inline, after user confirms homelab)
Coordinator: apply review edits
```

## Out of scope

- ChangeControlConfig
- Configlet CRUD
- One-shot “do the whole flow”
- Port shutdown
- Submit MCP tool registration
- Merging PR #14 (human)

## Integration health (after execute)

pytest + ruff on owned files; `uv run pytest tests/test_resource_write.py tests/test_studio_tags.py tests/test_studio_inputs_generic.py tests/test_workspace_submit.py tests/test_studio_crud.py tests/test_studios_write.py tests/test_studios.py -q`
