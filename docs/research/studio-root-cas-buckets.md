# Studio root-CAS + Telemetry writer — bucket plan

Generated from: `docs/superpowers/specs/2026-11-23-studio-root-cas-design.md`
Implementation plan: `docs/superpowers/plans/2026-11-23-studio-root-cas.md`
Integration branch: `feat/studio-root-cas`
Coordination skill: parallel-buckets

## Approval status

| Field | Value |
|-------|-------|
| Status | **`approved`** |
| Approved by | user |
| Approved waves | all (2026-11-23) |
| Notes | User routing: Claude + Codex + Cursor auto; no mtplx-local; no ollama-cloud. |

**Do not farm or implement until status is `approved` or `approved_wave_N`.**

## Bucket sizing summary

| Metric | Value |
|--------|-------|
| Total buckets | 7 |
| Parallel wave 1 | SRC0 alone |
| Parallel wave 2 | SRC1 (Claude) ∥ SRC2 (Codex), then SRC3 (Claude opus) |
| Parallel wave 3 | SRC4 ∥ SRC5 |
| Parallel wave 4 | SRC6 |
| Estimated phases | 4 waves |

## Session status

| Field | Value |
|-------|-------|
| **Level** | GREEN |
| **Updated** | 2026-11-23 |
| **Claude session / week %** | 5% / 7% |
| **Cursor total / API %** | 36% / 37% |
| **Codex / ChatGPT Plus week %** | 0% session / 7% week |
| **Capacity probe** | burnbar |
| **Before snapshot** | `docs/research/pb-sessions/studio-root-cas/before.json` |
| **Note** | Session GREEN — wide open |

## Bucket registry (schedule)

| Wave | ID | Title | Profile | Anthropic | Owner | Backend | Model | Exec | Files (own) | Depends on | Fits one session? |
|------|-----|-------|---------|-----------|-------|---------|-------|------|-------------|------------|-------------------|
| 1 | SRC0 | Shared `RootCasConfig` + `run_root_inputs_cas` + unit tests | `contract`/`write_crud` | sonnet | `claude-sonnet` | `claude-cli` | `sonnet` | — | `cvp_mcp/grpc/studio_root_cas.py`, `tests/test_studio_root_cas.py` | — | yes |
| 2 | SRC1 | MSS thin adapter onto root CAS | `write_crud` | sonnet | `claude-sonnet` | `claude-cli` | `sonnet` | — | `cvp_mcp/grpc/studio_mss_inputs.py` | SRC0 | yes |
| 2 | SRC2 | Campus SVI thin adapter onto root CAS | `write_crud` | none | `codex-default` | `codex-cli` | `gpt-6-sol` | — | `cvp_mcp/grpc/studio_campus_svi_inputs.py` | SRC0 | yes |
| 2 | SRC3 | Telemetry ops + fixture + CAS writer | `write_crud` | opus | `claude-opus` | `claude-cli` | `opus` | — | `cvp_mcp/grpc/studio_telemetry_inputs.py`, `tests/test_studio_telemetry_inputs.py`, `tests/fixtures/inputs_telemetry_config_root_minimal.json` | SRC0 | yes |
| 3 | SRC4 | MCP `set_telemetry_inputs` registration | `mcp_wiring` | none | `cursor-auto` | `inline` | `auto` | **inline** | `cvp_mcp/members/studios_write.py`, `cvp_mcp/tool_groups.py`, `tests/test_grouped_tool.py` | SRC3 | yes |
| 3 | SRC5 | Agent-fence allowlist `set_telemetry_inputs` | `write_crud` | none | `cursor-auto` | `cursor-cli` | `auto` | **subagent** | `agent-fence/gateway/policy/cloudvision.yaml` (+ rendered config if required) | SRC3 | yes |
| 4 | SRC6 | Live dry-run on staging (no submit) | `live_mcp` | none | `cursor-auto` | `inline` | `auto` | **inline** | _(no product files; MCP calls only)_ | SRC4, SRC5 + image deploy | yes |

**Probe overrides / notes (user-directed):**

- **Providers in play:** Claude Code CLI, Codex CLI (ChatGPT Plus), Cursor (`auto` only).
- **Banned this feature:** `mtplx-local`, `ollama-cloud` (subscription canceled), Cursor named sonnet/opus.
- **Claude** owns Anthropic sonnet/opus-tier slices (SRC0, SRC1, SRC3).
- **Codex** owns Campus adapter (SRC2) — cross-provider pack with Claude.
- **Cursor `auto`** owns MCP wiring + fence + live verify (SRC4–SRC6).
- Wave 2: start **SRC1 ∥ SRC2** same turn (claude ∥ codex). **SRC3** after SRC1 (serialize two Claude farms) or after both SRC1+SRC2 merge — prefer after SRC1+SRC2 so root+adapters exist before telemetry.
- Image pin bump remains part of SRC6 deploy notes.

## Merge order

```text
SRC0 → (SRC1 ∥ SRC2) → SRC3 → (SRC4 ∥ SRC5) → SRC6
```

Wave 2 packs **Claude ∥ Codex** (SRC1 ∥ SRC2). SRC3 (Claude opus) runs after those merge so only one Claude farm at a time after SRC0.

Integration branch: `feat/studio-root-cas`
Bucket branches: `feat/studio-root-cas-bucket-<id>`

## File ownership map

| File | Bucket |
|------|--------|
| `cvp_mcp/grpc/studio_root_cas.py` | SRC0 |
| `tests/test_studio_root_cas.py` | SRC0 |
| `cvp_mcp/grpc/studio_mss_inputs.py` | SRC1 |
| `cvp_mcp/grpc/studio_campus_svi_inputs.py` | SRC2 |
| `cvp_mcp/grpc/studio_telemetry_inputs.py` | SRC3 |
| `tests/test_studio_telemetry_inputs.py` | SRC3 |
| `tests/fixtures/inputs_telemetry_config_root_minimal.json` | SRC3 |
| `cvp_mcp/members/studios_write.py` | SRC4 |
| `cvp_mcp/tool_groups.py` | SRC4 |
| `tests/test_grouped_tool.py` | SRC4 |
| `agent-fence/gateway/policy/cloudvision.yaml` | SRC5 |

## Out of scope

- `submit_cvp_workspace` / Change Control execute
- Loosening generic `set_inputs`
- Full-document replace on telemetry (`document_ok`)
- Rewriting MSS/Campus public MCP contracts
- Device-side token copy / TerminAttr bounce
- `ollama-cloud` farms (subscription canceled)
- `mtplx-local` farms (user ban for this feature)

## Do NOT (all buckets)

- Edit files outside ownership
- Submit CVP workspaces
- Touch Arista switchports
- Commit unless human asks (plan says so; farm prompts may still prepare commits)

## References

- Spec: `docs/superpowers/specs/2026-11-23-studio-root-cas-design.md`
- Plan: `docs/superpowers/plans/2026-11-23-studio-root-cas.md`
- Capacity before: `docs/research/pb-sessions/studio-root-cas/before.json`
