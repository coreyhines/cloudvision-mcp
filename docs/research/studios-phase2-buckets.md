# Studios Phase 2.0 — bucket plan

Generated from: `docs/studios-phase2-spec.md`
Integration branch: `feat/studios-phase2-impl`
Coordination skill: Parallel Buckets (Cursor adapter)

## Approval status

| Field | Value |
|-------|-------|
| Status | **`approved`** |
| Approved by | user (2026-08-22; Qwen 3.8 local) |
| Approved waves | all |
| Notes | Local Ollama bucket 0: `qwen3.8:27b-mlx` (overlay `ollama_local.standard`), not probe default `qwen3.6:35b-a3b-mxfp8` |

**Do not farm or implement until status is `approved` or `approved_wave_N`.**

## Bucket sizing summary

| Metric | Value |
|--------|-------|
| Total buckets | 7 (0, 1a, 1b, 2, R1, R2, R3) |
| Parallel wave 1 | 0 ∥ 1a |
| Wave 2 | 1b |
| Wave 3 | 2 (wiring, inline) |
| Wave 4 | R1 ∥ R2 ∥ R3 |
| Estimated phases | 4 |

## Session status

| Field | Value |
|-------|-------|
| **Level** | GREEN (Cursor 9% total / 18% API; Claude session 21%; Codex 0%) |
| **Updated** | 2026-08-22 11:10 CDT |
| **Claude session / week %** | 21% / 0% (week-all 63%) |
| **Cursor total / API %** | 9% / 18% |
| **Codex week %** | 0% |
| **Ollama cloud session / week %** | 29% / **89% YELLOW** — R2 stayed on Cursor |
| **Capacity probe** | burnbar_primary |
| **Before snapshot** | `docs/research/pb-sessions/studios-phase2/before.json` |
| **After snapshot** | `docs/research/pb-sessions/studios-phase2/after.json` |
| **Execute** | Waves 1–4 complete; review edits applied on integration branch |

## Bucket registry (schedule)

| Wave | ID | Title | Profile | Anthropic | Owner | Backend | Model | Exec | Files (own) | Depends | Fits one session? |
|------|-----|-------|---------|-----------|-------|---------|-------|------|-------------|------------|-------------------|
| 1 | 0 | Write gates + preview_token | `contract` | none | ollama-local | ollama-local-cli | **qwen3.8:27b-mlx** | — | `cvp_mcp/write_access.py`, `tests/test_write_access.py` | — | yes |
| 1 | 1a | Resource POST/DELETE helper | `write_crud` | opus | claude-opus | claude-cli | opus | — | `cvp_mcp/grpc/resource_write.py`, `tests/test_resource_write.py` | — | yes |
| 2 | 1b | Workspace + description CAS | `write_crud` | sonnet | codex-default | codex-cli | gpt-5.6-sol | — | `cvp_mcp/grpc/studios_write.py`, `tests/test_studios_write.py` | 0, 1a | yes |
| 3 | 2 | Register MCP tools | `mcp_wiring` | none | cursor-auto | cursor-auto | auto | **inline** | `cloudvision_mcp.py` | 1b | yes |
| 4 | R1 | Defect-first code review | `pure_logic` | none | cursor-named-sonnet | cursor-cli | sonnet | **subagent** | review notes only | 2 | yes |
| 4 | R2 | Safety/adversarial review | `pure_logic` | none | cursor-auto | cursor-auto | auto | **subagent** | review notes only | 2 | yes |
| 4 | R3 | Contract vs spec review | `pure_logic` | opus | claude-opus | claude-cli | opus | — | review notes only | 2 | yes |

### Probe overrides

| Bucket | Probe primary | Posted owner | Why |
|-------|---------------|--------------|-----|
| R2 | ollama-cloud (`kimi-k2.7-code:cloud`) | **cursor-auto** | Ollama cloud week **89%**; Cursor included 9% |
| 0 | ollama-local default `qwen3.6:35b-a3b-mxfp8` | **qwen3.8:27b-mlx** | User: use Qwen 3.8 locally; matches overlay `ollama_local.standard`. Alt on disk: `qwen3.8:27b-mxfp8`. |

## Deliverables by bucket

### 0 — Write gates

- `writes_enabled()` / `submit_enabled()` exact `"1"`
- `SUBMIT_STALENESS_FIELD: str | None = None`
- `preview_token(tool, args)` sha256
- `assert_workspace_id` (`ws-mcp-`, not builtin-, non-empty)
- Tests: unset/`true`/`0` off; submit off when field None

**Do NOT:** HTTP, MCP tools, `cloudvision_mcp.py`

### 1a — HTTP helper

- `post_resource_config` / `delete_resource_config`
- Exact path allowlist from spec
- `request` allowlist `{REQUEST_START_BUILD, REQUEST_SUBMIT}`; SUBMIT also needs submit_enabled
- Envelope denylist `start`/`schedule` on Workspace/Studio config only
- Tests: no HTTP on bad path; Inputs `"change"` in string allowed

**Do NOT:** MCP registration, description CAS

### 1b — Studio write tools (library)

- `create_cvp_workspace` / `delete_cvp_workspace` / `build_cvp_workspace`
- `set_cvp_access_interface_description` per spec five-step RMW
- Fixture `tests/fixtures/inputs_ethernet6_720xp24_locator.json`
- Unit tests with mocked GET/POST

**Do NOT:** `@mcp.tool` in `cloudvision_mcp.py`

### 2 — Wiring (inline)

- `@mcp.tool` + `@tool_enabled` for 2.0 tools only when writes env is `"1"`
- Submit not registered

### R1–R3 — Review only

- Read-only. Findings in `docs/research/studios-phase2-review-R*.md`

## Merge order

```text
Wave 1: 0 ∥ 1a
Wave 2: 1b
Wave 3: 2 (inline)
Wave 4: R1 ∥ R2 ∥ R3
Coordinator: apply review edits
```

## Out of scope

- Submit tool registration
- `assign_cvp_studio_tags`, generic `set_cvp_studio_inputs`
- Studio create/delete
- Change Control writes
- Live POST to CVaaS (unit tests mock HTTP)

## Integration health (after execute)

| Check | Result |
|-------|--------|
| Combined pytest | `uv run pytest -q` → **283 passed** (2026-08-22 11:10 CDT) |
| Write tests | `tests/test_write_access.py` + `test_resource_write.py` + `test_studios_write.py` → **114 passed** |
| ruff | clean on owned files + `cloudvision_mcp.py` |
| black | `studios_write.py` reformatted after review edits |
| Unmerged leftover farm branches | `feat/studios-phase2-bucket-0-ollama`, `-1a-claude`, `-1b-claude` (already merged; safe to delete) |
| Worktrees | none |
| Writes in production | **still off** — do not set `CLOUDVISION_MCP_ALLOW_WRITES` on the homelab MCP host until a human asks |

### Execute attribution

| ID | Status | Commit | Notes |
|----|--------|--------|-------|
| 0 | merged | `3758f6d` | Ollama local `qwen3.8:27b-mlx` |
| 1a | merged | `610d8c0` | Claude CLI opus |
| 1b | merged | `df189f2` | Planned Codex; **rerouted** Claude opus (Codex 401) |
| 2 | merged | `14d3b87` | Cursor inline wiring |
| R1 | done | notes `docs/research/studios-phase2-review-R1.md` | Cursor named-sonnet Task |
| R2 | done | notes `docs/research/studios-phase2-review-R2.md` | Cursor auto Task (not ollama-cloud) |
| R3 | done | notes `docs/research/studios-phase2-review-R3.md` | Cursor Task (not claude-cli) |
| RS | inline | review edits on `feat/studios-phase2-impl` | fail-closed Inputs, pending CAS, bound `request_id`, DELETE param allowlist |
