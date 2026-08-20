# Parallel bucket session — studios-support-review

**Date:** 2026-08-19  
**Integration branch:** `feat/studios-support-spec` @ `7d676e2` (+ synthesis commit)  
**Coordinator:** Cursor  
**Spec:** `docs/studios-support-spec.md`  
**Tracker:** `docs/research/studios-support-review-buckets.md`

### Session summary

| Bucket | Owner | Planned backend | Resolved backend | Cost pool | Model | Exec | Sub-agent ID | Branch | Commit | Tests | Status |
|--------|-------|-----------------|------------------|-----------|-------|------|--------------|--------|--------|-------|--------|
| R1 | ollama-local | ollama-local | ollama-local | ollama_sunk | qwen3.8:27b-mlx | — | — | `feat/studios-support-review-bucket-R1-ollama` | `d88b7a0` — R1 findings | n/a | merged |
| R2 | cursor-auto | cursor-auto | cursor-auto | cursor_included | auto | subagent | `e083baf3-b624-4e1c-8771-99931c7593d9` | — | `fbdf15d` — R2+R4 | n/a | done |
| R3 | claude-opus | claude-cli | cursor-named-opus | cursor_api_quota | opus | subagent | `92a8bd60-8eba-48a3-af33-1c244bb37868` | — | `7d676e2` — R3 | n/a | done |
| R4 | codex-default | codex-cli | cursor gpt-5.6-sol | cursor_api_quota | gpt-5.6-sol | subagent | `f548cbf7-a124-4344-b1dd-32346c31827b` | — | `fbdf15d` — R2+R4 | n/a | done |
| R5 | cursor-named-sonnet | cursor-cli | cursor-named-sonnet | cursor_api_quota | sonnet | subagent | `245b6148-d5bb-452e-9e44-6004f75a5031` | — | `f27c09b` — R5 | n/a | done |
| RS | coordinator | inline | inline | cursor_included | auto | inline | — | `feat/studios-support-spec` | synthesis | n/a | done |

**Reroutes:** R3 Claude CLI failed (`--disallowedTools` ate the prompt). R4 Codex CLI 401 invalid refresh token. Both rerouted to Cursor Task with planned model families.

### Integration health

| Check | Result |
|-------|--------|
| Combined tests | not run (docs-only review) |
| Unmerged bucket branches | `feat/studios-support-review-bucket-R1-ollama` merged; leftover `*-R3-claude` / `*-R4-codex` branches from failed farms |
| Stashed WIP | none |
| Worktrees active | none expected (failed farms may have left cleanup) |
| Claude capacity at farm | unavailable (`no_oauth_token`) |
| Ollama at farm | local farm succeeded; burnbar later reported 127.0.0.1 unreachable |
| Cursor at farm | total 6% / API 10% → 13% |
| Capacity source | burnbar_primary |
| Portfolio notes | Five models: qwen3.8 local, Cursor auto, Cursor opus, gpt-5.6-sol, Cursor sonnet |

### Economics

| Provider | Before % | After % | Delta | Source |
|----------|----------|---------|-------|--------|
| anthropic | — | — | — | — |
| cursor (total / api) | 6% / 10% | 6% / 13% | +0 / +3 pt | burnbar |
| ollama-cloud (session / week) | 0% / 26% | 0% / 26% | +0 | burnbar |
| codex | 0% | 0% | +0 | chatgpt.json |

| Metric | Value |
|--------|-------|
| Cost pools active | cursor (included + API), ollama local |
| Wall clock (snapshot span) | 10.7 min |
| Buckets / Exec mix | 1 external Ollama, 4 Cursor subagents, 1 inline synthesis |
