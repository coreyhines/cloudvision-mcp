# Studios support spec — re-review after synthesis apply (bucketize)

Generated from: `docs/studios-support-spec.md` @ `bdf2715`
Fixtures: `tests/fixtures/designed_config_sources_720xp24.json`,
`tests/fixtures/workspace_build_enums.json`
Prior synthesis: `docs/research/studios-support-review-synthesis.md`
Integration branch: `feat/studios-support-spec` _(findings only; no product-code edits)_
Coordination skill: Parallel Buckets (Cursor adapter)

## Approval status

| Field | Value |
|-------|-------|
| Status | **`approved`** (scope: all, granted: 2026-08-21T03:49Z) |
| Approved by | user (Approve schedule) |
| Approved waves | Wave 1 (RR1 ∥ RR2 ∥ RR3) |
| Notes | Three-persona re-review. RR2 on Claude Code CLI opus after re-auth. |

**Do not farm or dispatch until status is `approved` or `approved_wave_N`.**

## Bucket sizing summary

| Metric | Value |
|--------|-------|
| Total buckets | 3 (RR1–RR3) |
| Parallel wave 1 | RR1 ∥ RR2 ∥ RR3 |
| Wave 2 | none (coordinator may fold findings into chat; no separate synthesis bucket unless requested) |
| Cursor buckets | 3 (recommended — prior CLI farms failed; ollama-cloud session 100%; morpheus DNS fails) |
| Estimated phases | 1 |

## Session status (update each wave)

| Field | Value |
|-------|-------|
| **Level** | **GREEN** (Claude farm succeeded; Cursor GREEN) |
| **Updated** | 2026-08-20 22:58 CDT |
| **Claude session / week %** | session ~5% after RR2 · week all-models ~61% |
| **Cursor total / API %** | 6% / 14% after |
| **Codex / ChatGPT Plus week %** | 0% |
| **Ollama Cloud** | session 100% (unused) |
| **Capacity probe** | `burnbar_primary` |
| **Before snapshot** | `docs/research/pb-sessions/studios-support-re-review/before.json` |
| **After snapshot** | `docs/research/pb-sessions/studios-support-re-review/after.json` |
| **Session report** | `docs/research/studios-support-rereview-session-2026-08-20.md` |

## Bucket registry (schedule)

| Wave | ID | Title | Profile | Anthropic | Owner | Backend | Model | Exec | Files (own) | Depends on | Fits one session? |
|------|-----|-------|---------|-----------|-------|---------|-------|------|-------------|------------|-------------------|
| 1 | RR1 | Phase 1 contracts vs live fixtures + code helpers | `read_tools` | none | cursor-auto | cursor-auto | auto | **subagent** | `docs/research/studios-support-rereview-RR1.md` | — | yes |
| 1 | RR2 | Phase 2 residual safety after synthesis apply | `pure_logic` | **opus** | claude-opus | **claude-cli** | opus | — | `docs/research/studios-support-rereview-RR2.md` | — | yes |
| 1 | RR3 | Spec consistency, contradictions, implementability | `pure_logic` | none | cursor-named-sonnet | cursor-cli | sonnet | **subagent** | `docs/research/studios-support-rereview-RR3.md` | — | yes |

**Probe overrides**

- RR2: **Claude Code CLI opus** (subscription). Verified `claude auth status` logged in + `claude -p` smoke OK. Week ~61% all-models — fine for one review bucket.
- RR1/RR3: Cursor (auto / sonnet). Ollama cloud session exhausted; workstation DNS still fails.
- Note: do **not** farm Claude with `--bare` (skips keychain). Prior R3 farm also broke on `--disallowedTools` eating the prompt — use the fixed `farm_claude_bucket.sh` path carefully.

## Merge order

```text
(RR1 ∥ RR2 ∥ RR3) → optional coordinator fold into chat / short note
```

No product-code merge. Findings files are independent.

## File ownership map

| File | Bucket | Notes |
|------|--------|-------|
| `docs/studios-support-spec.md` | all **read** | no writes |
| `tests/fixtures/designed_config_sources_720xp24.json` | RR1 **read** | fixture fidelity |
| `tests/fixtures/workspace_build_enums.json` | RR1 **read** | enum/poll fidelity |
| `cvp_mcp/grpc/config_async_flow.py` | RR1 **read** | GetConfig still hardcodes RUNNING |
| `cvp_mcp/tool_access.py` | RR2/RR3 **read** | write-gate reality |
| `docs/research/studios-support-rereview-RR*.md` | each bucket | findings only |

## Persona briefs

### RR1 — Phase 1 contracts vs fixtures
Does the revised Phase 1 match live captures? Array GetConfig parse, string `source.key`, mainline `""`, Studio vs StudioConfig 404, poll `request_id`≈`buildId`, NDJSON helper ban. Flag any remaining “TBD / confirm with live GET” that is already answered.

### RR2 — Phase 2 residual safety
After synthesis: CC tools out, ALLOW_SUBMIT, replace semantics, proof-of-review, no FORCE/start, builtin on all writes. What holes remain? Would an implementer still ship something unsafe?

### RR3 — Consistency / implementability
Internal contradictions, inventory vs body, open questions vs resolved facts, missing tests, gaps that would block a Phase 1 coding PR.

## Out of scope

- Implementing Phase 1/2 MCP tools
- Live CVP writes / redeploy
- Editing the spec unless a follow-up ask applies RR findings

## Session reports

| Date | Chat posted | File |
|------|-------------|------|
| 2026-08-20 | yes (execute) | `docs/research/studios-support-rereview-session-2026-08-20.md` |
