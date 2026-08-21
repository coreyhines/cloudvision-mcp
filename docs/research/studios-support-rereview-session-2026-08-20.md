# Parallel bucket session — studios-support-re-review

**Date:** 2026-08-20  
**Integration branch:** `feat/studios-support-spec` @ post-merge (includes `bdf2715` + RR2 merge)  
**Coordinator:** Cursor  
**Spec:** `docs/studios-support-spec.md` @ `bdf2715` (synthesis apply + live fixtures)  
**Tracker:** `docs/research/studios-support-rereview-buckets.md`

### Session summary

| Bucket | Owner | Planned backend | Resolved backend | Cost pool | Model | Exec | Sub-agent ID | Branch | Commit | Tests | Status |
|--------|-------|-----------------|------------------|-----------|-------|------|--------------|--------|--------|-------|--------|
| RR1 | cursor-auto | cursor-auto | cursor-auto | cursor_included | auto | subagent | `2c6da1a1-4821-4dae-9882-c40cc2bb48a7` | — | (this session commit) | n/a | done |
| RR2 | claude-opus | claude-cli | claude-cli | claude_subscription | opus | farm | — | `feat/studios-support-rereview-bucket-RR2-claude` | `d9eb1d2` merged `eb9714c` | n/a | done |
| RR3 | cursor-named-sonnet | cursor-cli | cursor-named-sonnet | cursor_api_quota | sonnet | subagent | `740dac43-52ca-47d5-9eac-8ea0483af682` | — | (this session commit) | n/a | done |

**Auth note:** Claude Code re-authed before farm (`claude auth status` logged in). `claude -p` smoke OK. Burnbar Anthropic meter may still show RED without `burnbar login anthropic`; CLI usage via `claude_usage.py` worked (session ~5% after RR2).

### Integration health

| Check | Result |
|-------|--------|
| Combined tests | not run (docs-only) |
| Unmerged bucket branches | RR2 branch kept after merge; stale R3/R4 branches deleted |
| Worktrees active | none |
| Claude capacity at farm | GREEN session ~0% → ~5%; week all-models ~61% |
| Cursor at farm | total 6% / API 13% → 14% |
| Capacity source | burnbar_primary + claude_usage |

### Economics

| Provider | Before % | After % | Delta | Source |
|----------|----------|---------|-------|--------|
| anthropic (session / week via claude probe) | — | 5% / 61% week | RR2 opus farm | claude_usage |
| cursor (total / api) | 6% / 13% | 6% / 14% | +0 / +1 pt | burnbar |
| ollama-cloud | 100% / 65% | 100% / 65% | +0 | burnbar |
| codex | 0% | 0% | +0 | chatgpt.json |

| Metric | Value |
|--------|-------|
| Wall clock (snapshot span) | ~16 min |
| Buckets / Exec mix | 1 Claude CLI farm, 2 Cursor subagents |

### Cross-review headlines

| Bucket | Top findings |
|--------|----------------|
| RR1 | Phase 1 contracts match fixtures. Still need `get_config` `type` param + full-stream NDJSON helper before coding. Retitle stale “Still required” matrix prose. |
| RR2 | Synthesis apply was real, but turned R3 required *mechanisms* into *documented hazards*. Critical: unvalidated `post_resource_config`; mainline `workspace_id=""` not refused; root-path replace still default; proof-of-review compares immutable build record (no-op); template lint misses inputs path. |
| RR3 | New contradiction: StudioConfig keyed 404 vs search via StudioConfig/all worked example. Fixtures are analyst summaries, not literal wire bodies — weak for parser TDD. `get_cvp_studio_inputs` endpoint still unresolved. |
